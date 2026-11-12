#!/bin/sh
set -eu

# Preserve the distro authentication stack; add only a session hook.
hook='session optional pam_exec.so /usr/bin/vamos-resources pam-session'
for service in sshd login; do
  test -f "/etc/pam.d/$service"
  grep -qxF "$hook" "/etc/pam.d/$service" || printf '\n%s\n' "$hook" >> "/etc/pam.d/$service"
done
chmod 755 /usr/bin/vamos-resources
