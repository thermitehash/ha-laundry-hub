#include "st25r_i2c.h"

#include "esphome/core/hal.h"
#include "esphome/core/log.h"

namespace esphome::st25r_i2c {

static const char *const TAG = "st25r_i2c";
namespace proto = st25r_i2c_proto;

void ST25RI2C::setup() {
  // Mirrors st25r::ST25R::setup() but never calls mark_failed(): a reader that
  // does not answer at boot is retried with backoff instead.
  if (this->reset_pin_ != nullptr) {
    this->reset_pin_->setup();
    this->pulse_reset_();
  }
  if (this->irq_pin_ != nullptr) {
    this->irq_pin_->setup();
    this->irq_pin_->attach_interrupt(st25r::ST25R::isr, static_cast<st25r::ST25R *>(this), gpio::INTERRUPT_RISING_EDGE);
  }
  this->try_init_();
}

void ST25RI2C::try_init_() {
  this->bus_error_ = false;
  bool ok = this->reset_chip_();
  if (ok && !this->bus_error_) {
    if (this->recovery_.failures() != 0)
      ESP_LOGI(TAG, "Reader recovered after %u failed attempt(s)", this->recovery_.failures());
    this->recovery_.on_success();
    this->chip_ready_ = true;
    this->status_clear_warning();
    return;
  }
  this->chip_ready_ = false;
  this->recovery_.on_failure(millis());
  this->status_set_warning(LOG_STR("NFC reader not responding"));
  ESP_LOGW(TAG, "Reader init failed (attempt %u, last I2C error %d during %s); retry in %u ms",
           this->recovery_.failures(), static_cast<int>(this->last_error_),
           proto::describe_mode(this->last_error_mode_), static_cast<unsigned>(this->recovery_.current_delay()));
}

void ST25RI2C::update() {
  if (!this->chip_ready_) {
    if (!this->recovery_.in_backoff(millis()))
      this->try_init_();
    return;
  }
  this->bus_error_ = false;
  st25r::ST25R::update();
  if (this->bus_error_)
    this->handle_bus_error_();
}

void ST25RI2C::loop() {
  if (!this->chip_ready_) {
    this->disable_loop();
    return;
  }
  st25r::ST25R::loop();
  if (this->bus_error_)
    this->handle_bus_error_();
}

void ST25RI2C::handle_bus_error_() {
  // Abort the scan; the upstream state machine only starts a new scan from IDLE.
  this->state_ = STATE_IDLE;
  this->irq_status_ = 0;
  this->chip_ready_ = false;
  this->bus_error_ = false;
  this->recovery_.on_failure(millis());
  this->status_set_warning(LOG_STR("NFC reader I2C error"));
  ESP_LOGW(TAG, "I2C error %d during %s; re-init in %u ms", static_cast<int>(this->last_error_),
           proto::describe_mode(this->last_error_mode_), static_cast<unsigned>(this->recovery_.current_delay()));
  this->disable_loop();
}

void ST25RI2C::note_(i2c::ErrorCode err, uint8_t mode) {
  if (err == i2c::ERROR_OK)
    return;
  this->bus_error_ = true;
  this->last_error_ = err;
  this->last_error_mode_ = mode;
  this->error_count_++;
}

uint8_t ST25RI2C::read_register(uint8_t reg) {
  uint8_t header[proto::MAX_HEADER_LEN];
  size_t n = proto::encode_register_read(reg, header);
  if (n == 0) {
    this->note_(i2c::ERROR_INVALID_ARGUMENT, proto::MODE_REG_READ);
    return 0;
  }
  uint8_t value = 0;
  auto err = this->write_read(header, n, &value, 1);
  this->note_(err, header[n - 1]);
  return err == i2c::ERROR_OK ? value : 0;
}

void ST25RI2C::write_register(uint8_t reg, uint8_t value) {
  uint8_t frame[proto::MAX_HEADER_LEN + 1];
  size_t n = proto::encode_register_write(reg, value, frame);
  if (n == 0) {
    this->note_(i2c::ERROR_INVALID_ARGUMENT, proto::MODE_REG_WRITE);
    return;
  }
  this->note_(this->write(frame, n), frame[n - 2]);
}

void ST25RI2C::write_command(uint8_t command) {
  uint8_t frame[1];
  if (proto::encode_direct_command(command, frame) == 0) {
    this->note_(i2c::ERROR_INVALID_ARGUMENT, command);
    return;
  }
  this->note_(this->write(frame, 1), command);
}

void ST25RI2C::write_fifo(const uint8_t *data, size_t len) {
  uint8_t frame[MAX_FIFO_WRITE + 1];
  size_t n = proto::encode_fifo_load(data, len, frame, sizeof(frame));
  if (n == 0) {
    this->note_(i2c::ERROR_TOO_LARGE, proto::MODE_FIFO_LOAD);
    return;
  }
  this->note_(this->write(frame, n), proto::MODE_FIFO_LOAD);
}

void ST25RI2C::read_fifo(uint8_t *data, size_t len) {
  if (len == 0)
    return;
  const uint8_t header = proto::fifo_read_header();
  auto err = this->write_read(&header, 1, data, len);
  if (err != i2c::ERROR_OK) {
    for (size_t i = 0; i < len; i++)
      data[i] = 0;  // never hand stale bytes to the anticollision logic
  }
  this->note_(err, header);
}

void ST25RI2C::dump_config() {
  st25r::ST25R::dump_config();
  LOG_I2C_DEVICE(this);
  ESP_LOGCONFIG(TAG,
                "  Transport: I2C\n"
                "  Chip ready: %s\n"
                "  I2C errors since boot: %u",
                YESNO(this->chip_ready_), static_cast<unsigned>(this->error_count_));
}

}  // namespace esphome::st25r_i2c
