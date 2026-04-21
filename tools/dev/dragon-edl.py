#!/usr/bin/env python3
"""Drive Dragon Q6A from BIOS into EDL mode via UART navigation.

Sequence (confirmed working):
    1. power off
    2. power on
    3. wait for BIOS (serial output starts)
    4. press F2 → drops into main menu
    5. arrow-down 8x → lands on "Reboot into EDL/9008"
    6. Enter

Usage:
    dragon-edl.py              # power-cycle + nav (default)
    dragon-edl.py --no-cycle   # skip power cycle, assume BIOS already up
"""
import argparse, os, re, subprocess, sys, time
try:
    import serial
except ImportError:
    sys.exit("pyserial not installed — run: pip3 install --user pyserial")

PORT = os.environ.get("DRAGON_UART", "/dev/ttyUSB0")
BAUD = 115200
HWMON_PWM = "/sys/class/hwmon/hwmon5/pwm1"
HWMON_PWM_EN = "/sys/class/hwmon/hwmon5/pwm1_enable"

F2 = b"\x1bOQ"                # VT100 F2 keycode
DOWN = b"\x1b[B"
ENTER = b"\r"
ESC = b"\x1b"

def power(state):
    subprocess.run(["sudo", "sh", "-c",
        f"echo 1 > {HWMON_PWM_EN} && echo {state} > {HWMON_PWM}"], check=True)

def wait_for_edl_usb(timeout=30):
    for _ in range(timeout):
        r = subprocess.run(["lsusb"], capture_output=True, text=True)
        if "05c6:9008" in r.stdout:
            return True
        time.sleep(1)
    return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-cycle", action="store_true",
                    help="Skip power cycle (assume BIOS is already at main menu)")
    ap.add_argument("--f2-wait", type=float, default=10.0,
                    help="Seconds to wait after power-on before pressing F2 (default 10)")
    args = ap.parse_args()

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
        # F2 during early UEFI drops into main menu. Spam F2 for a few seconds
        # to catch whenever UEFI is ready to receive it.
        print(f"[edl] pressing F2 for {args.f2_wait:.0f}s to enter setup")
        end = time.time() + args.f2_wait
        while time.time() < end:
            s.write(F2)
            s.flush()
            time.sleep(0.4)
        # Drain
        s.read(200000)
        time.sleep(1)

    # Navigate: 7 down arrows to land on "Reboot into EDL/9008", then Enter
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

if __name__ == "__main__":
    sys.exit(main())
