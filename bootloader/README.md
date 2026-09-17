# Dragon Q6A U-Boot

Dragon Q6A does not use RB3 Gen 2's separate `uefi_a`/`uefi_b` layout. Its EDK2
core is a 0x9f000000 load segment inside the SPI NOR `XBL` image; `ImageFv` is
only a data firmware volume and is not the APPSBL entry point. vamOS patches
only that EDK2 segment while retaining the stock XBL_SEC, TrustZone and
hypervisor firmware. The complete XBL is then rehashed with Qualcomm's v6
format. Secure boot is disabled on the Dragon Q6A, so the qtestsign wrapper is
an image-format requirement rather than a security boundary.

Build with:

```sh
./vamos build uboot
```

The XBL-ready result is `build/u-boot/u-boot-xbl-core.bin`; it is
`u-boot.bin` linked at the Dragon XBL segment base, `0x9f000000`. The build starts from
`qcm6490_defconfig`, applies `configs/dragon-q6a.config`, and fails if the
core is larger than the stock EDK2 XBL segment. The device flash step combines
it with the exact XBL read from the module and emits
`build/u-boot-dragon-q6a-xbl.mbn`.

The flash step also extracts Radxa's XOR-obfuscated, Microsoft-signed Secure
Launch TCB boot application from that private stock-XBL backup, plus
`mssecapp.mbn` and the platform ACPI/CSRT package from the module's existing
firmware partitions. It validates and embeds them at a fixed gap after U-Boot.
The ACPI package is registered only for QHEE's pre-launch contract; Linux
continues to boot from device tree. The public source and ordinary build
artifacts therefore contain none of these proprietary payloads; TrustZone
authenticates the generated device payload before the EL2 handoff. Install the
`uefitool-cli` host package to provide `uefiextract`.

Flash with the device already in EDL, or let the command request EDL from a
running vamOS system:

```sh
./vamos flash uboot
```

The first flash saves the current XBL as `build/xbl-stock-dragon-q6a.bin`.
Later U-Boot upgrades validate and reuse the board-specific Secure Launch
firmware embedded by the first flash, so they do not require the original
stock XBL on the build host. Keep the stock backup for restoring UEFI.
Restore it with:

```sh
./vamos flash uboot --restore-stock
```

The fast path samples the UFS connector's active-low card-detect GPIO and
initializes that product variant's storage first. UFS devices therefore avoid
the absent PCIe link timeout, while NVMe devices avoid probing UFS. The other
backend remains a recovery fallback. U-Boot then reads the existing redundant
`EFI/vamos/grubenv` records, persists a trial attempt before Linux, and boots a
raw arm64 `Image` plus the slot DTB. This deliberately keeps the current OTA
state format readable by both U-Boot and the old GRUB/UEFI path during rollout.

OTA release manifests are signed independently of the bootloader image. Remote
installs reject an unsigned manifest; create the matching `vamos.json.sig` with
an Ed25519 release key by setting `VAMOS_OTA_SIGNING_KEY` or passing
`tools/build/package_ota.py --signing-key KEY.pem`. Development builds may emit
an unsigned local manifest and explicitly remove any stale signature.

Replacing XBL requires working EDL recovery. The bench flash command verifies
the stock backup and every programmed payload by reading the partition back
before resetting the device.

Software recovery crosses the kernel handoff through the redundant A/B
metadata. `/usr/bin/reboot-edl` writes and syncs a one-shot `edl_request` while
preserving the current stable or trial OTA state. U-Boot consumes and clears
the request with a higher generation before it invokes the proven SCM32 EDL
sequence. At a U-Boot prompt, `edl` remains available directly.
`dragon.py edl --software-only` tries the metadata path over NCM first and then
the bounded zero-delay U-Boot UART rescue window. When NCM is unavailable, the
UART path can power-cycle the board; it does not assert the hardware EDL input
or navigate the stock BIOS.

At normal autoboot, `edlprobe` briefly attaches the external USB controller as a
USB2 peripheral. A standard device-descriptor request from an upstream host
triggers the existing `edl` command without needing Linux. Before resetting,
U-Boot attempts to clear a pending one-shot EDL request on the selected storage
variant. This avoids leaving the usual software request pending for the next
normal boot. Missing storage or unreadable boot metadata does not prevent the
EDL reset. Connector VBUS alone does not trigger recovery. The probe waits up
to 750 ms after USB initialization, then releases the controller and continues
normal boot if no host is detected. Its USB2-only configuration does not
initialize the shared USB3/DisplayPort PHY or change the separate Linux device tree.

A PC connected at power-on therefore enters `05c6:9008` without the separate
EDL-button wire. This still depends on working early XBL and U-Boot; keep access
to the onboard EDL button for boot-firmware recovery. Other USB hosts can also
trigger it. The probe detects host traffic, not a particular computer identity.

To boot Linux while leaving the PC cable attached, use `./dragon.py normal`
from EDL, or `./dragon.py normal --storage ufs` for a UFS module. The command
uses only the EDL USB connection. Firehose locates the existing boot records
through their GPT/FAT metadata, writes `normal_request=1` with a higher
generation, verifies both copies, then resets. The backup is written first;
only sectors containing the 1024-byte records are changed, preserving their
surrounding bytes. A/B slot, trial state, root mappings, and USB role are retained.

On the next boot, `vamosboot ... usb-check` consumes and clears that request
before bypassing host-triggered EDL. It also clears any pending EDL request.
The consumed record gets another higher generation, so a stale redundant copy
cannot repeat the bypass. The following boot again detects a connected PC.
This needs the updated U-Boot and valid GPT, FAT, and boot records on both ESPs;
the host command refuses malformed metadata without attempting filesystem repair.
Normal NCM enumeration is checked after reset. A stored USB host role does not
expose NCM and must be changed separately if USB networking is wanted.

## Boot watchdog and diagnostics

U-Boot starts the Qualcomm hardware watchdog after relocation, before USB and
storage discovery, with a 30-second timeout. Its cooperative servicing stops
if execution hangs. The timer remains enabled across the Linux handoff; the
QCOM kernel driver adopts it. `watchdog.open_timeout=45` bounds the kernel's
servicing before userspace opens `/dev/watchdog0`. Earlier XBL and pre-relocation
U-Boot execution are outside this coverage.

Runit takes ownership immediately after mounting the pseudo-filesystems, before
filesystem checks and userdata migration. Both stable boots and OTA trials get
a 180-second userspace health deadline and a 30-second hardware timeout. The
existing stage-1, userdata and configured-workload checks must pass before the
watchdog is disarmed. An uncommitted trial rolls back on the next normal boot;
a stable-boot timeout resets the device without automatically promoting its
other slot. A connected USB host still selects EDL after a reset.

The redundant boot records retain a boot counter, slot, last stage and previous
boot's stage. Stages are `loading` (before DSP/kernel loads), `kernel` (before
handoff), `userspace` (after userdata setup), and `healthy`. They survive complete
power removal, unlike a RAM-only marker. Diagnostic writes require at least one
writable ESP. Entering EDL does not count as a Linux boot or change these stages,
and stops the watchdog so that recovery is not limited to 30 seconds.

`sudo vamos-boot status` shows the current report. `/data/vamos-boot/` retains
`latest.json`, `last-incomplete.json`, and a history bounded to 32 boots. An
incomplete stage establishes where progress stopped, not why power was lost.
Reset evidence includes the watchdog driver's `GETBOOTSTATUS` result and the
raw PMIC power-on status that XBL saved in SMEM item 403. Its layout is firmware
specific; missing data is reported as unavailable and zero watchdog flags do
not establish that no watchdog reset occurred. Additional EDL/Firehose resets
can replace hardware reset evidence before Linux starts.

At a U-Boot prompt, `vamosboot scsi 0 status` reads the persisted stages on UFS;
use `nvme 0` for NVMe. `vamosboot ... kernel` records the handoff stage and is
normally called by the boot script.

UART remains an optional diagnostic route: `run vamos_boot` at the U-Boot
prompt bypasses the probe directly. `edlprobe [timeout_ms]` returns success only
when it sees the host request and does not reset by itself. A bare Firehose
reset without the one-shot request returns to EDL while the PC is attached.
