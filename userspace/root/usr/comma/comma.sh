#!/usr/bin/env bash
# Void Linux version - uses sv instead of systemctl

source /etc/profile
DEVICE_TYPE="$(device-type)"

# Add venv to PATH for Void
export PATH="/usr/local/venv/bin:$PATH"

SETUP="/usr/comma/setup"
RESET="/usr/comma/reset"
CONTINUE="/data/continue.sh"
INSTALLER="/tmp/installer"
RESET_TRIGGER="/data/__system_reset__"

sudo chown comma: /data
sudo chown comma: /data/media

handle_setup_keys () {
  # Keep SSH available during setup, but do not install static setup keys.
  if [[ ! -e /data/continue.sh ]]; then
    if [ ! -e /data/params/d ]; then
      mkdir -p /data/params/d_tmp
      ln -s /data/params/d_tmp /data/params/d
    fi

    echo -n 1 > /data/params/d/SshEnabled
    echo -n 1 > /data/params/d/UsbNcmEnabled
  fi
}

# factory reset handling. Legacy Asius installations did not have a separate
# userdata partition, so keep accepting that layout until it is migrated.
if [ ! -f /tmp/booted ]; then
  touch /tmp/booted
  if [ -f "$RESET_TRIGGER" ]; then
    echo "launching system reset, reset trigger present"
    rm -f $RESET_TRIGGER
    $RESET
  elif [ "$(cat /sys/class/input/input*/device/touch_count 2>/dev/null | head -1)" -gt 4 ] 2>/dev/null; then
    echo "launching system reset, got taps"
    $RESET --tap-reset
  elif [ "$DEVICE_TYPE" != "v1" ] && ! mountpoint -q /data; then
    echo "userdata not mounted. loading system reset"
    $RESET --recover
  fi
fi

# setup /data/tmp
rm -rf /data/tmp
mkdir -p /data/tmp

# symlink vscode to userdata
mkdir -p /data/tmp/vscode-server
ln -s /data/tmp/vscode-server ~/.vscode-server
ln -s /data/tmp/vscode-server ~/.cursor-server
ln -s /data/tmp/vscode-server ~/.windsurf-server

# Initial Dragon images contain a git-complete openpilot checkout. OTA rootfs
# images never contain /data, so an existing checkout remains untouched.
if [[ ! -s $CONTINUE ]]; then
  rm -f "$CONTINUE"
  if git -C /data/openpilot rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    cat > "$CONTINUE.tmp" << 'CONT'
#!/usr/bin/env bash
cd /data/openpilot
exec /data/openpilot/launch_openpilot.sh
CONT
    chmod +x "$CONTINUE.tmp"
    mv "$CONTINUE.tmp" "$CONTINUE"
  else
    echo "ERROR: built-in openpilot checkout is missing; refusing to create an invalid continue.sh"
  fi
fi

while true; do
  pkill -f "$SETUP"
  handle_setup_keys

  if [ -f $CONTINUE ]; then
    if [[ "$DEVICE_TYPE" == "v1" ]]; then
      mkdir -p /data/params/d
      rm -f /data/params/d/AthenadPairingUntil
      for key in AlphaLongitudinalEnabled ExperimentalMode ExperimentalModeConfirmed; do
        [[ -e "/data/params/d/$key" ]] || echo -n 1 > "/data/params/d/$key"
      done
    fi

    if [ -f /run/vamos-trial-boot ]; then
      echo "committing healthy vamOS trial boot"
      if ! sudo /usr/bin/vamos-boot commit; then
        echo "vamOS trial commit failed; waiting for watchdog rollback"
        while true; do sleep 60; done
      fi
    fi
    exec "$CONTINUE"
  fi

  sudo abctl --set_success

  # cleanup installers from previous runs
  rm -f $INSTALLER
  pkill -f $INSTALLER

  # run setup and wait for installer
  $SETUP &
  echo "waiting for installer"
  while [ ! -f $INSTALLER ]; do
    sleep 0.1
  done

  # run installer and wait for continue.sh
  chmod +x $INSTALLER
  $INSTALLER &
  echo "running installer"
  while [ ! -f $CONTINUE ] && ps -p $! > /dev/null; do
    sleep 0.1
  done
done
