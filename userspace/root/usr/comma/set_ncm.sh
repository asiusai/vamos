#!/bin/sh
set -eu

case " $(cat /proc/cmdline) " in
  *" vamos.usb_mode=ncm "*) ;;
  *) exit 0 ;;
esac

configfs=/sys/kernel/config
gadget="$configfs/usb_gadget/vamos"

mountpoint -q "$configfs" || mount -t configfs configfs "$configfs"
mkdir -p "$gadget/strings/0x409" "$gadget/configs/c.1/strings/0x409"

printf '0x1d6b' > "$gadget/idVendor"
printf '0x0103' > "$gadget/idProduct"
printf '0x0320' > "$gadget/bcdUSB"
printf '0x0100' > "$gadget/bcdDevice"
printf '%s' "$(/usr/comma/get-serial.sh)" > "$gadget/strings/0x409/serialnumber"
printf '%s' 'Asius' > "$gadget/strings/0x409/manufacturer"
printf '%s' 'Dragon Q6A NCM' > "$gadget/strings/0x409/product"
printf '%s' 'NCM' > "$gadget/configs/c.1/strings/0x409/configuration"
printf '250' > "$gadget/configs/c.1/MaxPower"

mkdir -p "$gadget/functions/ncm.usb0"
ln -s "$gadget/functions/ncm.usb0" "$gadget/configs/c.1/ncm.usb0" 2>/dev/null || true

udc=""
attempt=0
while [ "$attempt" -lt 50 ]; do
  udc="$(find /sys/class/udc -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null | head -n 1)"
  [ -n "$udc" ] && break
  attempt=$((attempt + 1))
  sleep 0.1
done
[ -n "$udc" ] || { echo 'NCM: no USB device controller appeared' >&2; exit 1; }

if [ -z "$(cat "$gadget/UDC")" ]; then
  printf '%s' "$udc" > "$gadget/UDC"
fi

attempt=0
while [ "$attempt" -lt 30 ] && ! ip link show usb0 >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  sleep 0.1
done
ip address replace 192.168.42.2/24 dev usb0
ip link set usb0 up
