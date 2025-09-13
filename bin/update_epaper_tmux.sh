#!/usr/bin/env bash
set -euo pipefail

# 1) config
# /etc/default で上書き可。EPD_PY は azazel-epd.service と同じ規約。
[ -f /etc/default/azazel-zero ] && . /etc/default/azazel-zero || true
AZAZEL_ROOT="${AZAZEL_ROOT:-/home/azazel/Azazel-Zero}"
EPD_PY="${EPD_PY:-${AZAZEL_ROOT}/py/boot_splash_epd.py}"
LOCK="/run/azazel-epd.lock"

# スクリプトが無ければ静かに撤退
[ -f "$EPD_PY" ] || exit 0

# 2) tmux 情報（なければ黙って戻る）
INFO="$(tmux display -p -t azazel '#{session_name}:#{window_index} #{window_name}' 2>/dev/null || true)"
[ -n "${INFO:-}" ] || exit 0

# 3) 早期ブート猶予（アニメを上書きしないため）。環境変数で調整可。
#    例: BOOT_EPD_GRACE=1.0 （秒）。小数点以下は切り捨てで比較。
UPTIME_RAW="$(cut -d' ' -f1 /proc/uptime 2>/dev/null || echo 0)"
UP_S="${UPTIME_RAW%.*}"
GRACE_RAW="${BOOT_EPD_GRACE:-0}"
GRACE_S="${GRACE_RAW%.*}"
if [ "${GRACE_S:-0}" -gt 0 ] && [ "${UP_S:-0}" -lt "${GRACE_S:-0}" ]; then
  exit 0
fi

# 4) 連打抑止（最低インターバル）。環境変数 MIN_EPD_INTERVAL（秒, デフォルト1）
STAMP="/run/azazel-epd.stamp"
MIN_EPD_INTERVAL="${MIN_EPD_INTERVAL:-1}"
NOW="$(date +%s)"
if [ -f "$STAMP" ]; then
  LAST="$(cat "$STAMP" 2>/dev/null || echo 0)"
  if [ $(( NOW - LAST )) -lt "${MIN_EPD_INTERVAL}" ]; then
    exit 0
  fi
fi
echo "$NOW" > "$STAMP" 2>/dev/null || true

# 5) EPD 更新（排他・非特権でも失敗を許容）
if command -v flock >/dev/null 2>&1; then
  flock -w 0 "$LOCK" /usr/bin/python3 "$EPD_PY" --mode info --no-clear --gentle "$INFO" >/dev/null 2>&1 || true
else
  /usr/bin/python3 "$EPD_PY" --mode info --no-clear --gentle "$INFO" >/dev/null 2>&1 || true
fi

exit 0