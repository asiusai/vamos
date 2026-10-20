# Persistent Firehose session

The shipped binary uses upstream `strongtz/edl-ng` v1.5.0,
commit `78f2078da4e4dc563daa7366f16bfb7015193966`, plus `session.patch`.
The patch adds sequential JSON requests over stdin and responses over stdout.
Existing transport logs go to stderr. It keeps the upstream Sahara and Firehose transport.

One session configures one storage backend and LUN 0.
Requests support read, write, erase, reset, and close.
The session validates storage bounds and exact write sizes.
Any error ends the session. It never reconnects or retries a write automatically.
The Python caller verifies writes with fresh full readback.

`dragon.py normal` and the system factory flasher use this connection.
Kernel-only and SPI bootloader flashing still use the existing CLI commands.
The USB payload limit remains 64 KiB unless `VAMOS_EDL_MAX_PAYLOAD` changes it.

Rebuild on Linux x64 with .NET SDK 9.0.319, Clang, and zlib development files:

```sh
DOTNET=/path/to/dotnet tools/flash/edl-ng/build.sh
python3 -m unittest discover -s tools/flash -p 'test_*.py'
```

The lock files fix the NuGet dependencies. The existing edl-ng license remains
in `tools/bin/edl-ng-dist/LICENSE`.

On 2026-10-20, Asius 1 used UFS, a 5 Gbit/s USB link, and 1 MiB payloads.
Updating a 624 MiB OS and 256 MiB ESP took 12.86 seconds through the reset command.
This includes full readback and both boot-control records. Userdata stayed intact.
The previous tool took 35.83 seconds with a 683 MiB OS and the same ESP size.
These measurements exclude boot time and factory userdata installation.
