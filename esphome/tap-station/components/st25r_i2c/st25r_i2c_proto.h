// ST25R3916 / ST25R3916B I2C framing helpers (pure C++, no ESPHome deps).
//
// Datasheet (DS12484, "Serial peripheral interface / I2C"): every transaction
// starts with a mode byte after the device address. The I2C framing uses the
// same mode bytes as SPI:
//   00AAAAAA  register write (A = address 0x00..0x3F), data bytes follow
//   01AAAAAA  register read, then repeated START + read
//   10000000  FIFO load (0x80), data bytes follow
//   10011111  FIFO read (0x9F), then repeated START + read
//   11CCCCCC  direct command (0xC0..0xFF, except 0xFB)
//   11111011  0xFB: Space-B prefix, followed by a register read/write mode byte
// The upstream ESPHome st25r component marks Space-B registers with bit 0x40
// (e.g. 0x45 = Space-B 0x05); that convention is kept here.
//
// Host-tested in esphome/tap-station/tests/ (g++ -Wall -Wextra -Werror).
#pragma once

#include <cstddef>
#include <cstdint>

namespace st25r_i2c_proto {

constexpr uint8_t I2C_ADDRESS = 0x50;
constexpr uint8_t MODE_REG_WRITE = 0x00;
constexpr uint8_t MODE_REG_READ = 0x40;
constexpr uint8_t MODE_FIFO_LOAD = 0x80;
constexpr uint8_t MODE_FIFO_READ = 0x9F;
constexpr uint8_t MODE_SPACE_B = 0xFB;
constexpr uint8_t MODE_DIRECT_CMD_MIN = 0xC0;
constexpr uint8_t ADDRESS_MASK = 0x3F;
constexpr uint8_t SPACE_B_FLAG = 0x40;
constexpr uint8_t MAX_REGISTER = 0x7F;  // 0x00..0x3F space A, 0x40..0x7F space B
constexpr size_t FIFO_SIZE = 512;
constexpr size_t MAX_HEADER_LEN = 2;

/// True if `reg` (upstream encoding) lives in register space B.
constexpr bool is_space_b(uint8_t reg) { return (reg & SPACE_B_FLAG) != 0; }

/// True if `reg` is a valid register in the upstream encoding.
constexpr bool valid_register(uint8_t reg) { return reg <= MAX_REGISTER; }

/// True for a valid direct command byte (0xC0..0xFF, 0xFB is the Space-B prefix).
constexpr bool valid_direct_command(uint8_t cmd) { return cmd >= MODE_DIRECT_CMD_MIN && cmd != MODE_SPACE_B; }

/// Encode the header of a register read into out[0..1]. Returns its length (1-2), 0 if invalid.
inline size_t encode_register_read(uint8_t reg, uint8_t *out) {
  if (!valid_register(reg) || out == nullptr)
    return 0;
  size_t n = 0;
  if (is_space_b(reg))
    out[n++] = MODE_SPACE_B;
  out[n++] = static_cast<uint8_t>(MODE_REG_READ | (reg & ADDRESS_MASK));
  return n;
}

/// Encode a complete single register write into out[0..2]. Returns its length (2-3), 0 if invalid.
inline size_t encode_register_write(uint8_t reg, uint8_t value, uint8_t *out) {
  if (!valid_register(reg) || out == nullptr)
    return 0;
  size_t n = 0;
  if (is_space_b(reg))
    out[n++] = MODE_SPACE_B;
  out[n++] = static_cast<uint8_t>(MODE_REG_WRITE | (reg & ADDRESS_MASK));
  out[n++] = value;
  return n;
}

/// Encode a direct command into out[0]. Returns 1, or 0 if `cmd` is not a direct command.
inline size_t encode_direct_command(uint8_t cmd, uint8_t *out) {
  if (!valid_direct_command(cmd) || out == nullptr)
    return 0;
  out[0] = cmd;
  return 1;
}

/// Encode a FIFO load (0x80 + data) into out. Returns total length, 0 if invalid or too big.
inline size_t encode_fifo_load(const uint8_t *data, size_t len, uint8_t *out, size_t cap) {
  if (data == nullptr || out == nullptr || len == 0 || len > FIFO_SIZE || cap < len + 1)
    return 0;
  out[0] = MODE_FIFO_LOAD;
  for (size_t i = 0; i < len; i++)
    out[i + 1] = data[i];
  return len + 1;
}

/// FIFO read header byte (followed by repeated START + read of N bytes).
constexpr uint8_t fifo_read_header() { return MODE_FIFO_READ; }

/// Decode a mode byte (for logging/tests). Returns a short static name.
inline const char *describe_mode(uint8_t mode) {
  if (mode == MODE_SPACE_B)
    return "space_b";
  if (mode == MODE_FIFO_LOAD)
    return "fifo_load";
  if (mode == MODE_FIFO_READ)
    return "fifo_read";
  switch (mode & 0xC0) {
    case MODE_REG_WRITE:
      return "reg_write";
    case MODE_REG_READ:
      return "reg_read";
    case 0xC0:
      return "direct_cmd";
    default:
      return "other";  // 0x80..0xBF except FIFO load/read: PT memory ops (unused)
  }
}

/// Format a UID as uppercase hex without separators. Returns the number of
/// characters written (excluding NUL), or 0 if the UID is empty/too long or cap is too small.
inline size_t format_uid_hex(const uint8_t *uid, size_t len, char *out, size_t cap) {
  static const char HEX[] = "0123456789ABCDEF";
  if (uid == nullptr || out == nullptr || len == 0 || len > 10 || cap < len * 2 + 1)
    return 0;
  for (size_t i = 0; i < len; i++) {
    out[i * 2] = HEX[uid[i] >> 4];
    out[i * 2 + 1] = HEX[uid[i] & 0x0F];
  }
  out[len * 2] = '\0';
  return len * 2;
}

/// Exponential backoff for chip recovery: base * 2^(failures-1), capped at max.
constexpr uint32_t backoff_delay_ms(uint8_t failures, uint32_t base_ms, uint32_t max_ms) {
  if (failures == 0)
    return 0;
  uint32_t delay = base_ms;
  for (uint8_t i = 1; i < failures && delay < max_ms; i++)
    delay = (delay > max_ms / 2) ? max_ms : delay * 2;
  return delay < max_ms ? delay : max_ms;
}

/// Tracks reader failures and when the next re-init may run. millis() wrap-safe.
class RecoveryPolicy {
 public:
  constexpr RecoveryPolicy(uint32_t base_ms, uint32_t max_ms) : base_ms_(base_ms), max_ms_(max_ms) {}

  void set_max(uint32_t max_ms) { this->max_ms_ = max_ms < this->base_ms_ ? this->base_ms_ : max_ms; }

  void on_success() {
    this->failures_ = 0;
    this->next_attempt_ = 0;
  }

  void on_failure(uint32_t now) {
    if (this->failures_ < 255)
      this->failures_++;
    this->next_attempt_ = now + this->current_delay();
  }

  /// True while a re-init attempt must wait.
  bool in_backoff(uint32_t now) const {
    return this->failures_ != 0 && static_cast<int32_t>(now - this->next_attempt_) < 0;
  }

  uint8_t failures() const { return this->failures_; }
  uint32_t current_delay() const { return backoff_delay_ms(this->failures_, this->base_ms_, this->max_ms_); }

 private:
  uint32_t base_ms_;
  uint32_t max_ms_;
  uint32_t next_attempt_{0};
  uint8_t failures_{0};
};

}  // namespace st25r_i2c_proto
