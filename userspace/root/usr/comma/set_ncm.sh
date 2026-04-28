#!/bin/bash
# Enables/disables USB NCM networking based on UsbNcmEnabled param.
# Called by ncm-param-watcher on param changes.
#
# On Dragon Q6A, the a600000.usb (USB-C) DWC3 controller is switched
# from host to device mode via debugfs. WiFi on 8c00000.usb is unaffected.

GADGET=/config/usb_gadget/g1
USB_IF="usb0"
USB_ADDR="192.168.42.2/24"
NCM_PARAM="/data/params/d/UsbNcmEnabled"

# Dragon: debugfs mode switch on USB-C controller
DWC3_MODE="/sys/kernel/debug/usb/a600000.usb/mode"

get_udc() {
  ls /sys/class/udc/ 2>/dev/null | head -1
}

ensure_configfs() {
  if ! mountpoint -q /config; then
    mount -t configfs none /config
  fi
}

ensure_gadget_base() {
  local udc="$1"
  ensure_configfs

  mkdir -p "$GADGET"
  cd "$GADGET" || exit 1

  mkdir -p strings/0x409
  mkdir -p configs/c.1/strings/0x409
  mkdir -p functions/ncm.0

  echo 0x1d6b > idVendor
  echo 0x0103 > idProduct
  echo 250 > configs/c.1/MaxPower

  local serial model
  serial="$(/usr/comma/get-serial.sh)"
  model="$(tr -d '\0' < /sys/firmware/devicetree/base/model 2>/dev/null || true)"

  echo "$serial" > strings/0x409/serialnumber
  echo "comma.ai" > strings/0x409/manufacturer
  echo "$model ($serial)" > strings/0x409/product
  echo "NCM" > configs/c.1/strings/0x409/configuration
}

unbind_gadget() {
  cd "$GADGET" || return 1
  echo "" > UDC 2>/dev/null || true
}

bind_gadget() {
  local udc="$1"
  cd "$GADGET" || return 1
  echo "$udc" > UDC
}

wait_for_usb_if() {
  for i in $(seq 1 30); do
    ip link show "$USB_IF" >/dev/null 2>&1 && return 0
    sleep 0.1
  done
  return 1
}

configure_usb_if() {
  ip link set "$USB_IF" up

  if ! ip addr show dev "$USB_IF" | grep -q "$USB_ADDR"; then
    ip addr show dev "$USB_IF" | awk '/192\.168\.42\./ {print $2}' | while read -r cidr; do
      ip addr del "$cidr" dev "$USB_IF" 2>/dev/null || true
    done
    ip addr add "$USB_ADDR" dev "$USB_IF"
  fi
}

enable_udc() {
  [ -n "$(get_udc)" ] && return 0

  if [ -f "$DWC3_MODE" ]; then
    echo device > "$DWC3_MODE"
    for i in $(seq 1 20); do
      [ -n "$(get_udc)" ] && return 0
      sleep 0.2
    done
    echo "ERROR: UDC did not appear after mode switch"
    return 1
  fi

  echo "ERROR: debugfs mode interface not available"
  return 1
}

disable_udc() {
  if [ -f "$DWC3_MODE" ]; then
    echo host > "$DWC3_MODE"
  fi
}

enable_ncm() {
  if ! enable_udc; then
    echo "ERROR: failed to enable USB device controller"
    return 1
  fi

  local udc
  udc="$(get_udc)"
  ensure_gadget_base "$udc"
  cd "$GADGET" || exit 1

  unbind_gadget

  ln -s functions/ncm.0 configs/c.1/f1 2>/dev/null || true
  echo "NCM" > configs/c.1/strings/0x409/configuration

  bind_gadget "$udc"

  if wait_for_usb_if; then
    configure_usb_if
  else
    echo "WARNING: $USB_IF not present yet."
  fi

  sv up dnsmasq
}

disable_ncm() {
  ensure_configfs

  sv down dnsmasq

  if [ -d "$GADGET" ]; then
    cd "$GADGET" || return
    unbind_gadget
    rm -f configs/c.1/f1 2>/dev/null || true
  fi

  if ip link show "$USB_IF" >/dev/null 2>&1; then
    ip link set "$USB_IF" down 2>/dev/null || true
  fi

  disable_udc
}

if [ -f "$NCM_PARAM" ] && [ "$(< "$NCM_PARAM")" = "1" ]; then
  echo "Enabling USB NCM"
  enable_ncm
else
  echo "Disabling USB NCM"
  disable_ncm
fi
