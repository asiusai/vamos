#!/bin/sh

[ -f /ASIUS ] || exit 0

if [ ! -e /dev/kvm ]; then
  echo "=> EL2 is unavailable; arming the Dragon UEFI Hypervisor Override"
fi
/usr/bin/vamos-hypervisor auto
