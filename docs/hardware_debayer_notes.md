# VFE PIX Hardware Debayer on Dragon Q6A (QCS6490 / SC7280)

Research notes on using the VFE (Video Front End) ISP PIX pipeline for
hardware debayer on the Radxa Dragon Q6A.

## Current Status

**Not functional.** The mainline kernel `camss-vfe-680.c` / `camss-vfe-17x.c`
driver only implements RDI (Raw Dump Interface) write-master configuration.
The VFE PIX ISP processing pipeline (CAMIF input, demux, demosaic, color
correction, gamma, scaler, Y/UV write masters) is not programmed by the
mainline driver. STREAMON on a PIX video device succeeds but no frames are
ever produced.

Production path uses RDI mode with CPU software debayer (RAW10 to NV12) at
30 FPS. PIX mode is opt-in via `DRAGON_PIX=1` environment variable.


## SC7280 CAMSS Topology

Confirmed via media controller enumeration:

- **5 CSIPHYs** (0-4), **5 CSIDs** (0-4), **5 VFEs** (0-4)
- Any CSIPHY can link to any CSID, any CSID can link to any VFE

### VFE Types

| VFE   | Type | Lines              | Video Devices     |
|-------|------|--------------------|-------------------|
| VFE0  | lite | 3 RDI              | video0-2          |
| VFE1  | lite | 3 RDI              | video3-5          |
| VFE2  | lite | 3 RDI              | video6-8          |
| VFE3  | full | 3 RDI + 1 PIX      | video9-11, video12 (PIX/NV12) |
| VFE4  | full | 3 RDI + 1 PIX      | video13-15, video16 (PIX/NV12) |

**Note:** The sc7280 resource table labels VFE0-2 as `is_lite = false` but
they only have 3 lines (RDI0-2) -- no PIX hardware. The 0206 patch bumps
`line_num` from 3 to 4 on all non-lite VFEs, but only VFE3-4 actually have
ISP processing blocks.

### CSID Pad Layout

- Pad 0: sink (from CSIPHY)
- Pads 1-3: source, connected to VFE RDI0-2
- Pad 4: source, connected to VFE PIX (full VFEs only)

### Routing Constraints

**RDI pairing**: CSID N must pair with VFE N. Cross-connections (e.g.
CSID2 -> VFE0 RDI) produce zero frames. Confirmed empirically.

**PIX cross-connection**: CSID pad 4 can route to any full VFE's PIX input.
CSID2 pad 4 -> VFE3 PIX and CSID3 pad 4 -> VFE4 PIX both succeed via
`MEDIA_IOC_SETUP_LINK`. However, the ISP processing is not implemented in
the mainline kernel driver.

### Dragon Camera Routing

| Camera | Sensor | Chain | RDI Device | PIX Device (future) |
|--------|--------|-------|------------|---------------------|
| cam 0 (road) | IMX219 18-0010 | CSIPHY2 -> CSID2 -> VFE2 RDI0 | video6 | CSID2 -> VFE3 PIX (video12) |
| cam 1 (wide) | IMX219 20-0010 | CSIPHY3 -> CSID3 -> VFE3 RDI0 | video9 | CSID3 -> VFE4 PIX (video16) |


## What Works

1. **CSID routing patches** (0202, 0204) correctly route sensor data from
   CSID pad 4 to VFE3/4 PIX entities.
2. **NV12 format** shows up on video12 (VFE3 PIX) and video16 (VFE4 PIX)
   after the PIX line is exposed.
3. **Media link setup** succeeds for PIX cross-connections.
4. **Format negotiation** works: Bayer input (SRGGB10_1X10) on the sink pad
   maps to YUYV8_1_5X8 on the source pad, which maps to V4L2_PIX_FMT_NV12
   on the video device.

## What Doesn't Work

1. **VFE PIX ISP register programming**: The mainline `camss-vfe-17x.c`
   `vfe_wm_start()` only configures RDI write masters (1D line mode).
   The PIX pipeline requires programming CAMIF, demux, demosaic, color
   correction, gamma LUT, YUV conversion, scaler, crop, and 2D write
   masters (Y + UV planes). Without this, STREAMON succeeds but no frames
   are produced -- no CAMIF SOF interrupts, no buffer completions.

2. **Buffer completion for multi-WM outputs**: PIX produces NV12 with two
   write masters (WM3 for Y, WM4 for UV). The ISR `vfe_isr_wm_done()` only
   processes the buffer on the *last* WM of a multi-WM output. The upstream
   `vfe_isr_comp_done()` iterates forward, finding the first WM -- but
   `vfe_isr_wm_done()` requires the last. Patch 0207 fixes this by
   iterating in reverse.

3. **ISR gating bug**: The per-WM `wm_done` loop gates on
   `status0 & BIT(9)` (`IMAGE_MASTER_PING_PONG(1)`), which is irrelevant
   for PIX WMs. `bus_status[1]` already carries per-WM `buf_done` bits.
   Patch 0207 removes this gate.


## Kernel Patch Files

All patches are `.disabled` (not applied during build). Located in
`vamos/kernel/patches/`:

### 0200 - VFE PIX pipeline and userspace register control
**File:** `0200-media-camss-add-VFE-PIX-pipeline-and-userspace-register-control.patch.disabled`
**Size:** 46 KB (the main patch)

Adds to `camss-vfe-17x.c` (vfe_ops_170, used by sc7280):

- **In-kernel ISP programming** (`vfe_pix_configure_isp()`): Programs the
  full ISP pipeline with hardcoded register values derived from openpilot
  downstream dumps. Modules: demux, white balance, black level,
  linearization (DMI LUT upload), debayer/demosaic, color correction, gamma
  (DMI LUT upload for 3 channels), YUV conversion (BT.601), scaler (Y and
  UV), crop, CAMIF. Also programs CGC (Clock Gate Control) override
  registers to force all ISP module clocks on.

- **PIX write master config** (`vfe_pix_set_crop_wm()`): Routes ISP output
  to WM3 (Y plane) and WM4 (UV plane) via BUS registers at 0x2018
  (output enable), 0x25a0/0x26a0 (port mapping), 0x2070 (composite group).

- **PIX-aware `vfe_wm_start()`**: When `line->id == VFE_LINE_PIX`, programs
  write masters in 2D mode (`MODE_QCOM_PLAIN`) with proper width, height,
  stride, packer config (0x0e for NV12), framedrop/subsample patterns.
  RDI path unchanged (`MODE_MIPI_RAW`, 1D).

- **PIX-aware `vfe_wm_update()`**: Computes `frame_inc` differently for Y
  plane (stride * height) vs UV plane (stride * height/2).

- **PIX reg_update**: Uses `REG_UPDATE_PIX` (BIT(0)) instead of
  `REG_UPDATE_RDI(n)` (BIT(1+n)).

- **PIX ISR handling**: Detects CAMIF SOF (`STATUS_0_CAMIF_SOF`) and
  composite reg_update done (`STATUS0_COMP_REG_UPDATE0_DONE` from bus
  status) for the PIX line. Limits RDI ISR loops to `VFE_LINE_RDI0..RDI2`.

- **PIX output allocation** (`vfe_get_output()`): PIX uses 2 write masters
  (wm_idx[0]=2, wm_idx[1]=3) instead of 1 for RDI.

- **SOF completion**: `vfe_isr_sof()` calls `complete(&output->sof)` to
  wake `VFE_WAIT_SOF` callers.

- **Userspace ioctl interface** (9 new ioctls via `vidioc_default`):
  - `VFE_WRITE_REGS`: Batch write ISP registers from userspace array
  - `VFE_WRITE_DMI`: Upload LUT data via DMI (Data Memory Interface)
  - `VFE_MAP_BUF` / `VFE_UNMAP_BUF`: DMA-BUF IOMMU mapping for output buffers
  - `VFE_SET_BUF`: Set per-WM buffer address, stride, frame_inc
  - `VFE_REG_UPDATE`: Trigger register update latch
  - `VFE_START` / `VFE_STOP`: Pipeline lifecycle (power, clocks, upstream streaming)
  - `VFE_WAIT_SOF`: Blocking wait for start-of-frame IRQ

- **New UAPI header** (`include/uapi/linux/qcom-camss-vfe.h`): Defines all
  ioctl structs and magic numbers. `VFE_IOC_MAGIC = '#'`,
  `SENSOR_IOC_MAGIC = 'S'`.

- **Mapped buffer tracking**: `struct xarray mapped_bufs` in `vfe_device`
  for tracking DMA-BUF attachments.

- **PIX format table** (`formats_pix_845[]`): NV12/NV21 output formats with
  Bayer 10-bit and 12-bit sink pad codes.

- **Source pad code mapping**: PIX line maps Bayer sink codes to
  `MEDIA_BUS_FMT_YUYV8_1_5X8` source code.

- **BPL alignment**: PIX video device uses 2048-byte BPL alignment (vs 8
  for RDI).

### 0202 - CSID gen2 PIX path configuration
**File:** `0202-media-camss-csid-gen2-skip-RDI-registers-for-PIX-path-on-full-CSIDs.patch.disabled`

On sc7280 full CSIDs (non-lite), the PIX output path has a dedicated
register bank at offset 0x200 (`CSID_PIX_CFG0` through
`CSID_PIX_LINE_DROP_PERIOD`). This is distinct from lite CSIDs where 0x200
is RDI0.

Previously, the driver skipped all configuration for pad 4 (vc index 3)
because `CSID_RDI_CFG0(3)` = 0x600 collides with TPG registers.

This patch adds:
- `__csid_configure_pix_stream()`: Writes to the 0x200 PIX register bank
  with the actual `decode_format` (not `PAYLOAD_ONLY` as RDI uses).
  Configured with `VIRTUAL_CHANNEL=0` to match sensor default.
- `__csid_ctrl_pix()`: PIX-specific halt/resume at `CSID_PIX_CTRL` (0x208).
- PIX IRQ handling: Reads/clears PIX IRQs at 0x30/0x34/0x38 (separate from
  RDI IRQs at 0x40+).
- Skips RDI register writes for `i >= 3` on non-lite CSIDs.

### 0204 - CSID 680 PIX routing
**File:** `0204-media-camss-csid-680-route-sensor-data-to-PIX-output-path.patch.disabled`

Fixes CSID 680 (sc7280/qcs6490) stream configuration for pad 4 / RDI 3:
- The default code treated all pads identically, using `PAYLOAD_ONLY` decode
  and `VIRTUAL_CHANNEL = pad_index`. For PIX (pad 4), the sensor uses VC 0
  (not 3), and ISP processing requires the actual decode format.
- Adds `__csid_configure_pix_rdi()` which captures VC 0 with the proper
  decode format for RDI 3.

### 0205 - Debug pipeline walk
**File:** `0205-debug-video-pipeline-walk.patch.disabled`

Adds `pr_err` debug logging throughout the pipeline:
- CSID `configure_stream` and `set_stream` entry points
- VFE `wm_start`, `wm_update`, `vfe_enable` with register readbacks
- ISR with all status register values
- Useful for diagnosing why PIX frames are not produced.

### 0206 - Expose PIX line on full VFEs
**File:** `0206-media-camss-sc7280-expose-PIX-line-on-full-VFEs.patch.disabled`

Changes `line_num` from 3 to 4 for VFE0, VFE1, VFE2 in the sc7280 resource
table (`vfe_res_7280[]`). This exposes the 4th line (PIX) as a video device
(e.g. video3, video7, video11 for VFE0/1/2 respectively, plus video12 for
VFE3 and video16 for VFE4).

Without this patch, only 3 RDI lines are registered per VFE and there is no
way to open the PIX video device.

### 0207 - Fix PIX ISR buffer completion bugs
**File:** `0207-media-camss-fix-PIX-ISR-buffer-completion-bugs.patch.disabled`

Two ISR bug fixes:
1. `vfe_isr_comp_done()` iterates forward and calls `wm_done()` with the
   first WM mapped to PIX. But `vfe_isr_wm_done()` requires the *last* WM
   for multi-WM outputs (it has an early-exit check:
   `if (output->wm_num > 1 && wm != output->wm_idx[output->wm_num - 1])`).
   Fix: iterate in reverse to find the UV WM (the last one).

2. The wm_done loop in the ISR gates on `status0 & BIT(9)` which is
   `IMAGE_MASTER_PING_PONG(1)` -- irrelevant for PIX WMs.
   `bus_status[1]` already carries per-WM `buf_done` bits. Fix: remove the
   status0 gate; check only `bus_status[1]`.

Also adds rate-limited overflow logging.


## Custom VFE Ioctl Interface

The patch adds a userspace register control interface, allowing camerad to
program ISP registers directly instead of relying on in-kernel ISP
configuration. The kernel handles power, clocks, IOMMU, and interrupt
delivery.

### Ioctl Definitions

Defined in `include/uapi/linux/qcom-camss-vfe.h` and mirrored in
`openpilot/third_party/linux/include/media/qcom-camss-vfe.h`.

```
VFE_IOC_MAGIC = '#'     (0x23)
SENSOR_IOC_MAGIC = 'S'  (0x53)
```

| Ioctl | Direction | Struct | Purpose |
|-------|-----------|--------|---------|
| `VFE_WRITE_REGS` | _IOW | `vfe_write_regs_cmd` | Batch write up to 1024 ISP register offset/value pairs |
| `VFE_WRITE_DMI` | _IOW | `vfe_dmi_cmd` | Upload LUT data via DMI (auto-increment, up to 4096 entries) |
| `VFE_MAP_BUF` | _IOWR | `vfe_map_buf_cmd` | Map DMA-BUF fd into VFE SMMU, returns IOVA + size |
| `VFE_UNMAP_BUF` | _IOW | `vfe_unmap_buf_cmd` | Unmap previously mapped buffer |
| `VFE_SET_BUF` | _IOW | `vfe_set_buf_cmd` | Set WM buffer address, stride, frame_inc |
| `VFE_REG_UPDATE` | _IO | (none) | Trigger register update latch (PIX or RDI) |
| `VFE_START` | _IO | (none) | Power on VFE, enable IRQs, start upstream pipeline |
| `VFE_STOP` | _IO | (none) | Stop WMs, decrement stream count, power off |
| `VFE_WAIT_SOF` | _IO | (none) | Block until CAMIF SOF IRQ (200ms timeout) |
| `SENSOR_WRITE_REGS` | _IOW | `sensor_write_regs_cmd` | Write sensor registers via CCI (on sensor subdev fd) |

### Important ENOTTY Gotcha

`VFE_IOC_MAGIC` is `'#'` (0x23), not `'v'` (0x76). Using the wrong magic
byte produces `ENOTTY` (ioctl not recognized). `EINVAL` means the ioctl
dispatched correctly but the arguments were invalid.


## camera_dragon.cc PIX Mode Implementation

File: `openpilot/system/camerad/cameras/camera_dragon.cc`

### Mode Selection

Controlled by `DRAGON_PIX=1` env var. Each `DragonCamera` instance has a
`use_pix` boolean set at construction.

### Media Link Setup (`setup_media_links()`)

- PIX mode: CSIPHY -> CSID (pad 1 -> pad 0), then CSID pad 4 -> VFE_PIX pad 0
- RDI mode: CSIPHY -> CSID, then CSID pad 1 -> VFE_RDI0 pad 0
- Falls back to RDI if the PIX link setup fails

### Camera Routing Table

```cpp
static const struct { int csiphy; int csid; int pix_vfe; int rdi_vfe; } routing[] = {
  {2, 2, 2, 2},   // cam 0 (road): PIX via VFE2, RDI via VFE2
  {3, 3, 1, 3},   // cam 1 (wide): PIX via VFE1, RDI via VFE3
};
```

### Format Negotiation (`set_formats()`)

PIX mode:
- CSIPHY/CSID/sensor: Bayer SRGGB10_1X10 at sensor resolution
- CSID pad 4 gets the same Bayer format
- VFE PIX subdev sink: Bayer SRGGB10_1X10
- VFE PIX video device: V4L2_PIX_FMT_NV12

RDI mode:
- Same sensor/CSIPHY/CSID format
- CSID pad 1 (RDI)
- VFE RDI video device: SRGGB10P (packed 10-bit Bayer)

### ISP Programming After STREAMON (`start_streaming()`)

When `use_pix` is true, after STREAMON (so VFE is powered and clocked):
1. Calls `build_initial_config_flat()` from `ife.h` to generate register
   writes and DMI uploads
2. Sends registers via `VFE_WRITE_REGS` ioctl
3. Sends DMI LUTs (linearization, vignetting, gamma) via `VFE_WRITE_DMI`
4. Triggers `VFE_REG_UPDATE` to latch all writes

### Per-Frame Update (`enqueue_pix_frame()`)

1. `build_update_flat()` generates per-frame register writes (CGC, demux,
   white balance, module enables, cropping, black level)
2. `VFE_WRITE_REGS` to send them
3. `VFE_SET_BUF` for WM3 (Y plane) and WM4 (UV plane) with proper IOVA,
   stride, and frame_inc
4. `VFE_REG_UPDATE` to latch

### Frame Processing

PIX mode: `process_pix_frame()` copies NV12 data from V4L2 MMAP buffer to
VIPC buffer (stride-aware copy if V4L2 stride differs from VIPC stride).

RDI mode: `process_rdi_frame()` calls `debayer_raw10_to_nv12()` for CPU
software debayer from RAW10 to NV12.


## ife.h -- ISP Register Configuration

File: `openpilot/system/camerad/cameras/ife.h`

Provides two interfaces for the same ISP register values:

### CDM-encoded (for downstream kernel with command DMA)
- `build_initial_config()` / `build_update()`: Write CDM packets with
  `write_cont()`, `write_random()`, `write_dmi()`
- Used by comma 3X with downstream Qualcomm CamX/KMD

### Flat register lists (for mainline kernel with custom ioctls)
- `build_initial_config_flat()` / `build_update_flat()`: Return
  `std::vector<reg_write>` and `std::vector<dmi_upload>`
- Used by Dragon with mainline kernel + VFE ioctl patches
- Helper functions: `collect_cont()`, `collect_random()`

### ISP Pipeline Stages Programmed

1. **CGC override** (0x02c-0x03c): Force all ISP module clocks on
2. **Module enables** (0x040-0x04c): BLACK, DEMUX, DEMO, BLACK_LEVEL,
   COLOR_CORRECT, RGB_LUT, Y_SCALER, UV_SCALER, Y_CROP
3. **CORE_CFG** (0x050): Bayer pattern from sensor
4. **CAMIF** (0x478-0x49c): Input formatting, subsample, skip
5. **Raw crop** (0xce4): Full frame dimensions
6. **Epoch IRQ** (0x4a0): Half-frame interrupt
7. **Linearization** (0x4dc-0x510): Kneepoints + DMI LUT (RAM sel 9)
8. **Vignetting** (0x6bc-0x6d8): Lens shading correction + DMI LUTs
   (RAM sel 14 GRR, 15 GBB)
9. **Demux** (0x560): Bayer channel routing
10. **White balance** (0x6fc): Unity (1.0x all channels)
11. **Black level** (0x6b0): Scale + offset from sensor info
12. **Debayer/demosaic** (0x6f8, 0x71c): Interpolation coefficients
13. **Color correction** (0x760): 3x3 matrix from sensor
14. **Gamma** (0x798): 3-channel LUT via DMI (RAM sel 26/28/30)
15. **Scaler** (0xa3c, 0xa68): Y and UV scaling
16. **Crop** (0xe0c-0xe38): Y and UV output dimensions
17. **YUV conversion** (0xf30): BT.601 RGB-to-YUV matrix
18. **Flush/halt** (0xf80): ISP flush configuration
19. **BUS config** (0x2018, 0x2070, 0x25a0, 0x26a0): PIX output routing to
    WM3 (Y) + WM4 (UV), composite group, port mapping
20. **WM dimensions** (0x2500-0x2658): Width, height, packer, stride,
    frame_inc, framedrop, subsample for WM3 and WM4


## What Would Need to Happen to Finish PIX Hardware Debayer

### Option 1: Enable the custom ioctl patches (most likely path)

1. **Enable patches 0200, 0202, 0204, 0206, 0207** by removing the
   `.disabled` suffix. These are the minimum set:
   - 0206: expose PIX line on VFEs
   - 0202 + 0204: CSID PIX path routing and configuration
   - 0200: VFE PIX ISP programming + userspace ioctl interface
   - 0207: ISR buffer completion fixes

2. **Remove debug logging** from patch 0205 (or don't enable it). The
   `pr_err` debug prints are rate-limited but still noisy.

3. **Rebuild kernel** with patches enabled and flash.

4. **Test with `DRAGON_PIX=1`**: The `camera_dragon.cc` PIX mode code is
   already implemented. It will use `ife.h` to program ISP registers via
   the VFE ioctls after STREAMON.

5. **Fix the routing constraint**: The current routing table in
   `camera_dragon.cc` routes cam 0 through VFE2 for PIX mode, but VFE2
   may be lite (only RDI). The routing needs to use VFE3 or VFE4 for PIX.
   The memory note says "CSID N must route to VFE N for RDI" but PIX can
   cross-connect, so CSID2 pad 4 -> VFE3 PIX should work.

6. **Validate ISP register values**: The register values in `ife.h` were
   derived from comma 3X (OX03C10 sensor on SDM845). The Dragon uses
   IMX219 sensors. The sensor-specific values (linearization LUT,
   vignetting LUT, gamma LUT, color correction matrix, black level) come
   from the `SensorInfo` object, so they should adapt. But the fixed ISP
   pipeline configuration (demux, CAMIF, scaler coefficients) may need
   tuning for IMX219's Bayer pattern and resolution.

7. **Verify IOMMU / DMA-BUF mapping**: The `VFE_MAP_BUF` ioctl maps
   DMA-BUF fds into the VFE SMMU. This path is used by `enqueue_pix_frame()`
   but the current `camera_dragon.cc` uses V4L2 MMAP buffers, not DMA-BUF.
   The VIPC buffer IOVAs need to be mapped if using the direct ioctl path.
   Alternatively, the in-kernel PIX path (part of 0200) uses V4L2 buffer
   management directly.

### Option 2: Full in-kernel VFE PIX implementation

Write a proper `vfe_pix_configure_isp()` in the kernel that reads format
information from the V4L2 subdev format and programs ISP registers
accordingly. This is what patch 0200 partially does with hardcoded values.
A complete implementation would need to:
- Derive ISP parameters from the negotiated format
- Support multiple sensor types
- Handle dynamic exposure/gain updates
- Program DMI LUTs from userspace-provided data

This is significantly more complex than the ioctl approach and is not
recommended.

### Option 3: GPU debayer via OpenCL on Adreno 643L

Skip VFE PIX entirely. Continue using RDI mode to capture raw Bayer frames,
then use an OpenCL kernel on the Adreno 643L GPU to debayer. This is an
intermediate option that avoids kernel patches but adds GPU load. The
Adreno 643L with Rusticl/Mesa can do this, but it competes with modeld for
GPU time (modeld already runs at ~38ms, near hardware limit).


## Key File Locations

### Kernel patches
- `vamos/kernel/patches/0200-media-camss-add-VFE-PIX-pipeline-and-userspace-register-control.patch.disabled`
- `vamos/kernel/patches/0202-media-camss-csid-gen2-skip-RDI-registers-for-PIX-path-on-full-CSIDs.patch.disabled`
- `vamos/kernel/patches/0204-media-camss-csid-680-route-sensor-data-to-PIX-output-path.patch.disabled`
- `vamos/kernel/patches/0205-debug-video-pipeline-walk.patch.disabled`
- `vamos/kernel/patches/0206-media-camss-sc7280-expose-PIX-line-on-full-VFEs.patch.disabled`
- `vamos/kernel/patches/0207-media-camss-fix-PIX-ISR-buffer-completion-bugs.patch.disabled`

### Openpilot camerad
- `openpilot/system/camerad/cameras/camera_dragon.cc` -- Dragon V4L2 camerad with PIX/RDI modes
- `openpilot/system/camerad/cameras/ife.h` -- ISP register configuration (CDM + flat)
- `openpilot/system/camerad/cameras/hw.h` -- Camera config structs
- `openpilot/third_party/linux/include/media/qcom-camss-vfe.h` -- UAPI header copy

### Kernel source (in patch)
- `drivers/media/platform/qcom/camss/camss-vfe-17x.c` -- VFE 170 ops (PIX ISP + ioctls)
- `drivers/media/platform/qcom/camss/camss-vfe.c` -- VFE common (PIX format table, multi-WM output)
- `drivers/media/platform/qcom/camss/camss-vfe.h` -- VFE structs (mapped_buf, hw_ops extensions)
- `drivers/media/platform/qcom/camss/camss-csid-gen2.c` -- CSID gen2 PIX path config
- `drivers/media/platform/qcom/camss/camss-csid-680.c` -- CSID 680 PIX routing
- `drivers/media/platform/qcom/camss/camss-video.c` -- Video ioctl dispatch for VFE ioctls
- `include/uapi/linux/qcom-camss-vfe.h` -- UAPI ioctl definitions
