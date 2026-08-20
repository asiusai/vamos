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

# Select the known v1 storage target unless the operator supplies a hardware
# override. This signed programmer can ACK an unsupported backend and then hang
# before another backend can be selected, so sequential runtime probing is not
# safe. Qualcomm exposes eMMC and SD cards as SDCC slots 0 and 1.
detect_edl_storage() {
  local memory slot label selection

  if ! command -v edl-ng >/dev/null 2>&1; then
    echo "ERROR: edl-ng is required for EDL flashing" >&2
    return 1
  fi

  selection="${VAMOS_EDL_MEMORY:-emmc}"
  case "${selection,,}" in
    ufs) memory=Ufs; label=UFS ;;
    nvme) memory=Nvme; label=NVMe ;;
    emmc|sdcc) memory=Sdcc; label=eMMC/SDCC ;;
    *) echo "ERROR: VAMOS_EDL_MEMORY must be Ufs, Nvme, or Sdcc" >&2; return 2 ;;
  esac
  slot="${VAMOS_EDL_SLOT:-0}"
  case "$slot" in 0|1) ;; *) echo "ERROR: VAMOS_EDL_SLOT must be 0 or 1" >&2; return 2 ;; esac

  EDL_STORAGE_ARGS=(--memory="$memory" --slot="$slot")
  if [ -n "${VAMOS_EDL_MEMORY:-}" ] || [ -n "${VAMOS_EDL_SLOT:-}" ]; then
    EDL_STORAGE_LABEL="$label slot $slot (explicit)"
  else
    EDL_STORAGE_LABEL="eMMC/SDCC slot 0 (Asius v1 default)"
  fi
  echo "== Using $EDL_STORAGE_LABEL =="
}
