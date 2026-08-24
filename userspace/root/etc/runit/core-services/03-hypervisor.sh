#!/bin/sh

[ -f /ASIUS ] || exit 0

if [ ! -e /dev/kvm ]; then
  if [ ! -d /sys/firmware/efi ]; then
    echo "WARN: U-Boot did not complete the Dragon Gunyah EL2 takeover"
    exit 0
  fi
  echo "=> EL2 is unavailable; arming the Dragon UEFI Hypervisor Override"
fi
/usr/bin/vamos-hypervisor auto
