mkdir -p /dev/mqueue
mountpoint -q /dev/mqueue || mount -o nosuid,noexec,nodev -t mqueue mqueue /dev/mqueue
