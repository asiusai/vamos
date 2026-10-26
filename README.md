# VamOS

The device OS for Asius v0, built around the Radxa Dragon Q6A and designed to
provide an AGNOS-compatible operational environment for openpilot without a
screen or Android userspace. See the [AGNOS parity matrix](docs/agnos-parity.md)
for equivalent capabilities, intentional hardware-specific differences, and
the release checklist.

[![fast](docs/fast.png)](https://discord.com/channels/469524606043160576/1262118077017882715/1482214461740683385)

## Usage

```bash
./vamos setup              # init submodules and udev rules
./vamos build kernel       # build Image and Dragon device tree
./vamos build system       # build the compressed EROFS system.img
./vamos build esp          # build the 256 MiB boot ESP
./vamos build ota          # build ESP and package a full OTA release
./vamos build disk         # build the common factory disk + optional userdata
./vamos build disk --storage ufs  # build the 4 KiB-sector UFS factory disk
./vamos devices            # list connected Dragons and USB speeds
./vamos flash              # prepared local VamOS + openpilot build
./vamos flash --no-openpilot # local VamOS alone
./vamos flash --remote 18.11 # a specific published release
./vamos flash --remote     # latest published VamOS + openpilot
./vamos uboot              # published U-Boot only; preserve OS and data
./vamos profile diff A B   # diff two rootfs profiles
```

Factory builds and EDL flashing support UFS modules with 4096-byte logical sectors.
UFS is the only flash target. Builds accept `--storage ufs` explicitly.
The signed QCS6490 programmer cannot safely probe unsupported storage backends.
CLI transfers default to conservative 64 KiB USB payloads; override that with
`VAMOS_EDL_MAX_PAYLOAD` when benchmarking a known-stable link.
Factory flashing, U-Boot installation, and `dragon.py normal` each reuse one Firehose connection.
Writes retain full readback verification. Failed operations stop the session
without automatic retries. See [the host tool](tools/flash/edl-ng/README.md).
Factory disks use a 64 GB minimum geometry and a UFS GPT;
`VAMOS_STORAGE_BYTES` remains available for builds that intentionally target a
different minimum capacity. On first boot,
the backup GPT, userdata partition, and ext4 filesystem expand to use the
remaining capacity of larger UFS media.

The sixth GPT entry reserves 64 MiB before userdata for `/persist`. Factory
flashing preserves this region. First boot generates `/persist/asius/identity.seed`
and mounts the filesystem read-only. Openpilot reads the key and derives its
recording-access key from it. A missing or damaged identity stops openpilot,
while local recovery access remains available.

Use `sudo vamos-identity status` to check identity storage. To import or replace
an identity, first stop openpilot with `sudo sv down /var/service/openpilot`.
Then run `sudo vamos-identity replace --from-file /path/to/key --confirm-identity-change`
and reboot. Omit `--from-file` to generate a new identity. This changes device
identity and recording recovery access. Retain the old key privately when needed.
Normal writes remain blocked by the read-only mount; root can deliberately
unlock it. This is not a hardware fuse or protection against physical EDL access.
Older five-partition installations need factory provisioning and explicit key
import once. OTA manifests require updater version 3, so earlier updaters refuse
the release before changing a slot. Never write a raw full-disk image over an existing identity.

Factory OS flashing preserves the connected SOM's boot firmware.
Run `vamos uboot` separately when U-Boot installation is needed.
It verifies the published recipe, saves a complete NOR backup, and rejects unknown firmware revisions before writing.
Only XBL changes. The complete NOR is verified before reset.

`build disk` also packages and verifies the reusable Firehose inputs for that
storage target. This work happens once per image build; `flash` reuses
the prepared chunks for every device. Set `VAMOS_SKIP_FLASH_BUNDLE=1` only when
building a disk artifact that will not be flashed locally.

### Standalone flashing

The `Flash CLI` GitHub Actions workflow builds `vamos-cli-linux-x86_64`.
Its archive contains one executable. Extract it and run `./vamos devices` or `./vamos flash --remote`.
It includes Python, release signature verification, libusb, and the native Firehose backend.
Users do not need the source checkout, source submodules, Docker, compilers, or Python package installation.
The current build targets Linux x86-64 on Ubuntu 24.04 or a compatible system.
Windows, macOS, and ARM hosts are not supported by this build.
USB permissions can require one sudo prompt.

`flash --remote` selects the same signed release as the browser. `flash --remote VERSION` selects a specific signed release.
Both download and cache verified image chunks and the matching programmer.
`flash` uses a prepared local factory manifest and its adjacent chunks. Use `--manifest PATH` to select another local manifest.
It does not rebuild images or require the original full-disk image.
The standalone executable defaults to `build/flash-ufs/flash.json` in the current directory.
The source wrapper defaults to that path in this repository.
Local and remote flashing accept `--no-openpilot`. Openpilot-only flashing is not supported.
Use `--yes` to confirm an unattended installation.

Flashing VamOS replaces recordings and settings. Device identity is preserved.
`uboot` preserves the installed OS, recordings, settings, and identity.
Downloads and NOR backups are stored under `$XDG_CACHE_HOME/vamos`, or `~/.cache/vamos` by default.
The final line shows elapsed time, average download speed, average write speed, and the USB link generation.
Write speed excludes verification. Elapsed time includes verification and reset, but not Linux startup.

For source development, use Python 3.12 or newer with `cryptography==50.0.2`.
Set `VAMOS_PYTHON` to that environment's Python executable.
The `devices` command and command help need no additional Python packages.
Build a standalone executable with `bash tools/build/build_cli.sh` after installing `tools/flash/requirements-build.txt` in a virtual environment.

Fresh devices accept `comma/comma` through physical USB NCM at `192.168.42.2` and UART.
Remote SSH requires an individual authorized key. There is no shared bootstrap key.
Users can change their password with `passwd`. Normal OS updates preserve this change.
The boot firmware
selects the external controller's role before Linux starts; use
`vamos-update usb-mode host|ncm` to persist the other role and reboot without
unsafe live switching.
Host tools use `~/.ssh/asius_operator_ed25519` by default. Set `DRAGON_SSH_KEY` to select another operator key.
Add its public key through local access or the app. Authorized keys are stored in `/data/params/d/GithubSshKeys`.

A running Dragon can enter Qualcomm EDL without the hardware button:

```bash
sudo reboot-edl
```

The command writes a one-shot request to both redundant A/B boot-control
records, syncs them, and reboots. U-Boot or the stock-UEFI selector consumes
and clears the request before loading Linux, then invokes the stock Qualcomm
EDL secure-call sequence. This keeps the request power-loss safe, preserves any
OTA trial state, and does not rely on secure calls that the Dragon firmware
rejects from Linux EL1.

On the Dragon development bench, `dragon.py` automates recovery over USB and
UART. It can also control a dock with real ganged VBUS switching:

```bash
./dragon.py edl                 # request reboot-edl over NCM
./dragon.py normal              # leave EDL over USB (UFS module)
./dragon.py normal --storage ufs # same command for a Dragon with UFS
./dragon.py dock status
./dragon.py dock on|off|cycle
```

The Dragon U-Boot also detects an upstream USB host at power-on and enters EDL
without the button wire. A connected PC triggers this through USB requests;
VBUS alone does not. `dragon.py normal` uses Firehose to set and verify a
one-shot normal request in both boot-control records, then resets over the
same USB cable. U-Boot consumes the request and boots Linux with the PC still
attached. UART, dock power switching, and the EDL wire are not needed. This
requires the updated Dragon U-Boot and readable A/B boot records. See
[bootloader recovery](bootloader/README.md) for the probe timeout and manual
recovery commands.

The Dragon U-Boot starts a hardware watchdog that Linux and the early userspace
supervisor take over. Stable boots and OTA trials must reach the common OS
health checks before it is disarmed. Failed trials roll back on the next normal
boot. `sudo vamos-boot status` shows the current and previous boot stages plus
available reset evidence; `/data/vamos-boot/` retains a bounded boot history.
See [boot supervision](bootloader/README.md#boot-watchdog-and-diagnostics) for
timeouts and coverage limits.

The default dock locations are `1-1` (USB 2) and `2-1` (USB 3). Override them
with `DRAGON_DOCK_USB2_HUB` and `DRAGON_DOCK_USB3_HUB`. The host tool assigns
`192.168.42.50/24` directly when a new random NCM MAC prevents the network
manager from reusing its previous connection.

## Device name

At boot, the console hostname comes from `/data/params/d/DeviceName`, normalized
to lowercase ASCII letters, digits and hyphens (maximum 63 characters). Missing
or entirely non-ASCII/punctuation names use `asius-v0`. Openpilot keeps the
hostname synchronized with subsequent parameter changes. The original display
name remains unchanged in the app and Bluetooth.

## Dragon hardware policy

`vamos-clock` restores the later of the image build time and the timestamp in
`/data/vamos-clock/state.json` before workloads start. Asius v0 does not depend
on an RTC or GPS. Board-supplied boot time is ignored, and no RTC is read or
written. The service checkpoints every 30 seconds and at shutdown. An abrupt
power cut can lose roughly the last checkpoint interval of running time.
While switched off, elapsed time is unknown. Chrony uses public NTS services to recover authenticated internet time.
An authorized app can also supply time through Bluetooth or the network.
NTS bootstrap skips certificate dates until the first clock update. It still checks certificate chains, signatures and hostnames.
Normal TLS connections always check certificate dates. Runtime
corrections step forward or slow the clock when it is ahead; they never step
backward. A recent NTS fix takes precedence over phone time.
`sudo vamos-clock status` shows the last source, correction and saved timestamp.
The runit service retains its `busybox-ntpd` name but runs `vamos-clock serve`.
Validate the policy with `python3 -m unittest discover -s tools -p test_time_sync.py`.

`vamos-hardware` owns Dragon-specific IRQ routing, GPU clocks and runtime power,
thermal trip overrides, and diagnostic device permissions. Openpilot calls
`sudo vamos-hardware initialize` from its hardware initialization hook and
`sudo vamos-hardware gpu-power-save on|off` when its power state changes. The
helper has no Openpilot Python dependency, and the same interface is available
to other workloads.

Initialization retains the existing Dragon policy; it does not run a second
time from a boot service. Openpilot still decides when to enter power save and
retains its existing CPU policy. Install a system image containing this helper
before updating to an Openpilot revision that delegates these operations to it.

The external USB host uses comma's receiver-detection suspend sequencing and
stuck-port recovery, adapted to Linux 7.2 in patch 0090 and the board DTS.
Dragon's external port selects TX mode 0 and QMP v4 tap 31; receiver calibration
and the internal USB controller retain their existing settings. See
[kernel/README.md](kernel/README.md) for the comparison and validation limits.
The matching [Openpilot firmware](https://github.com/asiusai/openpilot/tree/master/openpilot/system/hardware/chestnut)
provides autonomous bridge recovery. Normal reboot and shutdown are unchanged.

## Model acceleration and builds

Openpilot uses tinygrad's Qualcomm GPU backend (`QCOM`) on Dragon.
Chestnut uses `USB+AMD:LLVM`. The image does not include QNN, QAIRT,
ONNX Runtime, or the unused FastRPC userspace libraries.
The pinned ADSP and CDSP firmware remains available for board initialization and audio.

Clang builds openpilot on the device. LLVM compiles GPU and CPU kernels.
GCC, development headers, and the separate Panda ARM cross-compiler remain available.

The first system build clones the Asius `openpilot` `master` branch into
the gitignored `.openpilot/` checkout. Later builds fetch that ref and safely
realign the managed checkout, then update its submodules. Release builds set
`VAMOS_OPENPILOT_REF` to the resolved openpilot commit so the image is
reproducible even if `master` moves. System builds use it for openpilot dependencies.

Factory disks use a native ARM64 openpilot build with compiled Chestnut models.
Run `python3 tools/build/download_prebuilt.py` to download and verify the signed release.
Alternatively, put native `tools/release/build_release.sh` output at
`build/openpilot/openpilot-v0-chestnut.tar.xz`.
`VAMOS_OPENPILOT_ARCHIVE` selects another archive within this repository.
Disk builds produce a common `dragon.img` with blank userdata and an optional
`userdata-openpilot.img` with the flattened, prebuilt `master-ci` checkout. The manual flasher and flash.asius.ai can apply
that payload for a driving device or omit it for a general-purpose vamOS
device. No profile or role is recorded: executable `/data/continue.sh` is the
sole opt-in to the openpilot workload.

A driving device boots without downloading or compiling openpilot. A vamOS-only device gets the same
writable OS, A/B layout, and persistent `/data` and `/home`, with no
`/data/openpilot`. Interactive shells start in `/data/openpilot` when it exists
and otherwise remain in the persistent home directory. OTA packages use
`system.img`, which intentionally contains no userdata payload and cannot add,
replace, or remove openpilot or other persistent userspace files.

`system.img` is an EROFS filesystem. The 10 GiB OS partition layout stays
unchanged, but updates write and verify only the image bytes. EROFS payloads
transfer without another compression layer. Local update manifests reuse the
host's image hashes; the device checks them during writing and from storage.
The default uses Zstandard level 9, 64 KiB clusters and deduplication.
For compression experiments, set `VAMOS_EROFS_COMPRESSION=lz4hc,12` or
`VAMOS_EROFS_COMPRESSION=lzma,6` when running `./vamos build system`.
LZMA makes smaller images but increases cold-read and startup times.

EROFS requires the U-Boot firmware-loading change described in
[`bootloader/README.md`](bootloader/README.md). The old ext4-only U-Boot cannot
start an EROFS slot.
The first conversion requires a kernel with Zstandard support.
EDL can install the new kernel and OS together.

Before runit starts, OverlayFS adds a writable layer under `/data/vamos-root`.
Each EROFS image UUID selects its own layer. Reboot keeps local OS changes;
an OS update gets a new layer, and rollback restores the previous layer.
Shared `/data` files, identity, Wi-Fi settings, and the persistent home remain
independent of these layers. Build and debug tools remain usable.

Image builds remove selected dependency tests and debug sections after the final
Python dependency installation. Runtime testing helpers, headers, compilers and
debuggers remain available. Kernel modules retain their full symbols in
`build/kernel-out`; installed copies omit debug sections.

VamOS locks only its additional Python runtime dependencies. The managed
openpilot checkout supplies its own dependencies and lock file during image
assembly. Do not keep a second openpilot dependency snapshot in VamOS.

Clang and Mesa use the same LLVM 22 runtime. Raylib uses the system EGL, GLES
and DRM libraries, which support Dragon hardware rendering. Its bundled Mesa
copy lacks the Dragon driver and is removed. Vulkan drivers for unrelated GPUs
are also removed. `userspace/trim.py` owns this cleanup and uses LLVM strip.

The onboard hub driver loads before AIC8800 firmware. This prevents a late hub
reset from restarting the radio firmware download.

## Dragon Hardware Video Encoding

Dragon Q6A uses the mainline Qualcomm Venus V4L2 memory-to-memory driver. The
root filesystem includes `qcom/vpu-2.0/venus.mbn`; openpilot can pass the VFE's
NV12 DMA buffers directly to Venus without a CPU copy.

Venus also requires Linux to boot at EL2. vamOS checks for `/dev/kvm` during
boot and, when EL2 is unavailable, writes the Dragon UEFI one-shot
`HypervisorOverride` variable. It reboots only after any A/B trial has been
committed, and records the BIOS version so a failed override cannot cause a
reboot loop. The built-in Venus driver skips probing a Dragon that booted at
EL1: stock firmware can otherwise reset the board before userspace reaches
this recovery step. Venus initializes normally after the EL2 reboot, including
when booting through stock UEFI. `vamos-hypervisor status` reports the current
state, while `vamos-hypervisor enable` explicitly retries the override. The tested firmware
makes EFI variables unavailable after Linux has booted at EL2; the helper exits
before mounting efivarfs in that state. Dragon updates use the disk-resident
selector described below and never require `BootNext`.

## Dragon OTA Updates

Dragon installations use two complete boot slots and a shared userdata
partition:

| Partition | Size | Purpose |
| --- | ---: | --- |
| `esp_a` | 256 MiB | Slot A kernel and device tree |
| `rootfs_a` | 10 GiB | Slot A vamOS |
| `esp_b` | 256 MiB | Slot B kernel and device tree |
| `rootfs_b` | 10 GiB | Slot B vamOS |
| `userdata` | remainder | Persistent `/data` |

`vamos-update` always writes the inactive slot. It verifies raw SHA-256 hashes,
filesystems, OS version, rollback tooling, ARM64 EFI headers, and the device
tree before creating a one-shot trial boot. A fixed ARM64 selector exists at
both `\\Image` and `\\EFI\\BOOT\\BOOTAA64.EFI`; the slot kernel is stored at
`\\EFI\\vamos\\Image`. Redundant 1 KiB state blocks on both ESPs record the
stable slot, pending slot, generation, and immutable rootfs PARTUUIDs.

The selector marks a trial attempted before starting its kernel. Userspace
commits it only after runit reaches the launcher and disarms the hardware
watchdog. A failed or interrupted trial rolls back on the next boot. Because
all selection state is on the boot storage, EL2 mode, missing EFI variables,
firmware resets, and moving the media to another Dragon do not affect the
selected slot.

Install a signed HTTP manifest:

```bash
sudo vamos-update install https://dist.asius.ai/vamos/vamos.json --reboot
```

Install local recovery files without a manifest:

```bash
# Directory contains esp.img[.xz], system.img[.xz], and optional VERSION.
sudo vamos-update local /data/vamos-local --reboot
```

Openpilot stages the OS before finalizing its own overlay, then runs
`vamos-update activate` only after finalization. Manual tooling can use the
same two-phase flow:

```bash
sudo vamos-update install ./vamos.json --defer-activation
sudo vamos-update verify
sudo vamos-update activate --reboot
```

Remote manifests require an adjacent 3309-byte ML-DSA-65 signature at
`<manifest>.sig`. A local manifest is also verified when an adjacent signature
exists; unsigned manifests are accepted only from local storage for physical
recovery.

New factory flashes already contain this final five-partition layout. Existing
compact or legacy two-partition Dragon installations still migrate it
restartably on first boot. They can also be migrated manually after backing up
persistent data:

```bash
sudo vamos-update initialize --yes
sudo reboot
```

## OTA Publishing

Signed OTA manifests and content-addressed images use `https://dist.asius.ai`.
Openpilot selects the release through `openpilot/system/hardware/v0/vamos.json`.
VamOS 18.7 passed download, inactive-slot verification, activation, and boot commit
through openpilot on Asius 1. This release uses a new signing key.
Devices with the retired key need an explicit trust-key update or a factory flash.

The `build and publish vamOS images` workflow runs manually or for an exact
`vamos-v<VERSION>` tag. It builds the kernel, system image, and ESP on ARM64.
It downloads the signed native openpilot archive and builds UFS factory images.
It signs the OTA manifest and both factory manifests, then uploads objects to R2.
Factory manifests use `vamos/releases/<VERSION>/flash-ufs.json`. The signed `vamos.json` and
`vamos.json.sig` are retained both as a 90-day workflow artifact and as durable
assets on the matching GitHub source release. Finally, the workflow commits
those same files to the Asius openpilot fork.

Configure these repository secrets:

- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_ACCOUNT_ID`
- `R2_BUCKET`
- `VAMOS_UPDATE_SIGNING_KEY`
- `OPENPILOT_DEPLOY_KEY` (write deploy key for `asiusai/openpilot`)

Set `VAMOS_PUBLIC_URL` to the public R2 custom-domain origin, currently
`https://dist.asius.ai`.

## Kernel Patches

Linux 7.2.7, driver sources, retained patches and the upgrade audit are
documented in [kernel/README.md](kernel/README.md).

Patches in `kernel/patches/` are applied in order to the Linux kernel tree. They follow this naming convention:

```
NNNN-SUBSYSTEM-description.patch
```

- `NNNN` — sequential number, zero-padded (0001, 0002, …)
- `SUBSYSTEM` — the area of the kernel being modified:
  - `defconfig` — kernel configuration files
  - `dts` — device tree sources
  - `driver` — driver changes
  - `core` — core kernel subsystem changes
- `description` — short kebab-case summary of the change

Example: `0001-defconfig-add-vamos.patch`

## TODO

comma threex:
- [x] ufs
- [x] display
- [x] i2c
- [x] wifi
  - [ ] testing (set benchmarks, test case)
- [x] usb
- [x] modem
- [ ] sound
- [x] SPI
- [ ] GPS
- [ ] cameras (OX03C10)
  - [ ] kernel wiring
  - [ ] ISP
  - [ ] openpilot
- [x] graphics
  - [x] gpu
- [ ] Venus? (video encode/decode)

comma four:
- [x] ufs
- [x] display
- [x] i2c (IMU/temp/...)
- [x] wifi
  - [ ] testing (set benchmarks, test case)
- [x] usb
- [x] modem
- [ ] sound
- [x] SPI
- [x] GPS
- [ ] cameras (OS04C10)
  - [ ] kernel wiring
  - [ ] ISP
  - [ ] openpilot
- [x] graphics
  - [x] gpu
- [ ] Venus (video encode/decode)

openpilot support:
- [ ]

tinygrad support:
- [x] direct msm_drm compute (no OpenCL runtime)

validation:
- [ ] dmesg is clean (background in https://github.com/commaai/agnos-builder/issues/325)
- [ ] test_onroad passes
- [ ] testing closet
