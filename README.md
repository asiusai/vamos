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
tree before creating a one-shot EFI trial boot. The persistent EFI boot order
continues to point at the known-good slot until userspace reaches the launcher
and disarms the hardware watchdog. A failed or interrupted trial therefore
returns to the previous slot.

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

Legacy two-partition Dragon installations can be migrated in place after
backing up persistent data:

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
- [ ] i2c
  - [ ] TODO: look at device tree
- [x] wifi
  - [ ] testing (set benchmarks, test case)
- [x] usb
- [x] modem
- [ ] sound
- [ ] SPI
- [ ] GPS
- [ ] cameras (OX03C10)
  - [ ] kernel wiring
  - [ ] ISP
  - [ ] openpilot
- [ ] graphics
  - [ ] gpu
- [ ] opencl - via rusticl / msm_drm
- [ ] Venus? (video encode/decode)

comma four:
- [x] ufs
- [x] display
- [ ] i2c (IMU/temp/...)
- [x] wifi
  - [ ] testing (set benchmarks, test case)
- [x] usb
- [x] modem
- [ ] sound
- [ ] SPI
- [ ] GPS
- [ ] cameras (OS04C10)
  - [ ] kernel wiring
  - [ ] ISP
  - [ ] openpilot
- [ ] graphics
  - [ ] gpu
- [ ] opencl - via rusticl / msm_drm
- [ ] Venus (video encode/decode)

openpilot support:
- [ ]

tinygrad support:
- [ ] msm_drm

validation:
- [ ] dmesg is clean (background in https://github.com/commaai/agnos-builder/issues/325)
- [ ] test_onroad passes
- [ ] testing closet
