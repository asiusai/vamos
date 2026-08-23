# VamOS

The device OS for Asius v1, built around the Radxa Dragon Q6A and designed to
provide an AGNOS-compatible operational environment for openpilot without a
screen or Android userspace. See the [AGNOS parity matrix](docs/agnos-parity.md)
for equivalent capabilities, intentional hardware-specific differences, and
the release checklist.

[![fast](docs/fast.png)](https://discord.com/channels/469524606043160576/1262118077017882715/1482214461740683385)

## Usage

```bash
./vamos setup              # init submodules and udev rules
./vamos build kernel       # build Image and Dragon device tree
./vamos build system       # build the 10 GiB system.img
./vamos build esp          # build the 256 MiB boot ESP
./vamos build ota          # build ESP and package a full OTA release
./vamos build disk         # build the common factory disk + optional userdata
./vamos flash kernel --storage nvme  # replace an NVMe ESP through EDL
./vamos flash system --storage nvme  # factory flash NVMe with openpilot
./vamos flash system --storage ufs --without-openpilot  # vamOS-only UFS
./vamos flash all --storage nvme     # flash system and kernel
./vamos profile diff A B   # diff two rootfs profiles
./vamos device-update comma@192.168.88.20
```

EDL flashing defaults to Asius v1 NVMe and requires 512-byte logical sectors.
Select the hardware target with `--storage nvme|ufs`; `--nvme` and `--ufs` are
equivalent shortcuts. Low-level tooling can still use
`VAMOS_EDL_MEMORY=Nvme|Ufs`. The signed QCS6490 programmer hangs after configuring an
unsupported backend, so it cannot safely probe multiple storage types in one
EDL session.
CLI transfers default to conservative 64 KiB USB payloads; override that with
`VAMOS_EDL_MAX_PAYLOAD` when benchmarking a known-stable link.
Factory disks use a 64 GB minimum NVMe/UFS geometry so one signed image also
fits larger supported media; `VAMOS_STORAGE_SECTORS` remains available for
builds that intentionally target a different minimum capacity. On first boot,
the backup GPT, userdata partition, and ext4 filesystem expand to use the
remaining capacity of larger NVMe or UFS media.

Fresh devices authorize the intentionally shared `tools/ssh/comma_setup.b64` key
once when no SSH keys have been provisioned, allowing initial access as
`comma@192.168.42.2` over USB NCM or through another reachable network path.
Host tools use that checked-in key by default; set `DRAGON_SSH_KEY` or pass a
tool-specific identity option to use an operator key. The bootstrap key can be
replaced in `/data/params/d/GithubSshKeys`.

A running Dragon can enter Qualcomm EDL without the hardware button:

```bash
sudo reboot-edl
```

The command syncs buffered writes and requests the QCS6490 firmware's `edl`
PSCI reset mode. A Firehose reset returns to normal boot only when the EDL input
is no longer asserted; release the recovery button or USB-VBUS pull-down first.

On the Dragon development bench, `dragon.py` automates the complete transition
through a USB hub with real ganged VBUS switching:

```bash
./dragon.py edl                 # request reboot-edl over NCM
./dragon.py normal              # delayed Firehose reset with dock VBUS off
./dragon.py dock status
./dragon.py dock on|off|cycle
```

The default dock locations are `1-1` (USB 2) and `2-1` (USB 3). Override them
with `DRAGON_DOCK_USB2_HUB` and `DRAGON_DOCK_USB3_HUB`. The host tool assigns
`192.168.42.50/24` directly when a new random NCM MAC prevents the network
manager from reusing its previous connection.

## Dragon NPU

The Dragon image includes the pinned QCS6490 cDSP firmware userspace, FastRPC
libraries, QAIRT v68 HTP runtime, and the QNN-enabled ONNX Runtime wheel. The
firmware-side unsigned shell must remain in `/usr/lib/dsp`; FastRPC checks that
canonical path before `ADSP_LIBRARY_PATH`.

After a cold boot, verify both the raw cDSP transport and a quantized HTP tensor
operation (with CPU fallback disabled) using:

```bash
qnn-smoke
```

On Asius hardware, modeld uses the Qualcomm MSM GPU for the camera warp and runs
the quantized vision network and recurrent temporal policy through QNN/HTP.
ONNX Runtime retains only four fixed-shape `Reshape` nodes as CPU bookkeeping;
all learned model arithmetic runs on HTP. The openpilot model build selects this
configuration with `MODELD_DEV=QNN` (the Asius default), or the full tinygrad MSM
backend with `MODELD_DEV=QCOM`.

The first system or disk build clones the Asius `openpilot` `master` branch into
the gitignored `.openpilot/` checkout. Later builds fetch that ref and safely
realign the managed checkout, then update its submodules. Release builds set
`VAMOS_OPENPILOT_REF` to the resolved openpilot commit so the image is
reproducible even if `master` moves. System builds use it for openpilot
dependencies. Disk builds produce one common `dragon.img` with blank userdata
and a separate `userdata-openpilot.img` payload containing a Git-complete
`/data/openpilot` checkout. The manual flasher and flash.asius.ai can apply
that payload for a driving device or omit it for a general-purpose vamOS
device. No profile or role is recorded: executable `/data/continue.sh` is the
sole opt-in to the openpilot workload.

A driving device therefore boots without downloading openpilot and performs
only the normal local openpilot build. A vamOS-only device gets the same
writable OS, A/B layout, and persistent `/data` and `/home`, with no
`/data/openpilot`. Interactive shells start in `/data/openpilot` when it exists
and otherwise remain in the persistent home directory. OTA packages use
`system.img`, which intentionally contains no userdata payload and cannot add,
replace, or remove openpilot or other persistent userspace files.

`device-update` packages the locally built `build/system.img` and
`build/esp.img`, rsyncs them directly to the device, and installs them into the
inactive vamOS A/B slot. It does not use GitHub, R2, an openpilot branch, or the
`VAMOS_VERSION` update gate. The image's existing `/VERSION` is used only for
post-write verification, so rebuilding and reinstalling the same version works.

## Dragon Hardware Video Encoding

Dragon Q6A uses the mainline Qualcomm Venus V4L2 memory-to-memory driver. The
root filesystem includes `qcom/vpu-2.0/venus.mbn`; openpilot can pass the VFE's
NV12 DMA buffers directly to Venus without a CPU copy.

Venus also requires Linux to boot at EL2. vamOS checks for `/dev/kvm` during
boot and, when EL2 is unavailable, writes the Dragon UEFI one-shot
`HypervisorOverride` variable. It reboots only after any A/B trial has been
committed, and records the BIOS version so a failed override cannot cause a
reboot loop. `vamos-hypervisor status` reports the current state, while
`vamos-hypervisor enable` explicitly retries the override. The tested firmware
makes EFI variables unavailable after Linux has booted at EL2; the helper exits
before mounting efivarfs in that state. Dragon updates use the disk-resident
selector described below and never require `BootNext`.

Build the images separately, then deploy them. The device reboots into a
one-shot trial of the new slot:

```bash
./vamos build system
./vamos build esp
./vamos device-update comma@192.168.88.20
```

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
sudo vamos-update install https://updates.asius.ai/vamos/vamos.json --reboot
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

Remote manifests require an adjacent 64-byte Ed25519 signature at
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

The separate `build and publish vamOS images` workflow runs manually or for an
exact `vamos-v<VERSION>` tag. It builds the kernel, system image, and ESP on
ARM64, creates content-addressed XZ objects, signs `vamos.json`, and publishes
the device-facing objects to Cloudflare R2. The signed `vamos.json` and
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
`https://updates.asius.ai`.

## Kernel Patches

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
