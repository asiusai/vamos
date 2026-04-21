#!/bin/bash
# Dragon Q6A control script
# Hardware: 12V via CHA_FAN1 (pwm1 on nct6799 / hwmon5), EDL over direct USB,
# serial on /dev/ttyUSB0, network over LAN/tailscale.
# Usage: dragon.sh [on|off|reboot|ssh]

HWMON=/sys/class/hwmon/hwmon5
OFF_SETTLE_SECS=5

power_off() {
    sudo sh -c "echo 1 > $HWMON/pwm1_enable && echo 0 > $HWMON/pwm1"
    echo "Dragon OFF"
}

power_on() {
    sudo sh -c "echo 1 > $HWMON/pwm1_enable && echo 255 > $HWMON/pwm1"
    echo "Dragon ON"
}

case "${1:-}" in
    on)
        power_on
        ;;
    off)
        power_off
        ;;
    reboot)
        power_off
        sleep "$OFF_SETTLE_SECS"
        power_on
        ;;
    edl)
        exec "$(dirname "$(readlink -f "$0")")/dragon-edl.py"
        ;;
    edl-now)
        exec "$(dirname "$(readlink -f "$0")")/dragon-edl.py" --no-cycle
        ;;
    ssh)
        IP="${2:-dragon1}"
        exec ssh -i ~/.ssh/comma_setup -o StrictHostKeyChecking=no comma@"$IP"
        ;;
    *)
        echo "Usage: dragon.sh [on|off|reboot|edl|edl-now|ssh]"
        echo ""
        echo "  on       Power on via fan header"
        echo "  off      Power off"
        echo "  reboot   Power off, settle, power on"
        echo "  edl      Cold cycle + navigate BIOS menu to 'Reboot into EDL/9008'"
        echo "  edl-now  Skip power cycle, just navigate (BIOS must be at main menu)"
        echo "  ssh      SSH to dragon1 (once booted)"
        echo ""
        echo "Flashing: ./vamos flash system (Dragon must be in EDL first)."
        echo "Serial:   dragon-uart.py (wrapper) or picocom /dev/ttyUSB0 -b115200."
        ;;
esac
