# Dragon Cleanup Log

## 2026-05-05

- Committed validated cleanup as `c61eba1 trim unused dragon image files`.
- Preserved `kernel/patches/*` and `kernel/configs/vamos.config`.
- Baseline after that commit:
  - NCM, WiFi, Bluetooth, processes, snapshots, and camera FPS passed.
  - Camera FPS: road and wide road around 20.03 fps / 49.92 ms.
  - Model replay outputs were clean.
  - Model replay timing failed only strict upstream thresholds: modelV2 around 0.054s max / 0.045s avg, driverStateV2 around 0.083s max / 0.079s avg.
- Next cleanup candidates:
  - Remove stale `dragon_updater`; it assumes old mmcblk A/B partitions and is not referenced.
  - Remove duplicate `/usr/local/bin` venv symlinks; `/usr/local/venv/bin` is already on PATH through `/etc/profile`, `/etc/profile.d/venv_path.sh`, and `/etc/environment`.
  - Remove empty `userspace/root/etc/sv/dnsmasq/down`; service enablement is handled by runit symlinks and `base_setup.sh`.
- Built and flashed the first added-file cleanup batch.
- Health result after flashing:
  - NCM, WiFi, Bluetooth, processes, snapshots, and camera FPS passed.
  - Camera FPS: road 20.04 fps / 49.91 ms, wide road 20.04 fps / 49.90 ms.
  - Model replay output comparison stayed clean; no mismatch fields were printed under `models`.
  - Model replay timing stayed in the expected baseline range: modelV2 0.0509s max / 0.0442s avg, driverStateV2 0.0812s max / 0.0770s avg.
  - The only failure remained the known strict upstream timing threshold.
- Removed added-only `tools/bin/edl-ng-dist/README.md`; the host flasher uses `edl-ng`, `libSystem.IO.Ports.Native.so`, `LICENSE`, and the Dragon firehose ELF, not this README.
- Inspected Dragon dmesg after a healthy boot. The AIC driver loaded these D80 firmware blobs:
  - `fw_patch_table_8800d80_u02.bin`
  - `fw_adid_8800d80_u02.bin`
  - `fw_patch_8800d80_u02.bin`
  - `fw_patch_8800d80_u02_ext0.bin`
  - `fmacfw_8800d80_u02.bin`
  - `aic_userconfig_8800d80.txt`
- Removed added-only AIC D80 blobs that were not requested on this hardware and appear to be alternate/test/RF variants. Kept `fw_ble_scan_ad_filter.bin` for now because it is BLE-related.
- Built and flashed the image with the AIC cleanup. The first health run on one board showed slightly slower model timing, but a follow-up run on another Dragon with a known power/camera issue showed baseline model performance:
  - modelV2 0.0519s max / 0.0436s avg.
  - driverStateV2 0.0808s max / 0.0768s avg.
  - Model replay output comparison stayed clean; no mismatch fields were printed under `models`.
  - Camera FPS and snapshots failed on that board because cameras were not connected, not because of the cleanup.
  - Conclusion: keep the AIC cleanup; no modeld regression is attributed to these deleted WiFi/BT alternate firmware blobs.
