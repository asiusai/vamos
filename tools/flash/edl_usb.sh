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
