# VamOS kernel

`linux/` pins Linux 7.2.7 from the official stable repository. Build it with
`./vamos build kernel`. The build applies the product patches to that exact
commit and merges `configs/vamos.config` over arm64 defconfig.

Keep hardware policy in userspace and board wiring in the device tree. Retain
driver changes only when the supported product needs behavior absent upstream.
The external USB PHY uses the mainline lane mapping. Transmit calibration is
selected in the board device tree: mode 0 and QMP v4 tap 31 on the external
Dragon port. The shared SoC defaults and internal USB controller are unchanged.

The AW9523 camera LED driver exposes six RGB sinks per board. It initializes
all sixteen currents to zero before LED mode. Each road board also exposes
one IR output, from 0 to 100 percent. Ten joined sinks are capped at DIM 206,
giving 149.45 mA nominal total. The cabin board has no IR control.
The global half-current setting limits RGB full scale to 18.5 mA nominal.

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
| 0093 | Enforce the Dragon Panda SPI bus ceiling in the controller, including per-transfer userspace requests |
| 0094 | Match Bluetooth command completions and preserve waits for later events during bonded phone reconnection |
| 0095 | Preserve the LE setup reference when encryption completes before remote features |
| 0096 | Use Bluetooth address types and on-air peer addresses in Secure Connections authentication |
| 0097 | Check authenticated LE encryption separately from BR/EDR link keys in SC-only mode |
| 0098 | Resolve AIC8800D80 private Bluetooth addresses in Linux to preserve the pairing address transcript |
| 0099-0101 | TrustZone SPI NOR access through SCM and MTD, from Junhao Xie and Armbian; limit transfers to 64 KiB |
| 0102 | Give Dragon EL2 shared buffers access for both the current caller and HLOS |

The upstream Dragon DTS uses a different firmware memory layout. Its board
support replaces the old full-board addition, but the Asius carveouts and
U-Boot/stock-EFI handoff remain necessary. Camera aliases are board-local. Keep
the established camera buses 16, 18 and 20: Openpilot identifies sensors by
these names. Bus 20 explicitly maps to `cci1_i2c1`; the old duplicate bus-19
alias for `cci1_i2c0` is removed. Disable upstream header `i2c0`: it owns
GPIO0, which Asius uses for the IMU interrupt. The IMU I2C controller remains
`i2c6` through the existing userspace `/dev/i2c-1` link.
Disable header `spi14` too: GPIO57 drives the Panda v6 IR current sink.
Mainline `pwm-gpio` and `pwm-leds` expose `asius:ir` at 1 kHz with brightness
0-100 and a default-off state. The EFI peripheral setup supplies the same
wiring for a stock firmware device tree. GPIO29 supplies the Panda button's
`KEY_PROG1` input through `gpio-keys`.
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

Patch 0094 prevents an LE LTK Reply from completing a pending remote-features
request. On Asius 1, this race disconnected a bonded iPhone during connection
setup. BlueZ then retained a stale connection. The patch preserves command
credits and completion errors. Retest bonded Android and iOS reconnection when
replacing it with an upstream fix.

Patch 0095 fixes the next stage of the same reconnect sequence. Encryption
completion must not release the LE remote-features request's reference.
Releasing it twice disconnects an active ATT connection four seconds later.
Initial Pixel and iPhone pairing and reconnection passed on Asius 1. Repeat pairing required patch 0098 below.

Patch 0096 corrects the address transcript used for LE Secure Connections.
The controller reports resolved identity address types. SMP needs the public/random
address type and the peer's on-air address. The wrong transcript caused matching
comparison numbers to fail the DHKey check on the Pixel.

Patch 0097 checks LE security with its security level, key size and encryption flags.
The previous SC-only check required a BR/EDR link-key type that LE does not set.
It disconnected the Pixel immediately after successful encryption.
LE still requires authenticated Secure Connections, a 16-byte key and active AES-CCM encryption.
BR/EDR checks are unchanged. The host regression compiles the actual patched checks:
`python3 -m unittest tools/test_bluetooth_security.py`.

Patch 0098 disables controller-side address resolution on USB device `a69c:8d81`.
The AIC8800D80 reports a resolved identity but omits the peer's on-air private address.
After removing an iPhone bond, repeat pairing fails its DHKey check despite matching comparison digits.
Linux resolves private addresses with saved IRKs instead. Secure Connections, Numeric Comparison and authenticated GATT remain required.
The regression tests check the privacy capability and the security checks together.

Kernel `#285` passed real Linux Chrome enrollment and two reconnect tests on Asius 1.
The iPhone passed enrollment, reconnection and another enrollment after removing both authorizations and bonds.
The second enrollment used the same device boot without a Bluetooth controller reset.
Tests compared independently calculated digits and injected a fresh three-second Panda input hold after a 12-second listening delay.
Microphone recordings confirmed the spoken comparison and success announcement on the PC and iPhone tests.
The Pixel was unavailable for this repeat-pairing test. Clean-flash validation remains pending.
The default 1.28-second advertisement interval caused second-client connection timeouts with the iPhone already connected.
Asius bluetoothd now requests a 100-150 ms interval through BlueZ.
Fresh Linux Chrome enrollment and repeated authenticated reconnects passed while the iPhone remained connected.
The iPhone also passed reconnection while Chrome remained connected. Both clients retained independent encrypted sessions.

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

The AIC8800 source is pinned to Radxa release `5.0+git20260123.5f7be68d-10`.
Its firmware-path fix replaces overlapping `sprintf` calls with bounded appends.
The D80 firmware blobs and in-tree Bluetooth driver are unchanged.

Keep `GPIO_CDEV_V1` enabled: Openpilot sensord uses the upstream v1 line-event
ioctl for IMU interrupts. Linux 7.2 no longer enables that API by default.

## Boot firmware storage

Patches 0099-0101 provide the proposed upstream SCM storage interface and its MTD driver.
They come from [Armbian's QCS6490 series](https://github.com/armbian/build/commit/1ecbaf6c93d3c026eaf3d6f6e91a3d842a8dac60).
The [64 KiB transfer limit](https://github.com/armbian/build/commit/91b98a0a28966eaa1dab76271860064ba0ee5fdb) keeps each allocation within the SCM memory pool.

Dragon U-Boot uses Secure Launch to enter EL2.
On this path, HLOS-only shared buffers cause SMC error `-8`.
Self-owner-only buffers permit initialization but fail the storage-information query.
Patch 0102 grants read/write access to both, only on Dragon boots with EL2 available.
Other boards and stock EL1 boots retain the existing behavior.

The `qcom_scm_storage` module exposes NOR through MTD.
Load it with `sudo modprobe qcom_scm_storage`.
Find the device by its `qcom_scm_storage` name; its device number can change.
Direct QSPI remains disabled because it resets this board.
This interface supports explicit firmware maintenance over SSH.
Automatic U-Boot and firmware installation through OS or openpilot updates is not implemented.
Preserve a verified device-specific backup before writing boot firmware.
Asius 1 passed full NOR reads and erase/write/restore tests in unallocated space.
Replacing an active boot-firmware partition through this interface remains untested.
One EDL return failed before Linux started. See the bootloader README for this unresolved boot limitation.

## Video driver selection

Keep `VIDEO_QCOM_IRIS` disabled. The 7.2 arm64 defconfig enables Iris and
Venus then omits the SC7280 compatible. Iris currently exposes decoding only;
openpilot recording requires the Venus encoder and its product controls.

## Upgrade validation

The camera LEDs breathe pure blue together during boot, with a 1.2-second rise
and a 1.8-second fall. The outer LEDs retain a faint 2/255 glow between breaths.
They peak at 25/255, below 10%, because camera exposure is not yet available.
Logical LEDs 1-6 sit at 0, 7.5, 15, 45, 52.5 and 60 mm. LEDs 2 and 5 stay off.
The three-second curve is sampled at 60 Hz without floating point in the kernel.
`tools/build/generate_boot_led_pattern.py` generates the table in patch 0062.
Patch 0054 maps each RGB trigger to its physical camera channel; logical 1 is
wide-camera package 2 and logical 6 is road-camera package 0. Openpilot takes
over the triggers and uses steady blue once startup is complete, adapting
the peak between 10% and 50% when fresh wide-road camera exposure is available.

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

## Panda SPI clock

The Dragon Panda controller has a 37 MHz request ceiling in the board DTS,
which selects a 33.33 MHz GENI clock. The next clock step is unreliable through
the board's level translator. Patch 0093 applies that limit to the controller,
so the SPI core clamps both the spidev default and explicit transfer speeds.
Host clients can request the upstream 50 MHz without a hardware-specific override.
The Panda slave firmware does not set this clock. A child device's
`spi-max-frequency` alone does not prevent spidev from requesting a faster clock.

Validated on `asius-v0-2` with unchanged Panda firmware: 500 reads each at
37 MHz, explicit 50 MHz, and a 50 MHz spidev default passed without additional
SPI errors with conservative protocol pacing. The upstream Python client also
passed 500 reads at 50 MHz without retries; 37 MHz comparison runs needed one
and two retries. Native pandad remained healthy with the shared default at
50 MHz. Install this kernel before using host clients without the Dragon override.
