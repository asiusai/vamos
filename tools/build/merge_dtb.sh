#!/usr/bin/env bash
# Merge camera DT nodes into the UEFI stock DTB.
# The UEFI DTB has correct memory reservations for Dragon Q6A;
# the kernel-compiled DTB has different addresses and crashes.
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"

UEFI_DTB="$DIR/kernel/dtb/uefi-stock.dtb"
OUT_DTB="$DIR/build/qcs6490-radxa-dragon-q6a.dtb"
WORK="/tmp/vamos_dtb_merge"

if [ ! -f "$UEFI_DTB" ]; then
  echo "ERROR: UEFI stock DTB not found at $UEFI_DTB"
  echo "Extract it from a running Dragon: sudo cat /sys/firmware/fdt > uefi-stock.dtb"
  exit 1
fi

mkdir -p "$WORK"

echo "-- Decompiling UEFI stock DTB --"
dtc -I dtb -O dts -o "$WORK/uefi.dts" "$UEFI_DTB" 2>/dev/null

echo "-- Patching: enable CAMSS, CCI1, add IMX219 CAM2, add HDMI HPD GPIO --"
cp "$WORK/uefi.dts" "$WORK/merged.dts"

python3 - "$WORK/merged.dts" <<'PYEOF'
import sys, re

path = sys.argv[1]
with open(path) as f:
    text = f.read()

# 1. Enable isp@acb3000 (CAMSS)
text = re.sub(
    r'(isp@acb3000 \{[^}]*?compatible = "qcom,sc7280-camss".*?)(status = "disabled")',
    r'\1status = "okay"',
    text, count=1, flags=re.DOTALL)

# 2. Add csiphy2 endpoint to CAMSS port@2
#    Find the third port@2 inside isp@acb3000 (the one under ports{})
isp_match = re.search(r'isp@acb3000 \{', text)
if isp_match:
    isp_start = isp_match.start()
    ports_match = re.search(r'ports \{', text[isp_start:])
    if ports_match:
        ports_start = isp_start + ports_match.start()
        port2_match = re.search(r'(port@2 \{\s*reg = <0x02>;\s*)\}', text[ports_start:])
        if port2_match:
            pos = ports_start + port2_match.start()
            old = port2_match.group(0)
            new = (port2_match.group(1) +
                   '\n\t\t\t\t\tendpoint {\n'
                   '\t\t\t\t\t\tdata-lanes = <0x00 0x01>;\n'
                   '\t\t\t\t\t\tremote-endpoint = <0x301>;\n'
                   '\t\t\t\t\t\tphandle = <0x300>;\n'
                   '\t\t\t\t\t};\n'
                   '\t\t\t\t}')
            text = text[:pos] + new + text[pos+len(old):]

# 3. Enable cci@ac4b000 (CCI1)
text = re.sub(
    r'(cci@ac4b000 \{[^}]*?compatible = "qcom,sc7280-cci.*?)(status = "disabled")',
    r'\1status = "okay"',
    text, count=1, flags=re.DOTALL)

# 4. Add cam_mclk2 pinctrl node under pinctrl@f100000 (TLMM, phandle 0xc0)
tlmm_match = re.search(r'(pinctrl@f100000 \{[^}]*?phandle = <0xc0>;\s*)', text)
if tlmm_match:
    pos = tlmm_match.end()
    mclk_node = ('\n\t\t\tcam-mclk2-active-state {\n'
                 '\t\t\t\tpins = "gpio66";\n'
                 '\t\t\t\tfunction = "cam_mclk";\n'
                 '\t\t\t\tdrive-strength = <0x06>;\n'
                 '\t\t\t\tbias-disable;\n'
                 '\t\t\t\tphandle = <0x302>;\n'
                 '\t\t\t};\n\n')
    text = text[:pos] + mclk_node + text[pos:]
    print("  Added cam-mclk2 pinctrl node")

# 5. Add IMX219 camera sensor under cci1/i2c-bus@0
cci1_match = re.search(r'cci@ac4b000 \{', text)
if cci1_match:
    cci1_start = cci1_match.start()
    i2c0_match = re.search(r'(i2c-bus@0 \{[^}]*?phandle = <0x232>;\s*)\}', text[cci1_start:])
    if i2c0_match:
        pos = cci1_start + i2c0_match.start()
        old = i2c0_match.group(0)
        camera_node = '''
				camera@10 {
					compatible = "sony,imx219";
					reg = <0x10>;
					clocks = <0x13b 0x60>;
					clock-names = "xclk";
					assigned-clocks = <0x13b 0x60>;
					assigned-clock-rates = <0x16e3600>;
					reset-gpios = <0xc0 0x4d 0x00>;
					pinctrl-0 = <0x302>;
					pinctrl-names = "default";

					port {

						endpoint {
							link-frequencies = /bits/ 64 <0x1b2e0200>;
							data-lanes = <0x01 0x02>;
							remote-endpoint = <0x300>;
							phandle = <0x301>;
						};
					};
				};'''
        new = i2c0_match.group(1) + camera_node + '\n\t\t\t}'
        text = text[:pos] + new + text[pos+len(old):]

# 6. Disable displayport-controller to prevent SError during DP probe.
#    UEFI already sets up DP display; Linux uses simpledrm to inherit it.
dp_match = re.search(r'(displayport-controller@ae90000 \{[^}]*?)(status = "okay")', text, re.DOTALL)
if dp_match:
    text = text[:dp_match.start(2)] + 'status = "disabled"' + text[dp_match.end(2):]
    print("  Disabled displayport-controller (using simpledrm instead)")
else:
    dp_match2 = re.search(r'(displayport-controller@ae90000 \{)', text)
    if dp_match2:
        pos = dp_match2.end()
        text = text[:pos] + '\n\t\t\t\tstatus = "disabled";' + text[pos:]
        print("  Added status=disabled to displayport-controller")

with open(path, 'w') as f:
    f.write(text)

print("  DTS patched successfully")
PYEOF

echo "-- Compiling merged DTB --"
dtc -I dts -O dtb -o "$OUT_DTB" "$WORK/merged.dts" 2>/dev/null

echo "-- Done: $(ls -lh "$OUT_DTB" | awk '{print $5}') --"
