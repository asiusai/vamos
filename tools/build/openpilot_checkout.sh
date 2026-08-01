#!/usr/bin/env bash

OP_REPO="https://github.com/asiusai/openpilot.git"
OP_BRANCH="master"
OP_SRC="$DIR/.openpilot"

update_openpilot_checkout() {
  export GIT_TERMINAL_PROMPT=0

  if [ ! -d "$OP_SRC/.git" ]; then
    if [ -e "$OP_SRC" ]; then
      echo "ERROR: $OP_SRC exists but is not an openpilot Git checkout" >&2
      return 1
    fi
    echo "== Cloning openpilot $OP_BRANCH into $OP_SRC =="
    git clone --branch "$OP_BRANCH" --single-branch --recurse-submodules "$OP_REPO" "$OP_SRC"
  else
    if [ -n "$(git -C "$OP_SRC" status --porcelain --untracked-files=all --ignore-submodules=none)" ]; then
      echo "ERROR: refusing to update dirty managed checkout: $OP_SRC" >&2
      git -C "$OP_SRC" status --short >&2
      return 1
    fi
    echo "== Updating cached openpilot $OP_BRANCH checkout =="
    git -C "$OP_SRC" remote set-url origin "$OP_REPO"
    git -C "$OP_SRC" config remote.origin.fetch "+refs/heads/$OP_BRANCH:refs/remotes/origin/$OP_BRANCH"
    git -C "$OP_SRC" fetch --prune origin
    if git -C "$OP_SRC" show-ref --verify --quiet "refs/heads/$OP_BRANCH"; then
      # This is a clean, managed build cache, so align it after intentional
      # history rewrites such as rebasing the integration branch.
      git -C "$OP_SRC" checkout --detach "origin/$OP_BRANCH"
      git -C "$OP_SRC" branch --force "$OP_BRANCH" "origin/$OP_BRANCH"
      git -C "$OP_SRC" checkout "$OP_BRANCH"
    else
      git -C "$OP_SRC" checkout --track -b "$OP_BRANCH" "origin/$OP_BRANCH"
    fi
    git -C "$OP_SRC" submodule sync --recursive
    git -C "$OP_SRC" submodule update --init --recursive --jobs "$(nproc)"
    git -C "$OP_SRC" lfs pull
  fi

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
