#!/usr/bin/env bash

# Linux's qcserial driver claims Qualcomm 9008 before WebUSB or edl-ng can.
# The udev setup rule handles future connections; this also fixes an EDL device
# that was already connected before the rule was installed or reloaded.
detach_qcserial() {
  local interface name
  [ "$(uname)" = Linux ] || return 0
  for interface in /sys/bus/usb/drivers/qcserial/*:*; do
    [ -L "$interface" ] || continue
    name="${interface##*/}"
    echo "== Detaching qcserial from EDL interface $name =="
    printf '%s' "$name" | sudo tee /sys/bus/usb/drivers/qcserial/unbind >/dev/null
  done
}

EDL_MAX_PAYLOAD="${VAMOS_EDL_MAX_PAYLOAD:-65536}"
if ! [[ "$EDL_MAX_PAYLOAD" =~ ^[0-9]+$ ]] || [ "$EDL_MAX_PAYLOAD" -lt 4096 ]; then
  echo "ERROR: VAMOS_EDL_MAX_PAYLOAD must be an integer of at least 4096 bytes" >&2
  return 2 2>/dev/null || exit 2
fi
EDL_TRANSPORT_ARGS=(--maxpayload="$EDL_MAX_PAYLOAD")

# Select the first usable 512-byte-sector target in the same order as the
# browser flasher. Qualcomm exposes eMMC and SD cards as SDCC slots 0 and 1.
# A one-sector read is used instead of trusting configure: unsupported backends
# can acknowledge configure and then reject every storage command.
detect_edl_storage() {
  local loader="$1"
  local memory slot label probe_dir probe_file output size

  if ! command -v edl-ng >/dev/null 2>&1; then
    echo "ERROR: edl-ng is required for EDL flashing" >&2
    return 1
  fi

  if [ -n "${VAMOS_EDL_MEMORY:-}" ]; then
    case "${VAMOS_EDL_MEMORY,,}" in
      ufs) memory=Ufs; label=UFS ;;
      nvme) memory=Nvme; label=NVMe ;;
      emmc|sdcc) memory=Sdcc; label=eMMC/SDCC ;;
      *) echo "ERROR: VAMOS_EDL_MEMORY must be Ufs, Nvme, or Sdcc" >&2; return 2 ;;
    esac
    slot="${VAMOS_EDL_SLOT:-0}"
    case "$slot" in 0|1) ;; *) echo "ERROR: VAMOS_EDL_SLOT must be 0 or 1" >&2; return 2 ;; esac
    EDL_STORAGE_ARGS=(--memory="$memory" --slot="$slot")
    EDL_STORAGE_LABEL="$label slot $slot (explicit)"
    echo "== Using $EDL_STORAGE_LABEL =="
    return 0
  fi

  probe_dir="$(mktemp -d -t vamos-edl-probe.XXXXXX)"
  probe_file="$probe_dir/sector.bin"
  while IFS=: read -r memory slot label; do
    echo "== Probing $label =="
    if output="$(sudo edl-ng "${EDL_TRANSPORT_ARGS[@]}" --memory="$memory" --slot="$slot" --loader="$loader" read-sector 0 1 "$probe_file" --lun 0 2>&1)"; then
      size="$(stat -c %s "$probe_file" 2>/dev/null || echo 0)"
      if [ "$size" = 512 ]; then
        rm -f "$probe_file" "$probe_file.partial"
        rmdir "$probe_dir"
        EDL_STORAGE_ARGS=(--memory="$memory" --slot="$slot")
        EDL_STORAGE_LABEL="$label"
        echo "== Selected $EDL_STORAGE_LABEL =="
        return 0
      fi
      echo "WARN: $label uses $size-byte sectors; this factory image requires 512-byte sectors" >&2
    fi
    rm -f "$probe_file" "$probe_file.partial"
    # Give libusb and the programmer time to release the interface before the
    # next edl-ng process reconfigures the same endpoint.
    sleep 1
  done <<'EOF'
Ufs:0:UFS
Nvme:0:NVMe
Sdcc:0:eMMC
Sdcc:1:SD card
EOF
  rmdir "$probe_dir"
  echo "ERROR: no usable UFS, NVMe, eMMC, or SD-card target was found" >&2
  [ -z "${output:-}" ] || printf '%s\n' "$output" >&2
  return 1
}
