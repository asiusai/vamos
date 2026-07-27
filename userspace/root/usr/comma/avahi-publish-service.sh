#!/bin/bash

# Read the raw device model
RAW_MODEL=$(tr -d '\0' < /sys/firmware/devicetree/base/model)

# Extract the short device type, preserving "v1" while keeping legacy
# models like "mici" and "tici" stable.
MODEL="${RAW_MODEL#comma }"
MODEL="${MODEL#Asius }"
MODEL="${MODEL#asius }"
MODEL=$(printf '%s\n' "$MODEL" | awk '{print tolower($1)}')

[ -z "$MODEL" ] && MODEL="unknown"

HOSTNAME=$(hostname)
SERVICE_NAME="comma SSH - $MODEL - [$HOSTNAME]"

ALIAS_FILE="/data/params/d_tmp/ApiCache_Device"
ALIAS=$(jq -e -r '.alias // empty' "$ALIAS_FILE" 2>/dev/null | grep -E '\S' || echo "$RAW_MODEL")

# Publish avahi service
exec avahi-publish -s "$SERVICE_NAME" _ssh._tcp 22 "vendor=comma" "device=comma" "model=$MODEL" "alias=$ALIAS"
