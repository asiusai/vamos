#!/bin/bash

# don't restart whole SoC on subsystem crash
for i in {0..7}; do
  echo "related" | sudo tee /sys/bus/msm_subsys/devices/subsys${i}/restart_level
done

# bring all CPU cores online
for i in {0..7}; do
  echo 1 | sudo tee /sys/devices/system/cpu/cpu${i}/online 2>/dev/null || true
done

# set all CPU policies to performance governor at max frequency
for p in 0 4 7; do
  echo performance | sudo tee /sys/devices/system/cpu/cpufreq/policy${p}/scaling_governor 2>/dev/null || true
done

# set GPU to max frequency (userspace governor, Adreno 643L max = 812MHz)
echo userspace | sudo tee /sys/class/devfreq/3d00000.gpu/governor 2>/dev/null || true
echo 812000000 | sudo tee /sys/class/devfreq/3d00000.gpu/userspace/set_freq 2>/dev/null || true
