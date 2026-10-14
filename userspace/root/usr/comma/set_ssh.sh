#!/bin/bash
# USB maintenance remains available when the app disables remote SSH.
set -eu
exec 9>/run/asius-ssh.lock
flock 9
nft list table inet asius_ssh >/dev/null 2>&1 || nft add table inet asius_ssh
SSH_REMOTE_RULE=""
if [ "$(cat /data/params/d/SshEnabled 2>/dev/null || true)" = "0" ]; then
  SSH_REMOTE_RULE='tcp dport 22 iifname != "usb0" drop'
fi
# One nftables transaction replaces both guards without an unprotected interval.
nft -f - <<RULES
flush table inet asius_ssh
add chain inet asius_ssh input { type filter hook input priority -10; policy accept; }
add rule inet asius_ssh input ip daddr 192.168.42.2 tcp dport 22 iifname != "usb0" drop
${SSH_REMOTE_RULE:+add rule inet asius_ssh input $SSH_REMOTE_RULE}
RULES
