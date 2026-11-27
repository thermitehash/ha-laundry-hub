// Host tests for st25r_i2c_proto.h. Build: make -C esphome/tap-station/tests
#include "../components/st25r_i2c/st25r_i2c_proto.h"

#include <cstdio>
#include <cstring>

namespace p = st25r_i2c_proto;

static int failures = 0;
static int checks = 0;
#define CHECK(cond)                                                     \
  do {                                                                  \
    checks++;                                                           \
    if (!(cond)) {                                                      \
      failures++;                                                       \
      std::fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
    }                                                                   \
  } while (0)

static void test_register_read() {
  uint8_t out[2] = {0, 0};
  CHECK(p::encode_register_read(0x3F, out) == 1);  // IC identity
  CHECK(out[0] == 0x7F);
  CHECK(p::encode_register_read(0x00, out) == 1);
  CHECK(out[0] == 0x40);
  CHECK(p::encode_register_read(0x1A, out) == 1);  // IRQ main
  CHECK(out[0] == 0x5A);
  // Space B: 0x45 (EMD sup conf) -> 0xFB, 0x40|0x05
  CHECK(p::encode_register_read(0x45, out) == 2);
  CHECK(out[0] == 0xFB && out[1] == 0x45);
  CHECK(p::encode_register_read(0x7F, out) == 2);
  CHECK(out[0] == 0xFB && out[1] == 0x7F);
  CHECK(p::encode_register_read(0x80, out) == 0);
  CHECK(p::encode_register_read(0xFF, out) == 0);
  CHECK(p::encode_register_read(0x01, nullptr) == 0);
}

static void test_register_write() {
  uint8_t out[3] = {0, 0, 0};
  CHECK(p::encode_register_write(0x02, 0xC8, out) == 2);  // OP_CONTROL
  CHECK(out[0] == 0x02 && out[1] == 0xC8);
  CHECK(p::encode_register_write(0x3F, 0x00, out) == 2);
  CHECK(out[0] == 0x3F);
  CHECK(p::encode_register_write(0x68, 0x10, out) == 3);  // AUX_MOD, space B 0x28
  CHECK(out[0] == 0xFB && out[1] == 0x28 && out[2] == 0x10);
  CHECK(p::encode_register_write(0x4C, 0x51, out) == 3);  // CORR_CONF1, space B 0x0C
  CHECK(out[0] == 0xFB && out[1] == 0x0C && out[2] == 0x51);
  CHECK(p::encode_register_write(0x80, 1, out) == 0);
  CHECK(p::encode_register_write(0x01, 1, nullptr) == 0);
  // Write mode byte never has the read bit set.
  for (int r = 0; r <= 0x7F; r++) {
    uint8_t f[3];
    size_t n = p::encode_register_write(static_cast<uint8_t>(r), 0xAA, f);
    CHECK((f[n - 2] & 0xC0) == 0x00);
    CHECK(f[n - 1] == 0xAA);
    CHECK(n == (r & 0x40 ? 3u : 2u));
  }
}

static void test_direct_commands() {
  uint8_t out[1] = {0};
  const uint8_t valid[] = {0xC1, 0xC3, 0xC4, 0xC5, 0xC7, 0xC8, 0xD5, 0xD6, 0xDF, 0xC0, 0xFF};
  for (uint8_t c : valid) {
    CHECK(p::encode_direct_command(c, out) == 1);
    CHECK(out[0] == c);
  }
  CHECK(p::encode_direct_command(0xFB, out) == 0);  // Space-B prefix, not a command
  CHECK(p::encode_direct_command(0xBF, out) == 0);
  CHECK(p::encode_direct_command(0x00, out) == 0);
  CHECK(p::encode_direct_command(0xC1, nullptr) == 0);
  CHECK(p::valid_direct_command(0xC0));
  CHECK(!p::valid_direct_command(0x9F));
}

static void test_fifo() {
  const uint8_t data[] = {0x93, 0x20};
  uint8_t out[8] = {};
  CHECK(p::encode_fifo_load(data, 2, out, sizeof(out)) == 3);
  CHECK(out[0] == 0x80 && out[1] == 0x93 && out[2] == 0x20);
  CHECK(p::encode_fifo_load(data, 2, out, 2) == 0);   // cap too small
  CHECK(p::encode_fifo_load(data, 2, out, 3) == 3);   // exact fit
  CHECK(p::encode_fifo_load(data, 0, out, 8) == 0);   // empty
  CHECK(p::encode_fifo_load(nullptr, 2, out, 8) == 0);
  CHECK(p::encode_fifo_load(data, 2, nullptr, 8) == 0);
  static uint8_t big[p::FIFO_SIZE + 2];
  static uint8_t big_out[p::FIFO_SIZE + 3];
  CHECK(p::encode_fifo_load(big, p::FIFO_SIZE, big_out, sizeof(big_out)) == p::FIFO_SIZE + 1);
  CHECK(p::encode_fifo_load(big, p::FIFO_SIZE + 1, big_out, sizeof(big_out)) == 0);
  CHECK(p::fifo_read_header() == 0x9F);
}

static void test_describe() {
  CHECK(std::strcmp(p::describe_mode(0xFB), "space_b") == 0);
  CHECK(std::strcmp(p::describe_mode(0x80), "fifo_load") == 0);
  CHECK(std::strcmp(p::describe_mode(0x9F), "fifo_read") == 0);
  CHECK(std::strcmp(p::describe_mode(0x02), "reg_write") == 0);
  CHECK(std::strcmp(p::describe_mode(0x7F), "reg_read") == 0);
  CHECK(std::strcmp(p::describe_mode(0xC1), "direct_cmd") == 0);
  CHECK(std::strcmp(p::describe_mode(0xA0), "other") == 0);
}

static void test_uid_format() {
  const uint8_t uid7[] = {0x04, 0xA1, 0xB2, 0xC3, 0xD4, 0xE5, 0xF6};
  char out[21];
  CHECK(p::format_uid_hex(uid7, 7, out, sizeof(out)) == 14);
  CHECK(std::strcmp(out, "04A1B2C3D4E5F6") == 0);
  const uint8_t uid4[] = {0xde, 0xad, 0xbe, 0xef};
  CHECK(p::format_uid_hex(uid4, 4, out, 9) == 8);
  CHECK(std::strcmp(out, "DEADBEEF") == 0);
  CHECK(p::format_uid_hex(uid4, 4, out, 8) == 0);  // no room for NUL
  CHECK(p::format_uid_hex(uid4, 0, out, 9) == 0);
  CHECK(p::format_uid_hex(uid4, 11, out, sizeof(out)) == 0);
  CHECK(p::format_uid_hex(nullptr, 4, out, 9) == 0);
}

static void test_backoff() {
  CHECK(p::backoff_delay_ms(0, 1000, 60000) == 0);
  CHECK(p::backoff_delay_ms(1, 1000, 60000) == 1000);
  CHECK(p::backoff_delay_ms(2, 1000, 60000) == 2000);
  CHECK(p::backoff_delay_ms(6, 1000, 60000) == 32000);
  CHECK(p::backoff_delay_ms(7, 1000, 60000) == 60000);
  CHECK(p::backoff_delay_ms(255, 1000, 60000) == 60000);
  CHECK(p::backoff_delay_ms(3, 1000, 1500) == 1500);
  CHECK(p::backoff_delay_ms(255, 0xFFFFFFF0u, 0xFFFFFFFFu) == 0xFFFFFFFFu);  // no overflow
}

static void test_recovery_policy() {
  p::RecoveryPolicy r(1000, 8000);
  CHECK(!r.in_backoff(0));
  CHECK(r.failures() == 0);
  r.on_failure(100);
  CHECK(r.failures() == 1);
  CHECK(r.in_backoff(100));
  CHECK(r.in_backoff(1099));
  CHECK(!r.in_backoff(1100));
  r.on_failure(1100);  // 2s
  CHECK(r.in_backoff(3099));
  CHECK(!r.in_backoff(3100));
  for (int i = 0; i < 300; i++)
    r.on_failure(5000);  // saturates, no wrap of the counter
  CHECK(r.failures() == 255);
  CHECK(r.current_delay() == 8000);
  r.on_success();
  CHECK(r.failures() == 0);
  CHECK(!r.in_backoff(5000));

  // millis() wraparound: failure just before the wrap.
  p::RecoveryPolicy w(1000, 60000);
  w.on_failure(0xFFFFFF00u);
  CHECK(w.in_backoff(0xFFFFFFFFu));
  CHECK(w.in_backoff(0x000002E7u));   // 0x100 + 0x2E7 = 999 ms elapsed
  CHECK(!w.in_backoff(0x000002E8u));  // 1000 ms elapsed

  p::RecoveryPolicy m(1000, 60000);
  m.set_max(10);  // clamped to base
  m.on_failure(0);
  m.on_failure(0);
  CHECK(m.current_delay() == 1000);
}

int main() {
  test_register_read();
  test_register_write();
  test_direct_commands();
  test_fifo();
  test_describe();
  test_uid_format();
  test_backoff();
  test_recovery_policy();
  if (failures) {
    std::fprintf(stderr, "%d of %d checks failed\n", failures, checks);
    return 1;
  }
  std::printf("All %d checks passed\n", checks);
  return 0;
}
