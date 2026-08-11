alias l='ls -lah'
alias gup='git pull --rebase'
alias gl='git pull'
alias gp='git push'
alias gcam='git commit -a -m'
alias gst='git status'
alias gco='git checkout'
alias gsu='git submodule update'

# openpilot has used both layouts. Resolve at call time so the helpers keep
# working across an openpilot update instead of baking one path into vamOS.
dump() {
  local candidate
  for candidate in \
    /data/openpilot/tools/scripts/dump.py \
    /data/openpilot/openpilot/tools/scripts/dump.py; do
    if [ -x "$candidate" ]; then
      "$candidate" "$@"
      return $?
    fi
  done
  echo "dump: openpilot dump.py not found" >&2
  return 127
}

op() {
  local candidate
  for candidate in \
    /data/openpilot/tools/op.sh \
    /data/openpilot/openpilot/tools/op.sh; do
    if [ -x "$candidate" ]; then
      "$candidate" "$@"
      return $?
    fi
  done
  echo "op: openpilot op.sh not found" >&2
  return 127
}
