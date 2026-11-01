#!/usr/bin/env bash
# Idempotent system setup for input-automation (run once per machine, as root):
#   sudo ./install/system-setup.sh
#
# Does:
#   1. Load the uinput kernel module at boot
#   2. Install a udev rule making /dev/uinput writable by the input group
#   3. Add the invoking user to the input group (relogin required)

set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "error: run as root (sudo $0)" >&2
  exit 1
fi

# 1. uinput module at boot
if ! grep -qs '^uinput$' /etc/modules-load.d/uinput.conf; then
  echo uinput > /etc/modules-load.d/uinput.conf
  echo "added uinput to /etc/modules-load.d/uinput.conf"
fi
modprobe uinput 2>/dev/null || true

# 2. udev rule for /dev/uinput
RULE=/etc/udev/rules.d/80-input-automation.rules
cat > "${RULE}" <<'EOF'
KERNEL=="uinput", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"
EOF
udevadm control --reload
udevadm trigger /dev/uinput 2>/dev/null || true
echo "installed ${RULE}"

# 3. input group for the invoking user
TARGET_USER="${SUDO_USER:-$USER}"
if ! id -nG "${TARGET_USER}" | grep -qw input; then
  usermod -aG input "${TARGET_USER}"
  echo "added ${TARGET_USER} to the input group — relogin required"
fi

echo "system setup complete"
