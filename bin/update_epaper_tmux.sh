#!/usr/bin/env bash
set -euo pipefail

# 1) config
# /etc/default で上書き可。EPD_PY は azazel-epd.service と同じ規約。
[ -f /etc/default/azazel-zero ] && . /etc/default/azazel-zero || true
AZAZEL_ROOT="${AZAZEL_ROOT:-/home/azazel/Azazel-Zero}"
EPD_PY="${EPD_PY:-${AZAZEL_ROOT}/py/boot_splash_epd.py}"
LOCK="/run/azazel-epd.lock"

# 2) tmux 情報（なければ黙って戻る）
INFO="$(tmux display -p -t azazel '#{session_name}:#{window_index} #{window_name}' 2>/dev/null || true)"
[ -n "${INFO:-}" ] || exit 0

# 3) EPD 更新（排他・非特権でも失敗を許容）
if command -v flock >/dev/null 2>&1; then
  flock -w 0 "$LOCK" /usr/bin/python3 "$EPD_PY" --mode info "$INFO" >/dev/null 2>&1 || true
else
  /usr/bin/python3 "$EPD_PY" --mode info "$INFO" >/dev/null 2>&1 || true
fi

exit 0