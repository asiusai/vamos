# AGNOS parity for Asius v0

VamOS should present the same operational contract to openpilot and an operator
as AGNOS, while using the hardware-native implementation for the Radxa Dragon
Q6A. The 2026-11-12 source review compares AGNOS 20.2 (`bfe703c`) with
AGNOS 20.4 and `commaai/agnos-builder` master at `5e21d5c`.
Source review does not establish hardware validation.

## Functional matrix

| Capability | AGNOS | VamOS / Asius v0 |
| --- | --- | --- |
| Service supervision | systemd | runit, with a small `systemctl` compatibility shim for operator/openpilot workflows |
| Background resource isolation | CPU, I/O, memory, and task policy using mixed cgroup controllers | Native cgroup v2 service limits, task I/O priority, and authenticated session promotion |
| Immutable OS | read-only root with RAM-backed mutable state | read-only root with tmpfs `/var` and `/tmp`; `/home` binds persistent `/data/home` when userdata is available |
| Persistent state | userdata mounted at `/data` | userdata mounted at `/data` |
| Rollback-safe OS update | A/B partitions and `abctl` | signed A/B updater, one-shot trial boot, hardware-watchdog rollback, and redundant on-disk state |
| USB development link | ADB | SuperSpeed USB NCM at `192.168.42.2`, selected before Linux probes the controller |
| Wi-Fi | NetworkManager | NetworkManager |
| SSH control | `SshEnabled` parameter watcher | compatible `SshEnabled` directory watcher; safe across atomic Params replacement |
| USB control | `AdbEnabled` parameter watcher | Redundant A/B `usb_mode` state selects host or NCM at the next boot |
| Local discovery | Avahi SSH publication | compatible Avahi SSH publication |
| Clock sync | systemd-timesyncd | chrony with authenticated NTS; GPS and authenticated app time provide additional inputs |
| Logs | rsyslog, journald, hourly logrotate; varwatch removed | rsyslog, bounded `journalctl` shim, hourly logrotate, native varwatch |
| Runaway-power watchdog | `power_monitor` | compatible `power_monitor` and `/var/tmp/power_watchdog` contract |
| Setup | touchscreen installer | preinstalled openpilot checkout, with network recovery when the checkout is missing |
| Factory reset | formats userdata, then touchscreen setup | explicitly requested early-boot reset erases user state and restores the installed openpilot checkout |
| Display | DRM display service, backlight, UI | v0 is screenless and reports zero screen brightness |
| User feedback | display and audio | device LEDs, speech, and the Asius app |
| Remote access extras | none by default | Tailscale and GitHub runner are opt-in and remain down until persistently configured |
| Model acceleration | Qualcomm GPU on comma hardware | tinygrad MSM on Dragon, or Chestnut (`USB+AMD:LLVM`); no OpenCL or QNN runtime |

## AGNOS 20.4 changes

| Change | VamOS assessment |
| --- | --- |
| Background CPU, I/O, memory, and process limits (`f51a901`) | Implemented with native cgroup v2, runit restart protection, and PAM session promotion. See the policy and bench results below. |
| Sensor and encoder work moves off background CPUs | Default IRQs use CPUs 0-1. Dragon Venus and camera I2C use CPU 2; Panda SPI uses CPU 3. Openpilot assigns its sensor GPIO IRQ to CPU 2. |
| Faster OTA hashing (`d0c5415`) | Already covered by 4 MiB reads in `tools/build/package_ota.py`. Android sparse-image parsing does not apply to the EROFS/ESP payloads. |
| Boot consolidation and udev removal | Retain VamOS udev and board-specific boot ordering. AGNOS input hotplug permissions repair its udev removal and need no equivalent replacement here. |
| Boot CPU frequency cap removed (`56acf43`) | Do not copy SDM845 frequency values to Dragon. Keep the current Dragon thermal and power policy. |
| varwatch removed (`4c0837a`) | Retain VamOS log-space protection until an equivalent bounded-log policy is verified. |
| SDM845 kernel update (`8d6c7c0`) | Modem/PIL and icnss restart fixes target vendor drivers absent from Dragon's mainline stack. The tested Dragon already exposes the cgroup v2 `pids` controller. |
| Nuitka setup and display applications | Screenless setup remains separate. Openpilot's updater packaging changes arrive through the openpilot rebase. |

AGNOS uses mixed cgroup v1/v2 controllers for its older kernel.
VamOS uses native cgroup v2 and retains openpilot's existing cgroup cleanup.

### Resource policy

`vamos-resources` configures the hierarchy before runit starts normal services.
Runit supervises each process outside its resource group, so exhaustion does not kill the restart mechanism.

| Group | CPU and I/O policy | Memory and task limits |
| --- | --- | --- |
| Background services combined | CPUs 0-1, CPU weight 6, idle I/O | 512 MiB, no swap, 512 tasks |
| Each background service or logger | Inherits the background CPU policy | 128 MiB, 128 tasks, complete group killed on OOM |
| Openpilot | All online CPUs, weight 100, best-effort I/O priority 4, OOM adjustment -500 | No background limits |
| Authenticated SSH/login sessions and exempt services | All online CPUs, weight 100, best-effort I/O priority 4 | No background limits |

Tasks include threads. Empty foreground CPU sets inherit cores brought online after power saving.
Exempt services are openpilot's supervisor, the opt-in build runner, serial gettys, boot commit, and hypervisor reboot.
Early boot daemons, including udev and the boot watchdog, remain outside these service groups.
PAM promotes authenticated sessions without changing authentication or moving the listening SSH process.

The Dragon's mq-deadline scheduler uses task I/O priorities.
Its exposed BFQ weights are inactive, so this policy does not promise proportional I/O bandwidth.
`/tmp` and `/dev/shm` allow 1,048,576 inodes while retaining their byte limits.

Setup failures create `/run/vamos-resources-error` and keep recovery access available.
Openpilot startup and boot-health commitment require a healthy policy.
A failed trial retains its rollback watchdog; the boot-commit command cannot bypass this check.

### Resource validation, 2026-11-12

Host validation passed 65 resource, hardware, and updater tests. The ARM userspace image built and passed inspection.
Four Dragon kernel tests verified CPU inheritance, foreground CPU expansion, memory containment, restart, and task limits.
Actual runit restarted a disposable service after its 160 MiB allocation exceeded the 128 MiB limit.
SSH sessions escaped the bounded listener group after authentication.
Tailscale, Bluetooth, chrony, clock, varwatch, and SSH services remained within their limits during the bench check.

With four CPU workers in the background group, 26 sensor, MSM GPU, encoder, and audio-timing tests passed without skips.
These checks include camera encoding and segment rotation, but do not establish vehicle performance or an I/O throughput improvement.
The temporary bench overlay was restored and verified. No OS image was flashed or rebooted for this check.
Full image boot, A/B trial and rollback, ignition cycles, and sustained vehicle operation remain release checks.

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
