#!/bin/bash

start_remoteproc() {
  local expected_name=$1
  local remoteproc state

  for remoteproc in /sys/class/remoteproc/remoteproc*; do
    [ -e "$remoteproc/name" ] || continue
    [ "$(cat "$remoteproc/name")" = "$expected_name" ] || continue

    state=$(cat "$remoteproc/state")
    case "$state" in
      running|attached)
        echo "$expected_name remote processor already $state"
        return 0
        ;;
      offline)
        echo "starting $expected_name remote processor"
        echo start | sudo tee "$remoteproc/state" >/dev/null || return 1
        ;;
      *)
        echo "waiting for $expected_name remote processor (state: $state)"
        ;;
    esac

    for _ in {1..100}; do
      state=$(cat "$remoteproc/state")
      case "$state" in
        running|attached)
          echo "$expected_name remote processor $state"
          return 0
          ;;
      esac
      sleep 0.1
    done
    echo "failed to start $expected_name remote processor (state: $state)" >&2
    return 1
  done

  echo "$expected_name remote processor not found" >&2
  return 1
}

# The remoteproc drivers probe before the real root filesystem is mounted, so
# their first firmware request can fail with ENOENT. Retry after runit starts,
# when the pinned ADSP and CDSP firmware is available under /lib/firmware.
start_remoteproc adsp || true
start_remoteproc cdsp || true

if [ "${1:-}" = "--remoteprocs-only" ]; then
  exit 0
fi

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
