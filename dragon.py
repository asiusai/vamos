#!/usr/bin/env python3
"""Dragon Q6A control: power, USB dock, EDL, UART, SSH.

Hardware: 12V via CHA_FAN1 (pwm1 on nct6799 / hwmon5), EDL/NCM through a
ganged-power USB dock, serial on /dev/ttyUSB0, NCM at 192.168.42.2.

Usage:
  dragon.py on                        Power on via fan header
  dragon.py off                       Power off
  dragon.py reboot                    Power off, settle, power on
  dragon.py edl                       Enter EDL through a running vamOS device
  dragon.py edl --bios                Cold cycle + navigate BIOS menu to EDL
  dragon.py edl --no-cycle            Skip power cycle, just navigate
  dragon.py normal                    Reset EDL to normal boot using dock VBUS
  dragon.py dock on|off|cycle|status  Control the development dock VBUS
  dragon.py ssh [cmd...]               SSH to Dragon over USB NCM (run cmd if given)
  dragon.py status                     Show power, NCM, SSH, UART status
  dragon.py health [--target USER@HOST] Run health check
  dragon.py uart read [SECONDS]       Read UART for N seconds (default 3)
  dragon.py uart send 'string'        Send string (supports \\n/\\r/\\t)
  dragon.py uart exec 'cmd' [SECONDS] Send cmd, collect output
  dragon.py uart login PASSWORD       Root login sequence
  dragon.py uart wake                 Poke with newline, print response
"""
import argparse
import codecs
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

from tools.ssh_key import default_ssh_key

def find_hwmon(name="nct6799"):
    for h in os.listdir("/sys/class/hwmon"):
        try:
            with open(f"/sys/class/hwmon/{h}/name") as f:
                if f.read().strip() == name:
                    return f"/sys/class/hwmon/{h}"
        except FileNotFoundError:
            continue
    subprocess.run(["sudo", "modprobe", "nct6775"], capture_output=True)
    for h in os.listdir("/sys/class/hwmon"):
        try:
            with open(f"/sys/class/hwmon/{h}/name") as f:
                if f.read().strip() == name:
                    return f"/sys/class/hwmon/{h}"
        except FileNotFoundError:
            continue
    return None
PORT = os.environ.get("DRAGON_UART", "/dev/ttyUSB0")
BAUD = 115200
NCM_IP = "192.168.42.2"
NCM_HOST_IP = "192.168.42.50/24"
SSH_KEY = str(default_ssh_key())
OFF_SETTLE_SECS = 5
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EDL_LOADER = os.path.join(
    SCRIPT_DIR,
    "firmware-dragon/flat_build/spinor/dragon-q6a/prog_firehose_ddr.elf",
)
DOCK_USB2_HUB = os.environ.get("DRAGON_DOCK_USB2_HUB", "1-1")
DOCK_USB3_HUB = os.environ.get("DRAGON_DOCK_USB3_HUB", "2-1")
HEALTH_SCRIPT = os.path.join(SCRIPT_DIR, "dragon_health.py")
REMOTE_HEALTH_SCRIPT = "/tmp/dragon_health.py"
SSH_HOSTKEY_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "GlobalKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
]
SSH_HEALTH_OPTS = [
    "-o", "ConnectTimeout=5",
    "-o", "ServerAliveInterval=3",
    "-o", "ServerAliveCountMax=2",
]
SSH_OPTS = ["-i", SSH_KEY, *SSH_HOSTKEY_OPTS]

F2 = b"\x1bOQ"
DOWN = b"\x1b[B"
ENTER = b"\r"

# -- power --

def power(state):
    hwmon = find_hwmon()
    if not hwmon:
        sys.exit("nct6799 hwmon not found — is the module loaded?")
    subprocess.run(["sudo", "sh", "-c",
        f"echo 1 > {hwmon}/pwm1_mode && "
        f"echo 1 > {hwmon}/pwm1_enable && "
        f"echo {state} > {hwmon}/pwm1"], check=True)

def cmd_on(_args):
    power(255)
    print("Dragon ON")

def cmd_off(_args):
    power(0)
    print("Dragon OFF")

def cmd_reboot(_args):
    power(0)
    print("Dragon OFF")
    time.sleep(OFF_SETTLE_SECS)
    power(255)
    print("Dragon ON")

# -- ncm --

def find_ncm_interface():
    for iface in os.listdir("/sys/class/net"):
        if iface == "lo":
            continue
        try:
            with open(f"/sys/class/net/{iface}/device/../idVendor") as f:
                vid = f.read().strip()
            with open(f"/sys/class/net/{iface}/device/../idProduct") as f:
                pid = f.read().strip()
            if vid == "1d6b" and pid == "0103":
                return iface
        except FileNotFoundError:
            continue
    return None

def ensure_ncm():
    iface, host_ip = check_ncm()
    if host_ip:
        return
    if not iface:
        sys.exit("No Dragon NCM interface found — is the USB cable connected?")
    print(f"[ncm] bringing up {iface}")
    subprocess.run(["sudo", "ip", "link", "set", iface, "up"], check=True)
    if shutil.which("dhcpcd"):
        subprocess.run(["sudo", "dhcpcd", "-1", iface],
                       capture_output=True, text=True)
    _, host_ip = check_ncm()
    if not host_ip:
        # The gadget MAC changes across boots, so network managers do not always
        # reapply the bench connection. The Dragon is fixed at .2; configure the
        # host side directly instead of depending on a MAC-specific profile.
        subprocess.run(["sudo", "ip", "address", "replace",
                        NCM_HOST_IP, "dev", iface], check=True)
        time.sleep(1)
        _, host_ip = check_ncm()
    if not host_ip:
        sys.exit(f"[ncm] failed to configure {iface}")

# -- USB development dock --

def dock_hub(action, location, check=True):
    if not shutil.which("uhubctl"):
        sys.exit("uhubctl not found — install it before controlling dock VBUS")
    cmd = ["sudo", "uhubctl", "-f", "-e", "-l", location]
    if action:
        cmd.extend(["-a", action])
    return subprocess.run(cmd, check=check)

def dock_power(enabled):
    action = "on" if enabled else "off"
    locations = (DOCK_USB2_HUB, DOCK_USB3_HUB)
    if enabled:
        # The dock's USB 3 half can disappear when its ganged outputs are off.
        # Restore USB 2 first, then allow USB 3 to re-enumerate before enabling it.
        dock_hub(action, locations[0])
        time.sleep(2)
        last_error = None
        for _ in range(10):
            try:
                dock_hub(action, locations[1])
                break
            except subprocess.CalledProcessError as exc:
                last_error = exc
                time.sleep(0.5)
        else:
            raise last_error
    else:
        for location in locations:
            dock_hub(action, location)

def cmd_dock(args):
    if args.dock_action == "status":
        for location in (DOCK_USB2_HUB, DOCK_USB3_HUB):
            dock_hub(None, location, check=False)
        return
    if args.dock_action == "cycle":
        if args.seconds < 0:
            sys.exit("dock --seconds must not be negative")
        print("[dock] VBUS off")
        dock_power(False)
        time.sleep(args.seconds)
        print("[dock] VBUS on")
        dock_power(True)
        return
    enabled = args.dock_action == "on"
    print(f"[dock] VBUS {'on' if enabled else 'off'}")
    dock_power(enabled)

# -- status --

def check_power():
    hwmon = find_hwmon()
    if not hwmon:
        return None
    try:
        with open(f"{hwmon}/pwm1") as f:
            return int(f.read().strip()) > 0
    except (FileNotFoundError, PermissionError):
        return None

def check_ncm():
    iface = find_ncm_interface()
    if not iface:
        return None, None
    r = subprocess.run(["ip", "-4", "-o", "addr", "show", iface], capture_output=True, text=True)
    for m in re.finditer(r'inet ([\d.]+)', r.stdout):
        if m.group(1).startswith("192.168.42."):
            return iface, m.group(1)
    return iface, None

def check_ssh_cmd(cmd):
    r = subprocess.run(["ssh", *SSH_OPTS,
                        "-o", "ConnectTimeout=2", "-o", "BatchMode=yes",
                        f"comma@{NCM_IP}", cmd],
                       capture_output=True, text=True, timeout=8)
    return r.stdout.strip() if r.returncode == 0 else None

def check_uart():
    try:
        import serial
        s = serial.Serial(PORT, BAUD, timeout=0.3)
        s.close()
        return True
    except Exception:
        return False

def check_edl():
    r = subprocess.run(["lsusb", "-d", "05c6:9008"], capture_output=True, text=True)
    return r.returncode == 0

def cmd_status(_args):
    pwr = check_power()
    if pwr is None:
        print("  power:    ? (can't read nct6799 pwm1)")
    else:
        print(f"  power:    {'on' if pwr else 'off'}")

    uart = check_uart()
    print(f"  uart:     {PORT} {'ok' if uart else 'not available'}")

    edl = check_edl()
    print(f"  edl:      {'yes (05c6:9008)' if edl else 'no'}")

    iface, host_ip = check_ncm()
    if iface and not host_ip:
        try:
            ensure_ncm()
            _, host_ip = check_ncm()
        except (Exception, SystemExit):
            pass

    print(f"  ncm:      {NCM_IP}" if host_ip else "  ncm:      no")

    if host_ip:
        try:
            inet_addrs = check_ssh_cmd("ip -4 addr show | awk '/inet 192\\.168\\./ && !/usb/ {split($2,a,\"/\"); split($NF,d,\" \"); printf \"%s(%s) \", a[1], $NF}'")
        except Exception:
            inet_addrs = None
        print(f"  internet: {inet_addrs.strip()}" if inet_addrs else "  internet: no")
    else:
        print("  internet: -")

# -- ssh --

def cmd_ssh(args):
    ensure_ncm()
    ssh_cmd = ["ssh", *SSH_OPTS, f"comma@{NCM_IP}"]
    if args.ssh_args:
        ssh_cmd.extend(args.ssh_args)
    os.execvp("ssh", ssh_cmd)

# -- edl --

def wait_for_edl_usb(timeout=30):
    return wait_for_usb("05c6:9008", timeout)

def wait_for_usb(vid_pid, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = subprocess.run(["lsusb", "-d", vid_pid],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return True
        time.sleep(0.5)
    return False

def cmd_edl(args):
    if check_edl():
        print("[edl] Dragon is already in EDL mode (05c6:9008)")
        return 0

    iface, _host_ip = check_ncm()
    if iface and not args.bios and not args.no_cycle:
        ensure_ncm()
        print("[edl] requesting firmware EDL reset over NCM")
        try:
            subprocess.run(
                ["ssh", *SSH_OPTS, "-o", "BatchMode=yes",
                 "-o", "ConnectTimeout=4", f"comma@{NCM_IP}",
                 "sudo reboot-edl"],
                capture_output=True, text=True, timeout=6,
            )
        except subprocess.TimeoutExpired:
            # A successful reboot tears down NCM before SSH can close cleanly.
            pass
        if wait_for_edl_usb(timeout=30):
            print("[edl] Dragon is in EDL mode (05c6:9008)")
            return 0
        if args.software_only:
            print("[edl] software EDL reset did not enumerate", file=sys.stderr)
            return 2
        print("[edl] software reset failed; falling back to BIOS navigation",
              file=sys.stderr)

    return cmd_edl_bios(args)

def cmd_edl_bios(args):
    try:
        import serial
    except ImportError:
        sys.exit("pyserial not installed — run: pip3 install --user pyserial")

    try:
        s = serial.Serial(PORT, BAUD, timeout=0.2)
    except Exception as e:
        sys.exit(f"[edl] open {PORT}: {e}")

    if not args.no_cycle:
        print("[edl] power off")
        power(0)
        time.sleep(6)
        print("[edl] power on")
        power(255)
        print(f"[edl] pressing F2 for {args.f2_wait:.0f}s to enter setup")
        end = time.time() + args.f2_wait
        while time.time() < end:
            s.write(F2)
            s.flush()
            time.sleep(0.4)
        s.read(200000)
        time.sleep(1)

    print("[edl] nav: 7 x down + Enter")
    for _ in range(7):
        s.write(DOWN)
        s.flush()
        time.sleep(0.5)
    time.sleep(0.5)
    s.write(ENTER)
    s.flush()
    s.close()

    print("[edl] Enter sent, waiting for EDL USB...")
    if wait_for_edl_usb(timeout=30):
        print("[edl] Dragon is in EDL mode (05c6:9008)")
        return 0
    print("[edl] EDL device did not appear on USB within 30s", file=sys.stderr)
    return 2

def detach_qcserial():
    base = "/sys/bus/usb/drivers/qcserial"
    if not os.path.isdir(base):
        return
    for name in os.listdir(base):
        if ":" not in name or not os.path.islink(os.path.join(base, name)):
            continue
        subprocess.run(["sudo", "tee", os.path.join(base, "unbind")],
                       input=name, text=True, stdout=subprocess.DEVNULL,
                       check=True)

def cmd_normal(args):
    if not check_edl():
        if wait_for_usb("1d6b:0103", timeout=1):
            print("[normal] Dragon is already in normal NCM mode")
            ensure_ncm()
            return 0
        sys.exit("[normal] Dragon is not visible in EDL or NCM mode")
    if not shutil.which("edl-ng"):
        sys.exit("edl-ng not found")
    if args.delay < 4:
        sys.exit("normal --delay must be at least 4 seconds")
    if not os.path.isfile(EDL_LOADER):
        sys.exit(f"Firehose loader not found: {EDL_LOADER}")

    memory = os.environ.get("VAMOS_EDL_MEMORY", "Nvme").capitalize()
    if memory not in {"Ufs", "Nvme"}:
        sys.exit("VAMOS_EDL_MEMORY must be Ufs or Nvme")

    detach_qcserial()
    reset_cmd = [
        "sudo", "edl-ng", "--maxpayload=65536",
        f"--memory={memory}", "--slot=0",
        f"--loader={EDL_LOADER}", "reset", f"--delay={args.delay}",
    ]
    print(f"[normal] scheduling Firehose reset in {args.delay}s")
    subprocess.run(reset_cmd, check=True)

    dock_off = True
    try:
        print("[normal] dock VBUS off")
        dock_power(False)
        time.sleep(args.delay + 2)
    finally:
        if dock_off:
            print("[normal] dock VBUS on")
            dock_power(True)

    if not wait_for_usb("1d6b:0103", timeout=30):
        if check_edl():
            print("[normal] Dragon returned to EDL; VBUS did not release EDL",
                  file=sys.stderr)
        else:
            print("[normal] NCM did not enumerate within 30s", file=sys.stderr)
        return 2
    ensure_ncm()
    print(f"[normal] Dragon is in normal mode at {NCM_IP}")
    return 0

# -- uart --

def open_port():
    try:
        import serial
    except ImportError:
        sys.exit("pyserial not installed — run: pip3 install --user pyserial")
    try:
        return serial.Serial(PORT, BAUD, bytesize=8, parity='N', stopbits=1,
                             rtscts=False, xonxoff=False, timeout=0.1)
    except Exception as e:
        sys.exit(f"open {PORT}: {e}")

def strip_ansi(s):
    return re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', s).replace('\r', '')

def drain(s, seconds, echo=True):
    buf = bytearray()
    end = time.time() + seconds
    while time.time() < end:
        d = s.read(4096)
        if d:
            buf.extend(d)
            if echo:
                sys.stdout.write(strip_ansi(d.decode('utf-8', 'replace')))
                sys.stdout.flush()
            end = time.time() + 0.4
    if not echo:
        return strip_ansi(bytes(buf).decode('utf-8', 'replace'))

def cmd_uart(args):
    sub = args.uart_cmd
    if sub == "read":
        secs = float(args.uart_args[0]) if args.uart_args else 3.0
        with open_port() as s:
            drain(s, secs)
    elif sub == "send":
        if not args.uart_args:
            sys.exit("uart send: need string")
        data = codecs.decode(args.uart_args[0], 'unicode_escape').encode('utf-8', 'replace')
        with open_port() as s:
            s.write(data)
            s.flush()
    elif sub == "wake":
        with open_port() as s:
            s.write(b'\r\n')
            s.flush()
            drain(s, 2.0)
    elif sub == "exec":
        if not args.uart_args:
            sys.exit("uart exec: need command")
        command = args.uart_args[0]
        secs = float(args.uart_args[1]) if len(args.uart_args) > 1 else 3.0
        with open_port() as s:
            drain(s, 0.3, echo=False)
            s.write(command.encode('utf-8') + b'\r\n')
            s.flush()
            drain(s, secs)
    elif sub == "login":
        if not args.uart_args:
            sys.exit("uart login: need password")
        pw = args.uart_args[0]
        with open_port() as s:
            s.write(b'\r\n')
            out = drain(s, 2.0, echo=False)
            sys.stdout.write(out)
            sys.stdout.flush()
            if '#' in (out.splitlines()[-1:] or ['']):
                print("\n[already logged in as root]")
                return

            if 'login:' not in out and 'password:' not in out.lower():
                s.write(b'\r\n')
                out = drain(s, 2.0, echo=False)
                sys.stdout.write(out)
                sys.stdout.flush()

            if 'login:' in out:
                s.write(b'root\r\n')
                out = drain(s, 2.0, echo=False)
                sys.stdout.write(out)
                sys.stdout.flush()

            if 'password' in out.lower():
                s.write(pw.encode() + b'\r\n')
                drain(s, 3.0)
            else:
                print("\n[uart login prompt not found]", file=sys.stderr)
                return
    else:
        sys.exit(f"uart: unknown subcommand '{sub}'")

# -- health --

def cmd_health(args):
    target = args.target or f"comma@{NCM_IP}"
    openpilot_root = args.openpilot_root.rstrip("/") or "/"
    ssh_opts = [*(SSH_HOSTKEY_OPTS if args.target else SSH_OPTS), *SSH_HEALTH_OPTS]
    if args.identity:
        ssh_opts = ["-i", os.path.expanduser(args.identity), *SSH_HOSTKEY_OPTS, *SSH_HEALTH_OPTS]
    if not args.target:
        ensure_ncm()
    if not os.path.isfile(HEALTH_SCRIPT):
        sys.exit(f"health script not found: {HEALTH_SCRIPT}")

    print(f"[health] copying {HEALTH_SCRIPT} to {target}:{REMOTE_HEALTH_SCRIPT}", flush=True)
    subprocess.run(["scp", *ssh_opts, HEALTH_SCRIPT,
                    f"{target}:{REMOTE_HEALTH_SCRIPT}"], check=True)

    root = shlex.quote(openpilot_root)
    print(f"[health] testing openpilot root {openpilot_root}", flush=True)
    ssh_cmd = ["ssh", *ssh_opts, target,
               f"cd {root} && PYTHONPATH={root}:{root}/tinygrad_repo "
               f"OPENPILOT_ROOT={root} "
               f"python3 -u {REMOTE_HEALTH_SCRIPT}"]
    ret = subprocess.run(ssh_cmd).returncode
    local_dir = "/tmp/dragon_health_local"
    os.makedirs(local_dir, exist_ok=True)
    print(f"\n[health] pulling snapshots to {local_dir}", flush=True)
    subprocess.run(["scp", *ssh_opts,
                    f"{target}:/tmp/dragon_health/*.jpg", local_dir])
    return ret

# -- main --

def main():
    ap = argparse.ArgumentParser(description="Dragon Q6A control")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("on", help="Power on")
    sub.add_parser("off", help="Power off")
    sub.add_parser("reboot", help="Power cycle")
    sub.add_parser("status", help="Show Dragon status")
    dock_p = sub.add_parser("dock", help="Control development dock VBUS")
    dock_p.add_argument("dock_action", choices=["on", "off", "cycle", "status"])
    dock_p.add_argument("--seconds", type=float, default=3.0,
                        help="Off time for dock cycle (default 3 seconds)")
    ssh_p = sub.add_parser("ssh", help="SSH over USB NCM")
    ssh_p.add_argument("ssh_args", nargs="*")

    edl_p = sub.add_parser("edl", help="Enter EDL mode over NCM, with BIOS fallback")
    edl_p.add_argument("--bios", action="store_true",
                       help="Force the legacy BIOS navigation method")
    edl_p.add_argument("--software-only", action="store_true",
                       help="Do not fall back to BIOS if reboot-edl fails")
    edl_p.add_argument("--no-cycle", action="store_true",
                       help="Skip power cycle (assume BIOS already at main menu)")
    edl_p.add_argument("--f2-wait", type=float, default=10.0,
                       help="Seconds to press F2 after power-on (default 10)")

    normal_p = sub.add_parser("normal", help="Reset EDL to normal boot via dock VBUS")
    normal_p.add_argument("--delay", type=int, default=8,
                          help="Firehose reset delay in seconds (default 8)")

    uart_p = sub.add_parser("uart", help="UART commands")
    uart_p.add_argument("uart_cmd", choices=["read", "send", "exec", "login", "wake"])
    uart_p.add_argument("uart_args", nargs="*")

    health_p = sub.add_parser("health", help="Run system health check on Dragon")
    health_p.add_argument("--target", help="SSH target, e.g. comma@asius1. Defaults to USB NCM.")
    health_p.add_argument("--identity", help="SSH identity file for health target.")
    health_p.add_argument("--openpilot-root", default="/data/openpilot",
                          help="Remote openpilot checkout to test (default: /data/openpilot).")

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        sys.exit(1)

    dispatch = {"on": cmd_on, "off": cmd_off, "reboot": cmd_reboot,
                "status": cmd_status, "dock": cmd_dock, "ssh": cmd_ssh,
                "edl": cmd_edl, "normal": cmd_normal, "uart": cmd_uart,
                "health": cmd_health}
    ret = dispatch[args.cmd](args)
    sys.exit(ret or 0)

if __name__ == "__main__":
    main()
