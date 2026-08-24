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
Restore it with:

```sh
./vamos flash uboot --restore-stock
```

The fast path probes PCIe/NVMe first, reads the existing redundant
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
the bounded zero-delay U-Boot UART rescue window; it never falls through to a
power cycle in software-only mode.
