#!/bin/sh
set -e
export LC_ALL=C

# Params stores DeviceName as UTF-8 text. /data is mounted before this runs.
DEVICE_NAME=$(cat /data/params/d/DeviceName 2>/dev/null || true)
DEVICE_HOSTNAME=$(printf '%s' "$DEVICE_NAME" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-//; s/-$//' | cut -c1-63 | sed 's/-$//')
[ -n "$DEVICE_HOSTNAME" ] || DEVICE_HOSTNAME=asius-v0

printf "hostname: '%s'\n" "$DEVICE_HOSTNAME"
hostname "$DEVICE_HOSTNAME"
