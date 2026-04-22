#!/usr/bin/env bash
# Void Linux version - uses sv instead of systemctl

source /etc/profile

# Add venv to PATH for Void
export PATH="/usr/local/venv/bin:$PATH"

SETUP="/usr/comma/setup"
RESET="/usr/comma/reset"
CONTINUE="/data/continue.sh"
INSTALLER="/tmp/installer"
RESET_TRIGGER="/data/__system_reset__"

if [ ! -f /ASIUS ]; then
  echo "waiting for magic"
  for i in {1..200}; do
    # Check for drmfd socket (magic service creates this when ready)
    if [ -S /tmp/drmfd.sock ]; then
      break
    fi
    sleep 0.1
  done

  if [ -S /tmp/drmfd.sock ]; then
    echo "magic ready after ${SECONDS}s"
  else
    echo "timed out waiting for magic, ${SECONDS}s"
  fi
fi

sudo chown comma: /data
sudo chown comma: /data/media

handle_setup_keys () {
  # install default SSH key while still in setup
  if [[ ! -e /data/params/d/GithubSshKeys && ! -e /data/continue.sh ]]; then
    if [ ! -e /data/params/d ]; then
      mkdir -p /data/params/d_tmp
      ln -s /data/params/d_tmp /data/params/d
    fi

    echo -n 1 > /data/params/d/SshEnabled
    echo -n 1 > /data/params/d/UsbNcmEnabled
    cp /usr/comma/setup_keys /data/params/d/GithubSshKeys
  elif [[ ! -e /data/continue.sh ]]; then
    # still in setup — ensure dev access is enabled (handles reboot mid-setup)
    echo -n 1 > /data/params/d/SshEnabled
    echo -n 1 > /data/params/d/UsbNcmEnabled
  elif [[ -e /data/params/d/GithubSshKeys && -e /data/continue.sh ]]; then
    if cmp -s /data/params/d/GithubSshKeys /usr/comma/setup_keys; then
      rm /data/params/d/SshEnabled
      rm /data/params/d/UsbNcmEnabled
      rm /data/params/d/GithubSshKeys
    fi
  fi
}

# factory reset handling (ASIUS: /data lives on the rootfs, not a separate
# mountpoint, so skip the mountpoint check that would falsely trigger recovery)
if [ ! -f /tmp/booted ]; then
  touch /tmp/booted
  if [ -f "$RESET_TRIGGER" ]; then
    echo "launching system reset, reset trigger present"
    rm -f $RESET_TRIGGER
    $RESET
  elif [ "$(cat /sys/class/input/input*/device/touch_count 2>/dev/null | head -1)" -gt 4 ] 2>/dev/null; then
    echo "launching system reset, got taps"
    $RESET --tap-reset
  elif [ ! -f /ASIUS ] && ! mountpoint -q /data; then
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

# Auto-install openpilot if no continue.sh and no openpilot
if [[ ! -f $CONTINUE && ! -d /data/openpilot ]]; then
  echo "No openpilot found, cloning asiusai/openpilot vamos branch..."
  git clone --depth 1 -b vamos https://github.com/asiusai/openpilot.git /data/openpilot
  cat > $CONTINUE << 'CONT'
#!/usr/bin/env bash
cd /data/openpilot
exec /data/openpilot/launch_openpilot.sh
CONT
  chmod +x $CONTINUE
fi

while true; do
  pkill -f "$SETUP"
  handle_setup_keys

  if [ -f $CONTINUE ]; then
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
