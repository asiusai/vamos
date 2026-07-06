#!/usr/bin/env bash
set -euo pipefail

# Host-side Dragon OS04 raw bench runner.
#
# This intentionally uses /tmp/camss_rdi_probe directly. It does not start
# camerad, Spectra/CamX, or the openpilot camera stack.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAMOS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REMOTE_HOST="${REMOTE_HOST:-comma@192.168.42.2}"
REMOTE_PROBE="${REMOTE_PROBE:-/tmp/camss_rdi_probe}"
REMOTE_REGS="${REMOTE_REGS:-/tmp/os04c10_camthink_2lane_raw10.regs}"
LOCAL_REGS="${LOCAL_REGS:-/tmp/os04c10_camthink_2lane_raw10.regs}"
SSH_KEY="${SSH_KEY:-${HOME}/.ssh/comma_setup}"

SSH_OPTS=(
  -i "${SSH_KEY}"
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o GlobalKnownHostsFile=/dev/null
  -o LogLevel=ERROR
)

CAMS=(cam1 cam2 cam3)
REBUILD=0
SKIP_TESTGEN=0
FRAMES=1
POLLS=30
POLL_MS=100
OVERALL_RC=0
REG_OVERRIDES=()

usage() {
  cat <<'USAGE'
Usage:
  tools/os04_raw_bench.sh [options]

Options:
  --cam cam1|cam2|cam3|all  Camera to test. Default: all.
  --rebuild                 Copy and rebuild camss_rdi_probe on Dragon.
  --skip-testgen            Skip CSID internal testgen control run.
  --frames N                External sensor frame target. Default: 1.
  --polls N                 Poll iterations. Default: 30.
  --poll-ms N               Poll timeout in ms. Default: 100.
  --override addr=value     Append a sensor register override to the init file.
                            May be repeated. Example: --override 0x3501=0x01
  -h, --help                Show this help.

The external-sensor run samples CSID RX/RDI registers through --devmem-read.
Nonzero CSID IRQ/status with frames=0 means the physical change affected CSI
decode. All-zero CSID IRQ/status still means the Dragon receiver is seeing no
valid packets.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cam)
      [[ $# -ge 2 ]] || { echo "--cam needs a value" >&2; exit 1; }
      if [[ "$2" == "all" ]]; then
        CAMS=(cam1 cam2 cam3)
      else
        CAMS=("$2")
      fi
      shift 2
      ;;
    --rebuild)
      REBUILD=1
      shift
      ;;
    --skip-testgen)
      SKIP_TESTGEN=1
      shift
      ;;
    --frames)
      [[ $# -ge 2 ]] || { echo "--frames needs a value" >&2; exit 1; }
      FRAMES="$2"
      shift 2
      ;;
    --polls)
      [[ $# -ge 2 ]] || { echo "--polls needs a value" >&2; exit 1; }
      POLLS="$2"
      shift 2
      ;;
    --poll-ms)
      [[ $# -ge 2 ]] || { echo "--poll-ms needs a value" >&2; exit 1; }
      POLL_MS="$2"
      shift 2
      ;;
    --override)
      [[ $# -ge 2 ]] || { echo "--override needs addr=value" >&2; exit 1; }
      REG_OVERRIDES+=(--override "$2")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

for cam in "${CAMS[@]}"; do
  case "${cam}" in
    cam1|cam2|cam3) ;;
    *) echo "invalid camera: ${cam}" >&2; exit 1 ;;
  esac
done

ssh_dragon() {
  ssh "${SSH_OPTS[@]}" "${REMOTE_HOST}" "$@"
}

scp_to_dragon() {
  scp "${SSH_OPTS[@]}" "$1" "${REMOTE_HOST}:$2"
}

csid_reads_for_cam() {
  case "$1" in
    cam1|cam2)
      # CSID0, SC7280/QCS6490 3xx register layout.
      echo "--devmem-read 0x0acb3020 --devmem-read 0x0acb3040 --devmem-read 0x0acb3100 --devmem-read 0x0acb3104 --devmem-read 0x0acb3300 --devmem-read 0x0acb3308"
      ;;
    cam3)
      # CSID1, same offsets as CSID0.
      echo "--devmem-read 0x0acba020 --devmem-read 0x0acba040 --devmem-read 0x0acba100 --devmem-read 0x0acba104 --devmem-read 0x0acba300 --devmem-read 0x0acba308"
      ;;
  esac
}

cd "${VAMOS_DIR}"

./dragon.py status >/dev/null

python3 tools/os04c10_camthink_bringup.py --dump-regs "${REG_OVERRIDES[@]}" > "${LOCAL_REGS}"
scp_to_dragon "${LOCAL_REGS}" "${REMOTE_REGS}"

if [[ "${REBUILD}" == "1" ]]; then
  scp_to_dragon "tools/camss_rdi_probe.c" "/tmp/camss_rdi_probe.c"
  ssh_dragon "cc -O2 -Wall /tmp/camss_rdi_probe.c -o ${REMOTE_PROBE}"
fi

ssh_dragon "test -x ${REMOTE_PROBE}"

for cam in "${CAMS[@]}"; do
  echo "=== ${cam}: raw receiver control ==="
  if [[ "${SKIP_TESTGEN}" != "1" ]]; then
    ssh_dragon "sudo timeout 10s ${REMOTE_PROBE} --${cam} --raw10 --csid-testgen 1 --frames 1 --polls 20 --poll-ms 50 --out /tmp/${cam}-csid-testgen.raw"
  fi

  echo "=== ${cam}: external OS04 raw ==="
  reads="$(csid_reads_for_cam "${cam}")"
  set +e
  ssh_dragon "sudo timeout 20s ${REMOTE_PROBE} --${cam} --raw10 --init-reg-file ${REMOTE_REGS} --frames ${FRAMES} --polls ${POLLS} --poll-ms ${POLL_MS} --skip-stream-readback ${reads} --out /tmp/${cam}-os04-raw.raw"
  rc=$?
  set -e
  echo "=== ${cam}: external raw exit ${rc} ==="
  if [[ "${rc}" != "0" ]]; then
    OVERALL_RC="${rc}"
  fi
done

exit "${OVERALL_RC}"
