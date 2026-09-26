# VamOS kernel

`linux/` pins Linux 7.2.7 from the official stable repository. Build it with
`./vamos build kernel`. The build applies the product patches to that exact
commit and merges `configs/vamos.config` over arm64 defconfig.

Keep hardware policy in userspace and board wiring in the device tree. Retain
driver changes only when the supported product needs behavior absent upstream.
The external USB PHY uses the mainline lane mapping. Transmit calibration is
selected in the board device tree: mode 0 and QMP v4 tap 31 on the external
Dragon port. The shared SoC defaults and internal USB controller are unchanged.

## Patch scope and provenance

The 6.18 series had 57 patches. The 7.2 series consolidates the remaining changes
by subsystem. Original authors remain credited in patch headers and source.

| Patch | Purpose and previous patches |
| --- | --- |
| 0016 | Qualcomm vendor PSCI reset modes, from Elliot Berman |
| 0020 | SC7280 UFS Gear 4 initialization, from Xilin Wu |
| 0021 | PCIe1 bandwidth/OPPs, from Xilin Wu; `sc7280.dtsi` is now `kodiak.dtsi` |
| 0037 | RPMh resource/regulator readback, from Maulik Shah and Kamal Wadhwa (0037, 0038) |
| 0042 | Removable UFS detection from Xilin Wu, plus device temperature support (0042, 0084) |
| 0043 | Drain internal UFS commands before clock-scaling quiesce; fixes a 7.2.7 health-read deadlock |
| 0044 | Keep internal UFS recovery commands dispatchable, from Stanley Jhu's upstream v3 proposal |
| 0054 | Asius firmware memory map, peripherals, static USB wiring, TX calibration and recovery properties (0031, 0032, 0054, 0081-0083, 0092) |
| 0055 | Dragon QSEECOM allowlist |
| 0057 | OS04C10 sensor, VFE170 PIX/NV12 capture and camera clocks |
| 0058 | Root command-line fallback for stock EFI boot |
| 0059 | Held power-key handling |
| 0060 | Venus IDR period, realtime sessions, scaling and EL1 recovery (0060, 0061, 0064, 0085) |
| 0062 | SOM identity and boot LED trigger |
| 0063 | Privileged Adreno compute submissions |
| 0065 | Remoteproc retry and boot-firmware handoff, from Xilin Wu and Stephan Gerhold (0023, 0065-0070) |
| 0071 | SMP2P boot-firmware takeover and bounded entry handling, from Stephan Gerhold (0071-0075) |
| 0077 | Stock-EFI device-tree peripheral setup (0077, 0079, 0080) |
| 0078 | Panda MI2S audio routing |
| 0086-0088 | Optional board USB transmit calibration: replace the DWC3 mode field and apply a validated QMP v4 tap before startup |
| 0090 | DWC3 receiver detection and stuck SuperSpeed recovery (0090, 0091), adapted from the AGNOS behavior |

The upstream Dragon DTS uses a different firmware memory layout. Its board
support replaces the old full-board addition, but the Asius carveouts and
U-Boot/stock-EFI handoff remain necessary. Camera aliases are board-local. Keep
the established camera buses 16, 18 and 20: Openpilot identifies sensors by
these names. Bus 20 explicitly maps to `cci1_i2c1`; the old duplicate bus-19
alias for `cci1_i2c0` is removed. Disable upstream header `i2c0`: it owns
GPIO0, which Asius uses for the IMU interrupt. The IMU I2C controller remains
`i2c6` through the existing userspace `/dev/i2c-1` link.
Firmware gap reservations exclude named
`no-map` regions: overlapping `/memreserve/` entries prevent
upstream remoteproc from requesting the DSP regions on 7.2. The complete
firmware memory range remains reserved. The build checks the compiled DTB
for these overlaps before packaging it.

UFS internal commands now share the host tagset. Patch 0043 orders the
clock-scaling write lock before queue quiescing, so a health read holding
the read lock can finish. Patch 0044 separately preserves recovery command
dispatch, following the [upstream proposal](https://lore.kernel.org/r/20260912131625.2301486-1-stanleyjhu@google.com).
Retest concurrent health queries, I/O and frequency changes when replacing
these patches with a later stable kernel. Neither patch disables scaling.

## Removed patches

| Previous patches | Reason |
| --- | --- |
| 0010 | Static QMP USB lane mapping is upstream |
| 0053 | Correct DWC3 xHCI register mapping is upstream |
| 0076 | SMP2P IRQ line-state query is upstream; retain upstream error handling |
| 0011, 0016-wifi, 0017 | ath10k is unused on Dragon; the onboard radio is AIC8800D80 USB |
| 0012 | No VamOS squashfs mount requests the unsupported `discard` option |
| 0029 | QSPI flash remains disabled because Linux probing it resets the board; boot firmware owns it |
| 0035 | Extra GENI `MODULE_FIRMWARE` metadata is unnecessary for the explicitly packaged/built-in firmware |
| 0047, 0049, 0052 | Display/HDMI-only patches; Asius v0 is headless |

The unused ath10k firmware, IMX577 and KVM overlay files from the old board
patch are also gone.
The product DTS and kernel configuration supply the required settings directly.

## Wireless modules

`aic8800/` pins Radxa's driver and its packaging patches, including the 7.2 API
updates and USB suspend/reboot fix. `tools/build/build_aic8800.sh` applies that
series in a disposable build directory and builds the USB Wi-Fi and firmware
loader against the same kernel configuration and symbol versions as the image.
The source checkout stays clean. Firmware remains under
`kernel/firmware/aic8800_fw/USB`.

Bluetooth uses the in-tree `btusb` driver. No prebuilt `.ko`, forced module load,
or external `aic_btusb` module is required. Both the system image and kernel
modules must be deployed together when changing the kernel version.

The optional RTL8169 Ethernet driver is a module so its PHY lookup runs after
asynchronous PCIe host discovery, outside that asynchronous probe context.

Keep `GPIO_CDEV_V1` enabled: Openpilot sensord uses the upstream v1 line-event
ioctl for IMU interrupts. Linux 7.2 no longer enables that API by default.

## Video driver selection

Keep `VIDEO_QCOM_IRIS` disabled. The 7.2 arm64 defconfig enables Iris and
Venus then omits the SC7280 compatible. Iris currently exposes decoding only;
openpilot recording requires the Venus encoder and its product controls.

## Upgrade validation

Keep the previous kernel, DTB, modules, and boot metadata recoverable before a
trial. Compare boot recovery, USB host/NCM, Wi-Fi, Bluetooth, UFS throughput,
CPU/Adreno performance, KVM, remote processors and video encoding on the same
board. Use temporary test/log/cache directories under `/data`.

Camera capture, Panda power/SPI, audio, IMU and full openpilot operation require
the corresponding hardware. A bare-board pass does not establish those results.

## USB transmit calibration

The Dragon DTS selects DWC3 mode 0 and QMP v4 tap 31 on its external USB port.
The equipped USB-C board with a 3 m cable and 7900 XTX passed model compilation,
1,200 synthetic model frames, three 1,600-frame live-camera runs, three normal
reboots, and an 8 GiB transfer test in each direction. Stock TX settings failed
GPU initialization and model compilation in the same fixture.

The second USB-C Dragon passed 2 GiB in each direction with both settings and
clean GPU finalization. The calibrated run reported zero additional link errors;
the stock run reported one. This short comparison does not establish a speed
advantage. Mode 0 / tap 31 is selected because it passed on both tested boards.
The equipped board still failed to enumerate after a cable flip with either
setting, so the change is not a complete fix for orientation or attachment.
Earlier stock USB-A tests favored stock settings; they are a different fixture.

Patches 0086-0088 add an optional, validated board calibration without changing
the common PHY table. Patch 0054 selects it for the external Dragon controller
(`/soc@0/usb@a600000`) using `snps,tx_de_emphasis_quirk` and
`snps,tx_de_emphasis = /bits/ 8 <0>`. Its PHY (`/soc@0/phy@88e8000`)
uses `qcom,tx-deemph-6db = <31>`. Receiver EQ and VGA stay at their SoC defaults.
The driver reapplies the tap before PHY startup, including after power loss.
No userspace register writes are required. The custom Chestnut firmware remains
unchanged by this calibration update.
