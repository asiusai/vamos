"""Run the patched kernel's LE address and security checks with host-side inputs."""
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def function(source, name):
  start = source.index(name + '(')
  start = source.rfind('\n', 0, start) + 1
  return source[start:source.index('\n}', start) + 2]


class BluetoothSecurityTest(unittest.TestCase):
  def test_actual_kernel_checks(self):
    with tempfile.TemporaryDirectory() as directory:
      root = pathlib.Path(directory)
      for name in ('net/bluetooth/hci_conn.c', 'net/bluetooth/hci_event.c',
                   'include/net/bluetooth/hci.h', 'include/net/bluetooth/hci_core.h',
                   'drivers/bluetooth/btusb.c'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(subprocess.check_output(['git', '-C', str(ROOT / 'kernel/linux'), 'show', f'HEAD:{name}']))
      for patch in ('0096-bluetooth-smp-peer-address.patch', '0097-bluetooth-sc-only-le-security.patch',
                    '0098-bluetooth-aic-host-address-resolution.patch'):
        subprocess.run(['git', 'apply', str(ROOT / 'kernel/patches' / patch)], cwd=root, check=True)
      event = (root / 'net/bluetooth/hci_event.c').read_text()
      connection = (root / 'net/bluetooth/hci_conn.c').read_text()
      header = (root / 'include/net/bluetooth/hci_core.h').read_text()
      privacy = header[header.index('#define ll_privacy_capable'):header.index('#define read_key_size_capable')]
      source = PREAMBLE + privacy + function(event, 'ev_bdaddr_type') + function(event, 'le_conn_update_addr')
      source += function(connection, 'hci_conn_check_link_mode') + CASES
      (root / 'check.c').write_text(source)
      subprocess.run(['cc', '-std=c11', '-Wall', '-Werror', str(root / 'check.c'), '-o', str(root / 'check')], check=True)
      subprocess.run([str(root / 'check')], check=True)


PREAMBLE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8;
typedef struct { u8 b[6]; } bdaddr_t;
static const bdaddr_t zero;
#define BDADDR_ANY (&zero)
#define ADDR_LE_DEV_PUBLIC 0
#define ADDR_LE_DEV_RANDOM 1
#define ADDR_LE_DEV_PUBLIC_RESOLVED 2
#define ADDR_LE_DEV_RANDOM_RESOLVED 3
#define LE_LINK 1
#define ACL_LINK 2
#define BT_SECURITY_FIPS 4
#define HCI_LK_AUTH_COMBINATION_P256 8
#define HCI_SC_ONLY 0
#define HCI_PRIVACY 1
#define HCI_CONN_ENCRYPT 0
#define HCI_CONN_AES_CCM 1
#define HCI_LE_LL_PRIVACY 0x40
#define HCI_QUIRK_BROKEN_LL_PRIVACY 0
#define BT_DBG(...) ((void)0)
#define bt_dev_dbg(...) ((void)0)
#define bt_dev_err(...) ((void)0)
struct hci_dev { unsigned long flags, quirks; u8 le_features[8], commands[64], adv_addr_type; bdaddr_t rpa, random_addr, bdaddr; int le_conn_min_interval, le_conn_max_interval; };
struct hci_conn { struct hci_dev *hdev; unsigned long flags; bool out, sc, ssp; u8 dst_type, resp_addr_type, init_addr_type;
 bdaddr_t resp_addr, init_addr; int le_conn_min_interval, le_conn_max_interval, type, key_type, sec_level, enc_key_size; };
static bool test_bit(int bit, const unsigned long *flags) { return (*flags & (1UL << bit)) != 0; }
static bool hci_dev_test_flag(struct hci_dev *dev, int bit) { return test_bit(bit, &dev->flags); }
static bool hci_test_quirk(struct hci_dev *dev, int bit) { return test_bit(bit, &dev->quirks); }
static bool hci_conn_sc_enabled(struct hci_conn *conn) { return conn->sc; }
static bool hci_conn_ssp_enabled(struct hci_conn *conn) { return conn->ssp; }
static bool ext_adv_capable(struct hci_dev *dev) { return true; }
static int bacmp(const bdaddr_t *a, const bdaddr_t *b) { return memcmp(a,b,sizeof(*a)); }
static void bacpy(bdaddr_t *a, const bdaddr_t *b) { memcpy(a,b,sizeof(*a)); }
static void hci_copy_identity_address(struct hci_dev *dev, bdaddr_t *addr, u8 *type) { *addr=dev->bdaddr; *type=0; }
'''

CASES = r'''
int main(void) {
 struct hci_dev dev = {.flags=1, .bdaddr={{10,20,30,40,50,60}}};
 for (int feature=0; feature<2; feature++) for (int broken=0; broken<2; broken++) {
  dev.le_features[0]=feature ? HCI_LE_LL_PRIVACY : 0;
  dev.quirks=broken;
  assert(ll_privacy_capable(&dev)==(feature && !broken));
  dev.commands[39]=4;
  assert(privacy_mode_capable(&dev)==(feature && !broken));
 }
 bdaddr_t identity={{1,2,3,4,5,6}}, rpa={{11,12,13,14,15,0x40}}, local={{21,22,23,24,25,0x40}};
 for (int role=0; role<2; role++) for (int type=0; type<4; type++) for (int private_peer=0; private_peer<2; private_peer++) {
  struct hci_conn conn={.hdev=&dev, .out=role};
  le_conn_update_addr(&conn,&identity,type,NULL,private_peer ? &rpa : NULL);
  assert((role ? conn.resp_addr_type : conn.init_addr_type)==(private_peer ? 1 : type%2));
  assert(!bacmp(role ? &conn.resp_addr : &conn.init_addr, private_peer ? &rpa : &identity));
  assert(!bacmp(role ? &conn.init_addr : &conn.resp_addr, &dev.bdaddr));
  le_conn_update_addr(&conn,&identity,type,&local,&rpa);
  assert(!bacmp(role ? &conn.init_addr : &conn.resp_addr, &local));
  assert((role ? conn.init_addr_type : conn.resp_addr_type)==1);
 }
 for (int level=1; level<=4; level++) for (int size=7; size<=16; size++) for (int flags=0; flags<4; flags++) {
  struct hci_conn conn={.hdev=&dev,.type=LE_LINK,.sec_level=level,.enc_key_size=size,.flags=flags};
  assert(hci_conn_check_link_mode(&conn)==(level==4 && size==16 && flags==3));
 }
 struct hci_conn classic={.hdev=&dev,.type=ACL_LINK,.sec_level=4,.enc_key_size=16,.flags=3,.sc=true,.ssp=true,.key_type=8};
 assert(hci_conn_check_link_mode(&classic));
 classic.key_type=0; assert(!hci_conn_check_link_mode(&classic));
 classic.key_type=8; classic.sc=false; assert(!hci_conn_check_link_mode(&classic));
 return 0;
}
'''

if __name__ == '__main__':
  unittest.main()
