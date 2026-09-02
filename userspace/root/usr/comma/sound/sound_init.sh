#!/bin/bash

if [ -f /ASIUS ]; then
  echo "waiting for Dragon sound card to come online"
  while [ ! -e /dev/snd/controlC0 ]; do
    sleep 0.01
  done
  echo "Dragon sound card online"

  chgrp audio /dev/snd/*
  chmod 660 /dev/snd/*

  while ! amixer -D hw:0 controls 2>/dev/null | \
      grep -q "RX_CODEC_DMA_RX_0 Audio Mixer MultiMedia1"; do
    sleep 0.05
  done
  echo "Dragon AUX and Panda playback controls ready"

  # Keep both physical outputs described by the kernel/topology. Panda remains
  # the compatibility default; writing "aux" to /data/vamos/audio-output
  # selects the Dragon headphone output on the next service start.
  audio_output="$(cat /data/vamos/audio-output 2>/dev/null || true)"
  if [ "$audio_output" = "aux" ]; then
    amixer -q -D hw:0 cset name="PRIMARY_MI2S_RX Audio Mixer MultiMedia1" 0
    amixer -q -D hw:0 cset name="RX_CODEC_DMA_RX_0 Audio Mixer MultiMedia1" 1
  else
    amixer -q -D hw:0 cset name="RX_CODEC_DMA_RX_0 Audio Mixer MultiMedia1" 0
    amixer -q -D hw:0 cset name="PRIMARY_MI2S_RX Audio Mixer MultiMedia1" 1
  fi

  # These are the upstream Dragon Q6A UCM settings, applied here because
  # vamOS does not run a desktop UCM session.
  amixer -q -D hw:0 cset name="HPHL_RDAC Switch" 1
  amixer -q -D hw:0 cset name="HPHR_RDAC Switch" 1
  amixer -q -D hw:0 cset name="HPHL Switch" 1
  amixer -q -D hw:0 cset name="HPHR Switch" 1
  amixer -q -D hw:0 cset name="HPHR_COMP Switch" 0
  amixer -q -D hw:0 cset name="HPHL_COMP Switch" 0
  amixer -q -D hw:0 cset name="CLSH Switch" 1
  amixer -q -D hw:0 cset name="LO Switch" 1
  amixer -q -D hw:0 cset name="RX HPH Mode" CLS_H_LOHIFI
  amixer -q -D hw:0 cset name="RX_HPH PWR Mode" LOHIFI
  amixer -q -D hw:0 cset name="RX_MACRO RX0 MUX" AIF1_PB
  amixer -q -D hw:0 cset name="RX_MACRO RX1 MUX" AIF1_PB
  amixer -q -D hw:0 cset name="RX INT0_1 MIX1 INP0" RX0
  amixer -q -D hw:0 cset name="RX INT1_1 MIX1 INP0" RX1
  amixer -q -D hw:0 cset name="RX INT0 DEM MUX" CLSH_DSM_OUT
  amixer -q -D hw:0 cset name="RX INT1 DEM MUX" CLSH_DSM_OUT

  amixer -q -D hw:0 cset name="MultiMedia3 Mixer PRIMARY_MI2S_TX" 1
  exit 0
fi

/usr/comma/sound/adsp-start.sh

echo "waiting for sound card to come online"
while [ ! -d /proc/asound/sdm845tavilsndc ] || [ "$(cat /proc/asound/card0/state 2> /dev/null)" != "ONLINE" ] ; do
  sleep 0.01
done
echo "sound card online"

# Fix permissions for audio group
chgrp audio /dev/snd/*
chmod 660 /dev/snd/*

while ! /usr/comma/sound/tinymix controls | grep -q "SEC_MI2S_RX Audio Mixer MultiMedia1"; do
  sleep 0.01
done
echo "tinymix controls ready"

/usr/comma/sound/tinymix set "SEC_MI2S_RX Audio Mixer MultiMedia1" 1
if grep -q mici /sys/firmware/devicetree/base/model; then
  /usr/comma/sound/tinymix set "MultiMedia1 Mixer SEC_MI2S_TX" 1
else
  /usr/comma/sound/tinymix set "MultiMedia1 Mixer TERT_MI2S_TX" 1
  /usr/comma/sound/tinymix set "TERT_MI2S_TX Channels" Two
fi

# setup the amplifier registers
/usr/local/venv/bin/python /usr/comma/sound/amplifier.py
