"""Exercise the patched AudioReach parser with exact-size firmware buffers."""
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = 'sound/soc/qcom/qdsp6/topology.c'


class AudioReachTopologyTest(unittest.TestCase):
  def test_actual_parser_under_sanitizers(self):
    with tempfile.TemporaryDirectory() as directory:
      root = pathlib.Path(directory)
      source = root / SOURCE
      source.parent.mkdir(parents=True)
      source.write_bytes(subprocess.check_output(['git', '-C', str(ROOT / 'kernel/linux'), 'show', f'HEAD:{SOURCE}']))
      for patch in ('0115-audio-module-config-size.patch', '0117-audio-topology-tuple-bounds.patch'):
        subprocess.run(['git', 'apply', str(ROOT / 'kernel/patches' / patch)], cwd=root, check=True)
      text = source.read_text()
      start = text.index('static struct audioreach_module_priv_data *\naudioreach_get_module_priv_data(')
      end = text.index('\n}', start) + 2
      check = root / 'check.c'
      check.write_text(PREAMBLE + text[start:end] + CASES)
      subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                      '-fno-omit-frame-pointer', '-g', str(check), '-o', str(root / 'check')], check=True)
      subprocess.run([str(root / 'check')], check=True, timeout=10)


PREAMBLE = r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint32_t __le32;
#define le32_to_cpu(x) (x)
#define ERR_PTR(x) ((void *)(intptr_t)(x))
#define SND_SOC_AR_TPLG_MODULE_CFG_TYPE 0x01001006
struct snd_soc_tplg_vendor_array { __le32 size, type, num_elems; } __attribute__((packed));
struct snd_soc_tplg_private {
  __le32 size;
  union { char data[0]; struct snd_soc_tplg_vendor_array array[0]; };
} __attribute__((packed));
struct audioreach_module_priv_data { __le32 size, type, priv[2], data[0]; };
#define struct_size(p, member, count) (sizeof(*(p)) + sizeof((p)->member[0]) * (count))
static int fail_allocation;
#define kzalloc_flex(p, member, count) (fail_allocation ? NULL : calloc(1, struct_size(&(p), member, count)))
'''

CASES = r'''
static struct snd_soc_tplg_private *fixture(u32 length) {
  struct snd_soc_tplg_private *p = calloc(1, sizeof(*p) + length);
  assert(p);
  p->size = length;
  return p;
}
static void word(void *data, unsigned offset, u32 value) { memcpy((u8 *)data + offset, &value, 4); }
static void invalid(struct snd_soc_tplg_private *p) {
  assert(audioreach_get_module_priv_data(p) == ERR_PTR(-EINVAL));
  free(p);
}
int main(void) {
  struct snd_soc_tplg_private *p = fixture(0);
  assert(audioreach_get_module_priv_data(p) == NULL);
  free(p);
  for (unsigned n = 1; n < 12; n++) invalid(fixture(n));
  const u32 bad_sizes[] = {0, 1, 4, 8, 11, 13, 0x80000000, 0xffffffff};
  for (unsigned i = 0; i < sizeof(bad_sizes)/sizeof(bad_sizes[0]); i++) {
    p = fixture(12); word(p->data, 0, bad_sizes[i]); invalid(p);
  }
  for (unsigned n = 1; n < 12; n++) {
    p = fixture(12 + n); word(p->data, 0, 12); invalid(p);
  }
  p = fixture(12); word(p->data, 0, 12);
  assert(audioreach_get_module_priv_data(p) == NULL); free(p);
  for (unsigned bytes = 0; bytes < 20; bytes++) {
    p = fixture(16 + bytes);
    word(p->data, 0, bytes); word(p->data, 4, SND_SOC_AR_TPLG_MODULE_CFG_TYPE);
    if (bytes % 4) { invalid(p); continue; }
    memset(p->data + 16, 0x5a, bytes);
    struct audioreach_module_priv_data *r = audioreach_get_module_priv_data(p);
    assert(r && r != ERR_PTR(-EINVAL));
    assert(memcmp(r, p->data, 16 + bytes) == 0); free(r); free(p);
  }
  for (unsigned n = 12; n < 20; n++) {
    p = fixture(n); word(p->data, 0, 4); word(p->data, 4, SND_SOC_AR_TPLG_MODULE_CFG_TYPE); invalid(p);
  }
  p = fixture(16); word(p->data, 0, 0xfffffffc); word(p->data, 4, SND_SOC_AR_TPLG_MODULE_CFG_TYPE); invalid(p);
  p = fixture(36); word(p->data, 0, 12);
  word(p->data, 12, 8); word(p->data, 16, SND_SOC_AR_TPLG_MODULE_CFG_TYPE);
  word(p->data, 28, 0x12345678); word(p->data, 32, 0xabcdef01);
  struct audioreach_module_priv_data *r = audioreach_get_module_priv_data(p);
  assert(r && r->data[0] == 0x12345678 && r->data[1] == 0xabcdef01); free(r);
  fail_allocation = 1;
  assert(audioreach_get_module_priv_data(p) == ERR_PTR(-ENOMEM)); free(p);
  return 0;
}
'''


if __name__ == '__main__':
  unittest.main()
