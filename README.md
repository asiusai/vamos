# vamOS
a new operating system for comma 3X and comma four

[![fast](docs/fast.png)](https://discord.com/channels/469524606043160576/1262118077017882715/1482214461740683385)

## Usage

```bash
./vamos setup              # init submodules and udev rules
./vamos build kernel       # build Image and Dragon device tree
./vamos build system       # build the 10 GiB system.img
./vamos build esp          # build the 256 MiB boot ESP
./vamos build ota          # build ESP and package a full OTA release
./vamos build disk         # build an initial Dragon NVMe image
./vamos flash kernel       # replace the ESP through EDL
./vamos flash system       # replace the complete NVMe image through EDL
./vamos flash all          # flash system and kernel
./vamos profile diff A B   # diff two rootfs profiles
./vamos device-update comma@192.168.88.20
```

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
the gitignored `.openpilot/` checkout. Later builds fast-forward that checkout
or safely realign it after an intentional branch rewrite, then update its
submodules. System builds use it for openpilot dependencies, and disk
builds package it as a git-complete `/data/openpilot` checkout.
The device therefore boots without downloading openpilot and can use normal
Git commands afterward. OTA packages use `system.img`, which intentionally
contains no `/data/openpilot` payload and cannot replace the device's existing
openpilot checkout.

`device-update` packages the locally built `build/system.img` and
`build/esp.img`, rsyncs them directly to the device, and installs them into the
inactive vamOS A/B slot. It does not use GitHub, R2, an openpilot branch, or the
`VAMOS_VERSION` update gate. The image's existing `/VERSION` is used only for
post-write verification, so rebuilding and reinstalling the same version works.

## Dragon Hardware Video Encoding

Dragon Q6A uses the mainline Qualcomm Venus V4L2 memory-to-memory driver. The
root filesystem includes `qcom/vpu-2.0/venus.mbn`; openpilot can pass the VFE's
NV12 DMA buffers directly to Venus without a CPU copy.

Venus also requires `Hypervisor Settings -> Hypervisor Override` to be Enabled
in Dragon UEFI so Linux boots at EL2. Without EL2, starting an encoder session
can reboot the board. This setting is not changed automatically. The tested
firmware also makes EFI variables unavailable to Linux, so Dragon updates use
the disk-resident selector described below and never require `BootNext`.

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
all selection state is on the NVMe, EL2 mode, missing EFI variables, firmware
resets, and moving the drive to another Dragon do not affect the selected slot.

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

Newly flashed compact images automatically create the inactive slot and
persistent userdata on first boot. The migration is restartable after a power
loss. Legacy two-partition Dragon installations can also be migrated manually
after backing up persistent data:

```bash
sudo vamos-update initialize --yes
sudo reboot
```

## OTA Publishing

The `release vamOS to R2` GitHub workflow runs manually or for `vamos-v*`
tags. It builds the kernel, system image, and ESP on ARM64, creates
content-addressed XZ objects, signs `vamos.json`, and publishes to Cloudflare
R2. Configure these repository secrets:

- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_ACCOUNT_ID`
- `R2_BUCKET`
- `VAMOS_UPDATE_SIGNING_KEY`

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
