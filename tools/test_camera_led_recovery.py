"""Compile the actual AW21024 driver callbacks and inject bus write failures."""
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class CameraLedRecoveryTest(unittest.TestCase):
  def test_actual_driver_recovers_without_replaying_ir(self):
    patch = (ROOT / 'kernel/patches/0102-leds-aw21024-camera.patch').read_text()
    section = patch.split('@@ -0,0 ', 1)[1].split('diff --git', 1)[0]
    source = '\n'.join(line[1:] for line in section.splitlines() if line.startswith('+'))
    callbacks = source[source.index('#define AW21024_BR_BASE'):source.index('static void aw21024_off')]
    with tempfile.TemporaryDirectory() as directory:
      root = pathlib.Path(directory)
      (root / 'check.c').write_text(PREAMBLE + callbacks + CASES)
      subprocess.run(['cc', '-std=c11', '-Wall', '-Werror', str(root / 'check.c'), '-o', str(root / 'check')], check=True)
      subprocess.run([str(root / 'check')], check=True)


PREAMBLE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8;
enum led_brightness { LED_OFF = 0 };
struct led_classdev { int unused; };
struct mutex { int held; };
struct i2c_client { u8 regs[256]; int writes, fail_at; bool fail_after_write; };
#define container_of(ptr, type, member) ((type *)((char *)(ptr) - offsetof(type, member)))
#define min_t(type, a, b) ((type)(a) < (type)(b) ? (type)(a) : (type)(b))
#define max_t(type, a, b) ((type)(a) > (type)(b) ? (type)(a) : (type)(b))
#define DIV_ROUND_UP(a, b) (((a) + (b) - 1) / (b))
#define DIV_ROUND_CLOSEST(a, b) (((a) + (b) / 2) / (b))
static void mutex_lock(struct mutex *m) { assert(!m->held); m->held = 1; }
static void mutex_unlock(struct mutex *m) { assert(m->held); m->held = 0; }
static void usleep_range(int a, int b) { (void)a; (void)b; }
static int i2c_smbus_write_byte_data(struct i2c_client *bus, u8 reg, u8 value) {
  bool fail = ++bus->writes == bus->fail_at;
  if (fail && !bus->fail_after_write) return -5;
  if (reg == 0x7f) memset(bus->regs, 0, sizeof(bus->regs));
  else {
    if (reg == 0 && value == 1) {
      for (int i = 1; i <= 24; i++) assert(bus->regs[i] == 0);
    }
    bus->regs[reg] = value;
  }
  return fail ? -5 : 0;
}
'''

CASES = r'''
static void setup(struct aw21024 *chip, struct i2c_client *bus) {
  memset(chip, 0, sizeof(*chip));
  memset(bus, 0, sizeof(*bus));
  chip->client = bus;
  for (int i = 0; i <= 12; i++) {
    chip->leds[i].chip = chip;
    chip->leds[i].channel = i;
  }
  assert(aw21024_init(chip) == 0);
  assert(aw21024_brightness_set(&chip->leds[12].cdev, 100) == 0);
  for (int i = 1; i <= 10; i++) assert(bus->regs[i] == 206);
  assert(aw21024_brightness_set(&chip->leds[2].cdev, 1285) == 0);
  assert(bus->regs[15] == 213);
  assert(bus->regs[0x58] == 6);
}

static void recovered_rgb(struct aw21024 *chip, struct i2c_client *bus) {
  bus->fail_at = 0;
  assert(aw21024_brightness_set(&chip->leds[2].cdev, 1285) == 0);
  assert(!chip->needs_init && !chip->lock.held && bus->regs[0] == 1);
  assert(bus->regs[15] == 213 && bus->regs[0x58] == 6);
  for (int i = 1; i <= 12; i++) assert(bus->regs[i] == 0);
  assert(aw21024_brightness_set(&chip->leds[12].cdev, 100) == 0);
  for (int i = 1; i <= 10; i++) assert(bus->regs[i] == 206);
  assert(bus->regs[15] == 213);
}

int main(void) {
  struct aw21024 chip;
  struct i2c_client bus;
  /* Fail every write in a normal RGB or grouped IR update. */
  for (int after = 0; after <= 1; after++) {
    for (int ir = 0; ir <= 1; ir++) {
      for (int at = 1; at <= (ir ? 11 : 5); at++) {
        setup(&chip, &bus);
        bus.writes = 0; bus.fail_at = at; bus.fail_after_write = after;
        assert(aw21024_brightness_set(&chip.leds[ir ? 12 : 0].cdev, ir ? 50 : 1285) == -5);
        assert(chip.needs_init && !chip.lock.held && bus.regs[0] == 0);
        for (int i = 1; i <= 24; i++) assert(bus.regs[i] == 0);
        recovered_rgb(&chip, &bus);
      }
    }
    /* Recovery itself can fail at any register, including enable. */
    for (int at = 1; at <= 52; at++) {
      setup(&chip, &bus);
      chip.needs_init = true;
      bus.writes = 0; bus.fail_at = at; bus.fail_after_write = after;
      assert(aw21024_brightness_set(&chip.leds[2].cdev, 1285) == -5);
      assert(chip.needs_init && !chip.lock.held && bus.regs[0] == 0);
      recovered_rgb(&chip, &bus);
    }
  }
  return 0;
}
'''


if __name__ == '__main__':
  unittest.main()
