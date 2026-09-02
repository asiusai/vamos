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

# Default to NVMe unless the operator explicitly selects a UFS device. This
# signed programmer can ACK an unsupported backend and then hang before another
# backend can be selected, so sequential runtime probing is not safe.
detect_edl_storage() {
  local memory label selection

  if ! command -v edl-ng >/dev/null 2>&1; then
    echo "ERROR: edl-ng is required for EDL flashing" >&2
    return 1
  fi

  selection="${VAMOS_EDL_MEMORY:-nvme}"
  case "${selection,,}" in
    ufs) memory=Ufs; label=UFS ;;
    nvme) memory=Nvme; label=NVMe ;;
    *) echo "ERROR: VAMOS_EDL_MEMORY must be Ufs or Nvme" >&2; return 2 ;;
  esac

  EDL_STORAGE_ARGS=(--memory="$memory" --slot=0)
  if [ -n "${VAMOS_EDL_MEMORY:-}" ]; then
    EDL_STORAGE_LABEL="$label slot 0 (explicit)"
  else
    EDL_STORAGE_LABEL="NVMe slot 0 (Asius v0 default)"
  fi
  echo "== Using $EDL_STORAGE_LABEL =="
}
