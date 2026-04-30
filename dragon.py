#!/usr/bin/env python3
"""Dragon Q6A control: power, EDL, UART, SSH.

Hardware: 12V via CHA_FAN1 (pwm1 on nct6799 / hwmon5), EDL over direct USB,
serial on /dev/ttyUSB0, network over USB NCM (192.168.42.2).

Usage:
  dragon.py on                        Power on via fan header
  dragon.py off                       Power off
  dragon.py reboot                    Power off, settle, power on
  dragon.py edl                       Cold cycle + navigate BIOS menu to EDL
  dragon.py edl --no-cycle            Skip power cycle, just navigate
  dragon.py ssh [cmd...]               SSH to Dragon over USB NCM (run cmd if given)
  dragon.py status                     Show power, NCM, SSH, UART status
  dragon.py uart read [SECONDS]       Read UART for N seconds (default 3)
  dragon.py uart send 'string'        Send string (supports \\n/\\r/\\t)
  dragon.py uart exec 'cmd' [SECONDS] Send cmd, collect output
  dragon.py uart login PASSWORD       Root login sequence
  dragon.py uart wake                 Poke with newline, print response
"""
import argparse, codecs, os, re, subprocess, sys, time

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
SSH_KEY = os.path.expanduser("~/.ssh/comma_setup")
OFF_SETTLE_SECS = 5

F2 = b"\x1bOQ"
DOWN = b"\x1b[B"
ENTER = b"\r"

# -- power --

def power(state):
    hwmon = find_hwmon()
    if not hwmon:
        sys.exit("nct6799 hwmon not found — is the module loaded?")
    subprocess.run(["sudo", "sh", "-c",
        f"echo 1 > {hwmon}/pwm1_enable && echo {state} > {hwmon}/pwm1"], check=True)

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
        if not iface.startswith("enx"):
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
    r = subprocess.run(["sudo", "dhcpcd", "-1", iface],
                       capture_output=True, text=True)
    _, host_ip = check_ncm()
    if not host_ip:
        sys.exit(f"[ncm] DHCP failed on {iface} — Dragon may not have NCM enabled")

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
    r = subprocess.run(["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no",
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
        print(f"  power:    ? (can't read {HWMON_PWM})")
    else:
        print(f"  power:    {'on' if pwr else 'off'}")

    uart = check_uart()
    print(f"  uart:     {PORT} {'ok' if uart else 'not available'}")

    edl = check_edl()
    print(f"  edl:      {'yes (05c6:9008)' if edl else 'no'}")

    iface, host_ip = check_ncm()
    if iface and not host_ip:
        try:
            subprocess.run(["sudo", "ip", "link", "set", iface, "up"], check=True, capture_output=True)
            subprocess.run(["sudo", "dhcpcd", "-1", iface], check=True, capture_output=True, timeout=10)
            _, host_ip = check_ncm()
        except Exception:
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
    ssh_cmd = ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", f"comma@{NCM_IP}"]
    if args.ssh_args:
        ssh_cmd.extend(args.ssh_args)
    os.execvp("ssh", ssh_cmd)

# -- edl --

def wait_for_edl_usb(timeout=30):
    for _ in range(timeout):
        r = subprocess.run(["lsusb"], capture_output=True, text=True)
        if "05c6:9008" in r.stdout:
            return True
        time.sleep(1)
    return False

def cmd_edl(args):
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
            sys.stdout.write(out); sys.stdout.flush()
            if '#' in (out.splitlines()[-1:] or ['']):
                print("\n[already logged in as root]")
                return
            if 'login:' in out:
                s.write(b'root\r\n')
                drain(s, 2.0)
            if 'password' in out.lower():
                s.write(pw.encode() + b'\r\n')
                drain(s, 3.0)
            else:
                s.write(b'root\r\n')
                drain(s, 2.0)
                s.write(pw.encode() + b'\r\n')
                drain(s, 3.0)
    else:
        sys.exit(f"uart: unknown subcommand '{sub}'")

# -- main --

def main():
    ap = argparse.ArgumentParser(description="Dragon Q6A control")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("on", help="Power on")
    sub.add_parser("off", help="Power off")
    sub.add_parser("reboot", help="Power cycle")
    sub.add_parser("status", help="Show Dragon status")
    ssh_p = sub.add_parser("ssh", help="SSH over USB NCM")
    ssh_p.add_argument("ssh_args", nargs="*")

    edl_p = sub.add_parser("edl", help="Enter EDL mode via BIOS")
    edl_p.add_argument("--no-cycle", action="store_true",
                       help="Skip power cycle (assume BIOS already at main menu)")
    edl_p.add_argument("--f2-wait", type=float, default=10.0,
                       help="Seconds to press F2 after power-on (default 10)")

    uart_p = sub.add_parser("uart", help="UART commands")
    uart_p.add_argument("uart_cmd", choices=["read", "send", "exec", "login", "wake"])
    uart_p.add_argument("uart_args", nargs="*")

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        sys.exit(1)

    dispatch = {"on": cmd_on, "off": cmd_off, "reboot": cmd_reboot, "status": cmd_status,
                "ssh": cmd_ssh, "edl": cmd_edl, "uart": cmd_uart}
    ret = dispatch[args.cmd](args)
    sys.exit(ret or 0)

if __name__ == "__main__":
    main()
