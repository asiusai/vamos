#!/bin/sh
set -e

DONGLE_ID=""
if [ -f /data/params/d/DongleId ]; then
  DONGLE_ID="$(cat /data/params/d/DongleId)"
fi

if [ -n "$DONGLE_ID" ]; then
  sysctl kernel.hostname="comma-$DONGLE_ID"
else
  SERIAL="$(/usr/comma/get-serial.sh)"
  sysctl kernel.hostname="comma-${SERIAL:-unknown}"
fi
