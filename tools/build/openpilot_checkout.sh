#!/usr/bin/env bash

OP_REPO="${VAMOS_OPENPILOT_REPO:-https://github.com/asiusai/openpilot.git}"
OP_REF="${VAMOS_OPENPILOT_REF:-master}"
OP_SRC="$DIR/.openpilot"

update_openpilot_checkout() {
  export GIT_TERMINAL_PROMPT=0

  if [ ! -d "$OP_SRC/.git" ]; then
    if [ -e "$OP_SRC" ]; then
      echo "ERROR: $OP_SRC exists but is not an openpilot Git checkout" >&2
      return 1
    fi
    echo "== Cloning openpilot $OP_REF into $OP_SRC =="
    git clone --no-checkout "$OP_REPO" "$OP_SRC"
  else
    if [ -n "$(git -C "$OP_SRC" status --porcelain --untracked-files=all --ignore-submodules=none)" ]; then
      echo "ERROR: refusing to update dirty managed checkout: $OP_SRC" >&2
      git -C "$OP_SRC" status --short >&2
      return 1
    fi
    echo "== Updating cached openpilot $OP_REF checkout =="
    git -C "$OP_SRC" remote set-url origin "$OP_REPO"
  fi

  # Fetching an explicit commit makes release builds reproducible while the
  # default branch ref keeps local development tracking our public fork.
  git -C "$OP_SRC" fetch --prune origin "$OP_REF"
  git -C "$OP_SRC" checkout --detach FETCH_HEAD
  git -C "$OP_SRC" submodule sync --recursive
  git -C "$OP_SRC" submodule update --init --recursive --jobs "$(nproc)"
  git -C "$OP_SRC" lfs pull

  if [ -n "$(git -C "$OP_SRC" status --porcelain --untracked-files=all --ignore-submodules=none)" ]; then
    echo "ERROR: managed openpilot checkout is dirty after update: $OP_SRC" >&2
    git -C "$OP_SRC" status --short >&2
    return 1
  fi
  if git -C "$OP_SRC" submodule status --recursive | grep -q '^[+-U]'; then
    echo "ERROR: openpilot submodules are missing or do not match their gitlinks" >&2
    git -C "$OP_SRC" submodule status --recursive >&2
    return 1
  fi
  git -C "$OP_SRC" lfs fsck
}
