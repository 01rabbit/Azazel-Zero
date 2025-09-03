#!/usr/bin/env bash
set -euo pipefail
AZAZEL_ROOT="${AZAZEL_ROOT:-/home/azazel/Azazel-Zero}"
INFO="$(tmux display -p -t azazel '#{session_name}:#{window_index} #{window_name}' || true)"
/usr/bin/python3 "${AZAZEL_ROOT}/py/boot_splash_epd.py" "${INFO:-}" >/dev/null 2>&1 || true