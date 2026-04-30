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
  dragon.py uart read [SECONDS]       Read UART for N seconds (default 3)
  dragon.py uart send 'string'        Send string (supports \\n/\\r/\\t)
  dragon.py uart exec 'cmd' [SECONDS] Send cmd, collect output
  dragon.py uart login PASSWORD       Root login sequence
  dragon.py uart wake                 Poke with newline, print response
"""
import argparse, codecs, os, re, subprocess, sys, time

HWMON_PWM = "/sys/class/hwmon/hwmon5/pwm1"
HWMON_PWM_EN = "/sys/class/hwmon/hwmon5/pwm1_enable"
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
    subprocess.run(["sudo", "sh", "-c",
        f"echo 1 > {HWMON_PWM_EN} && echo {state} > {HWMON_PWM}"], check=True)

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
    r = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True)
    if "192.168.42." in r.stdout:
        return
    iface = find_ncm_interface()
    if not iface:
        sys.exit("No Dragon NCM interface found — is the USB cable connected?")
    print(f"[ncm] bringing up {iface}")
    subprocess.run(["sudo", "ip", "link", "set", iface, "up"], check=True)
    subprocess.run(["sudo", "dhcpcd", "-1", iface], check=True,
                   capture_output=True, text=True)

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

    dispatch = {"on": cmd_on, "off": cmd_off, "reboot": cmd_reboot,
                "ssh": cmd_ssh, "edl": cmd_edl, "uart": cmd_uart}
    ret = dispatch[args.cmd](args)
    sys.exit(ret or 0)

if __name__ == "__main__":
    main()
