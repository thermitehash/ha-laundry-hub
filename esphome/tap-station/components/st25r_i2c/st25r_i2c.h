#pragma once

#include "esphome/components/i2c/i2c.h"
#include "esphome/components/st25r/st25r.h"
#include "esphome/core/component.h"
#include "st25r_i2c_proto.h"

namespace esphome::st25r_i2c {

/// I2C transport for ESPHome's st25r reader (tag logic stays upstream).
///
/// Error policy: any I2C error (NACK, timeout, ...) during a scan aborts it, marks
/// the chip as not ready and schedules a re-init with exponential backoff
/// (1 s doubling up to max_backoff). The component is never marked failed, so a
/// stuck or unplugged reader cannot put the device into a reboot loop and is
/// picked up again automatically once it answers.
class ST25RI2C : public st25r::ST25R, public i2c::I2CDevice {
 public:
  void setup() override;
  void update() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::DATA; }

  void set_max_backoff(uint32_t ms) { this->recovery_.set_max(ms); }
  bool is_chip_ready() const { return this->chip_ready_; }

 protected:
  uint8_t read_register(uint8_t reg) override;
  void write_register(uint8_t reg, uint8_t value) override;
  void write_command(uint8_t command) override;
  void write_fifo(const uint8_t *data, size_t len) override;
  void read_fifo(uint8_t *data, size_t len) override;

  void try_init_();
  void handle_bus_error_();
  void note_(i2c::ErrorCode err, uint8_t mode);

  static constexpr size_t MAX_FIFO_WRITE = 64;  // upstream writes at most 7 bytes

  st25r_i2c_proto::RecoveryPolicy recovery_{1000, 60000};
  i2c::ErrorCode last_error_{i2c::ERROR_OK};
  uint8_t last_error_mode_{0};
  uint32_t error_count_{0};
  bool bus_error_{false};
  bool chip_ready_{false};
};

}  // namespace esphome::st25r_i2c
