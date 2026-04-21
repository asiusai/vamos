#!/usr/bin/env python3
"""Non-interactive Dragon Q6A UART helper for Claude.

Usage:
  dragon-uart.py read [SECONDS]           # read for SECONDS (default 3)
  dragon-uart.py send 'cmd\\n'             # send string (supports \\n/\\r/\\t)
  dragon-uart.py exec 'cmd'  [SECONDS]    # send 'cmd\\n', collect output (default 3s)
  dragon-uart.py login  PASSWORD          # run root login sequence
  dragon-uart.py wake                     # poke with newline, print what comes back

UART: /dev/ttyUSB0 @ 115200 8N1. Dragon has serial-getty on ttyMSM0.
"""
import codecs, os, re, select, sys, termios, time
try:
    import serial
except ImportError:
    sys.exit("pyserial not installed — run: pip3 install --user pyserial")

PORT = os.environ.get("DRAGON_UART", "/dev/ttyUSB0")
BAUD = 115200

def open_port():
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
            end = time.time() + 0.4   # extend while data arriving
    if not echo:
        return strip_ansi(bytes(buf).decode('utf-8', 'replace'))

def cmd_read(argv):
    secs = float(argv[0]) if argv else 3.0
    with open_port() as s:
        drain(s, secs)

def cmd_send(argv):
    if not argv:
        sys.exit("send: need string")
    data = codecs.decode(argv[0], 'unicode_escape').encode('utf-8', 'replace')
    with open_port() as s:
        s.write(data)
        s.flush()

def cmd_wake(argv):
    with open_port() as s:
        s.write(b'\r\n')
        s.flush()
        drain(s, 2.0)

def cmd_exec(argv):
    if not argv:
        sys.exit("exec: need command")
    command = argv[0]
    secs = float(argv[1]) if len(argv) > 1 else 3.0
    with open_port() as s:
        # clear anything pending
        drain(s, 0.3, echo=False)
        s.write(command.encode('utf-8') + b'\r\n')
        s.flush()
        drain(s, secs)

def cmd_login(argv):
    if not argv:
        sys.exit("login: need password")
    pw = argv[0]
    with open_port() as s:
        # poke and see where we are
        s.write(b'\r\n')
        out = drain(s, 2.0, echo=False)
        sys.stdout.write(out); sys.stdout.flush()
        if '#' in out.splitlines()[-1:] and True:
            print("\n[already logged in as root]")
            return
        if 'login:' in out:
            s.write(b'root\r\n')
            drain(s, 2.0)
        if 'Password' in out or 'password' in out.lower():
            s.write(pw.encode() + b'\r\n')
            drain(s, 3.0)
        else:
            # also try login line once more
            s.write(b'root\r\n')
            drain(s, 2.0)
            s.write(pw.encode() + b'\r\n')
            drain(s, 3.0)

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd, argv = sys.argv[1], sys.argv[2:]
    {
        'read': cmd_read, 'send': cmd_send, 'exec': cmd_exec,
        'login': cmd_login, 'wake': cmd_wake,
    }.get(cmd, lambda _: sys.exit(f"unknown cmd: {cmd}"))(argv)

if __name__ == '__main__':
    main()
