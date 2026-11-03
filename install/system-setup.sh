#!/usr/bin/env bash
# Idempotent system setup for input-automation (run once per machine, as root):
#   sudo ./install/system-setup.sh
#
# Run this BEFORE `pipx install ...` — the evdev C extension has no prebuilt
# wheel for many Python versions, so building it needs gcc + matching python
# headers. This script installs exactly those (NOT the full development-tools
# group), then does the uinput/udev/input-group setup.
#
# Does:
#   0. Install minimal build deps (gcc, make, matching python3.X-devel)
#   1. Load the uinput kernel module at boot
#   2. Install a udev rule making /dev/uinput writable by the input group
#   3. Add the invoking user to the input group (relogin required)
#   4. Install the GUI desktop entry for the invoking user

set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "error: run as root (sudo $0)" >&2
  exit 1
fi

if ! command -v dnf >/dev/null 2>&1; then
  echo "error: this script currently supports dnf-based systems (Fedora/RHEL)" >&2
  exit 1
fi

# 0. minimal build dependencies for C extensions (evdev, python-uinput)
DEPS=(gcc make)
PYVER="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
if [[ -f "/usr/include/python${PYVER}/Python.h" ]]; then
  echo "python${PYVER} headers already present"
elif dnf -q list "python${PYVER}-devel" >/dev/null 2>&1; then
  DEPS+=("python${PYVER}-devel")
else
  DEPS+=("python3-devel")
fi
echo "installing build deps: ${DEPS[*]}"
dnf install -y "${DEPS[@]}"

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

# 4. desktop entry for the GUI (idempotent; only rewritten when it changed)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_SRC="${SCRIPT_DIR}/input-automation.desktop"
APPS_DIR="$(getent passwd "${TARGET_USER}" | cut -d: -f6)/.local/share/applications"
if [[ -f "${DESKTOP_SRC}" ]]; then
  mkdir -p "${APPS_DIR}"
  if ! cmp -qs "${DESKTOP_SRC}" "${APPS_DIR}/input-automation.desktop"; then
    install -m 0644 "${DESKTOP_SRC}" "${APPS_DIR}/input-automation.desktop"
    echo "installed ${APPS_DIR}/input-automation.desktop"
  fi
fi

echo "system setup complete"
echo "next step:  pipx install git+https://github.com/SebastianDeOliveiraSpitz/input-automate.git@main"
