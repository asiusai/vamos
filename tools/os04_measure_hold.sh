#!/usr/bin/env bash
set -euo pipefail

# Host-side helper for rail/MIPI DMM measurements.
# It starts one OS04 with the CamThink two-lane table and holds the sensor in
# stream state. This does not use camerad, Spectra/CamX, or openpilot.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAMOS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REMOTE_HOST="${REMOTE_HOST:-comma@192.168.42.2}"
REMOTE_HELPER="${REMOTE_HELPER:-/tmp/os04c10_camthink_bringup.py}"
SSH_KEY="${SSH_KEY:-${HOME}/.ssh/comma_setup}"

SSH_OPTS=(
  -i "${SSH_KEY}"
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o GlobalKnownHostsFile=/dev/null
  -o LogLevel=ERROR
)

CAM="cam3"
HOLD="180"
RECOVER=0

usage() {
  cat <<'USAGE'
Usage:
  tools/os04_measure_hold.sh [options]

Options:
  --cam cam1|cam2|cam3   Camera to hold. Default: cam3.
  --hold SECONDS         Started-state hold time. Default: 180.
  --recover              Rebind sensor driver after the hold.
  -h, --help             Show this help.

Default behavior leaves the sensor started after the hold, so rails/MIPI can
still be checked briefly. Use --recover when you want the script to cleanly
unbind/rebind the sensor after measurements.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cam)
      [[ $# -ge 2 ]] || { echo "--cam needs a value" >&2; exit 1; }
      CAM="$2"
      shift 2
      ;;
    --hold)
      [[ $# -ge 2 ]] || { echo "--hold needs a value" >&2; exit 1; }
      HOLD="$2"
      shift 2
      ;;
    --recover)
      RECOVER=1
      shift
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

case "${CAM}" in
  cam1|cam2|cam3) ;;
  *) echo "invalid camera: ${CAM}" >&2; exit 1 ;;
esac

cd "${VAMOS_DIR}"
./dragon.py status >/dev/null

scp \
  "${SSH_OPTS[@]}" \
  "tools/os04c10_camthink_bringup.py" \
  "${REMOTE_HOST}:${REMOTE_HELPER}"

cmd=(
  "sudo" "python3" "${REMOTE_HELPER}"
  "--camera" "${CAM}"
  "--check" "key"
  "--post-start-check" "none"
  "--hold" "${HOLD}"
  "--no-stop"
)

if [[ "${RECOVER}" == "1" ]]; then
  cmd+=("--recover")
fi

printf 'Starting %s for %ss. Measure rails/MIPI now.\n' "${CAM}" "${HOLD}"
ssh "${SSH_OPTS[@]}" "${REMOTE_HOST}" "$(printf '%q ' "${cmd[@]}")"
