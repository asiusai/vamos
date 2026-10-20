# AGNOS parity for Asius v0

VamOS should present the same operational contract to openpilot and an operator
as AGNOS, while using the hardware-native implementation for the Radxa Dragon
Q6A. This comparison uses AGNOS 19.5 on a comma four and the public
`commaai/agnos-builder` source at commit `8f7207a` as the reference.

## Functional matrix

| Capability | AGNOS | VamOS / Asius v0 |
| --- | --- | --- |
| Service supervision | systemd | runit, with a small `systemctl` compatibility shim for operator/openpilot workflows |
| Immutable OS | read-only root with RAM-backed mutable state | read-only root with tmpfs `/var`, `/tmp`, and an ephemeral `/home` overlay |
| Persistent state | userdata mounted at `/data` | userdata mounted at `/data` |
| Rollback-safe OS update | A/B partitions and `abctl` | signed A/B updater, one-shot trial boot, hardware-watchdog rollback, and redundant on-disk state |
| USB development link | ADB | SuperSpeed USB NCM at `192.168.42.2`, selected before Linux probes the controller |
| Wi-Fi | NetworkManager | NetworkManager |
| SSH control | `SshEnabled` parameter watcher | compatible `SshEnabled` directory watcher; safe across atomic Params replacement |
| USB control | `AdbEnabled` parameter watcher | Redundant A/B `usb_mode` state selects host or NCM at the next boot |
| Local discovery | Avahi SSH publication | compatible Avahi SSH publication |
| Clock sync | systemd-timesyncd | BusyBox NTP under runit; numeric peers avoid DNS bootstrap dependencies, image build time repairs implausible RTC dates, and successful sync is saved to the RTC |
| Logs | rsyslog, journald, hourly logrotate, varwatch | rsyslog, bounded `journalctl` shim, hourly logrotate, native low-overhead varwatch |
| Runaway-power watchdog | `power_monitor` | compatible `power_monitor` and `/var/tmp/power_watchdog` contract |
| Setup | touchscreen installer | preinstalled openpilot checkout, with network recovery when the checkout is missing |
| Factory reset | formats userdata, then touchscreen setup | explicitly requested early-boot reset erases user state and restores the installed openpilot checkout |
| Display | DRM display service, backlight, UI | intentionally absent; v1 is screenless and always reports zero screen brightness |
| User feedback | display and audio | device LEDs and the Asius app |
| Remote access extras | none by default | Tailscale and GitHub runner are opt-in and remain down until persistently configured |
| Model acceleration | Qualcomm GPU/OpenCL on comma hardware | tinygrad Qualcomm GPU (`QCOM`) or Chestnut (`USB+AMD:LLVM`), with no OpenCL or QNN runtime |

The headless setup and reset behavior intentionally differs from AGNOS. A
screenless device must not format away its only installer and then wait for UI
input that cannot be provided. Reset is therefore guarded by an explicit marker,
the mounted userdata device is verified, the installed checkout is returned to
its current commit, and state outside that checkout is erased.

## Hardware-specific omissions

The final image excludes services, firmware, and libraries that only support
comma's SDM845 display, modem, audio, Android compatibility layer, or remote
processor layout. In particular, it does not ship the ADB or OpenCL runtime.
The Dragon retains DSP firmware for board initialization and audio, and `libtinymesa.so` for GPU inference.
The unused QNN/QAIRT and FastRPC userspace libraries are removed.

The image also omits inactive compiler payloads that are not used by openpilot's
on-device build (`gnat1`, `c-index-test`, GDB's generic ARM simulator, and
`lto-dump`). C/C++, Clang, SCons, CMake, GDB, Git, Git LFS, and the Python build
environment remain available for normal development.

## Release validation

Image-only validation is necessary but not sufficient. Before a VamOS release
is installed in a car:

- Build the complete system image and confirm tinygrad MSM loading,
  compiler availability, log rotation, service links, and
  dynamic-library dependencies from the resulting rootfs.
- On a bench Dragon, verify Wi-Fi, Bluetooth, NCM/SSH, time sync, Avahi,
  ignition-off power save, clean boot logs, both A/B trial and rollback paths,
  and recovery setup. Exercise factory reset only on a recoverable test device.
- In the car, run openpilot's `test_onroad`, ignition cycles, all three camera
  streams, CAN/panda integration, video encoding, thermal behavior, and route
  logging/upload.
- Replay a captured route on the selected GPU backend. Record frame latency,
  dropped frames, CPU use, memory, and temperature after warm-up.

No release should be merged solely from a successful image build; the final
in-car checklist is intentionally required.
