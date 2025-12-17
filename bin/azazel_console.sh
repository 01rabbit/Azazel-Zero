#!/usr/bin/env bash

set -euo pipefail

# 共通設定の読込（存在しなくても続行）
[ -f /etc/default/azazel-zero ] && . /etc/default/azazel-zero || true
AZAZEL_ROOT="${AZAZEL_ROOT:-/home/azazel/Azazel-Zero}"

SESSION="azazel"
MENU="python3 ${AZAZEL_ROOT}/py/azazel_menu.py"   # 後で用意。暫定なら bash -l でも可
STATUS="python3 ${AZAZEL_ROOT}/py/azazel_status.py"

# Ensure environment variables for curses/emoji
export TERM=xterm-256color
: "${LANG:=C.UTF-8}"
: "${LC_ALL:=$LANG}"
: "${LC_CTYPE:=$LANG}"
export LANG LC_ALL LC_CTYPE

# Safe HOME and PATH for systemd environments
: "${HOME:=/home/azazel}"
export HOME
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:$PATH"

# Ensure tmux server, then create or replace the session idempotently
set +e
tmux start-server
tmux has-session -t "$SESSION" 2>/dev/null
HAS=$?
if [ "$HAS" -ne 0 ]; then
  # Create session with top pane running the status renderer
  CMD_STATUS="bash -lc \"$STATUS; exec bash -l\""
  tmux new-session -Ad -s "$SESSION" -n status "$CMD_STATUS"
  RC=$?
  if [ "$RC" -eq 0 ]; then
    # Split vertically and run the menu in the bottom pane (70% height)
    CMD_MENU="bash -lc \"$MENU; exec bash -l\""
    tmux split-window -v -p 80 -t "$SESSION":0 "$CMD_MENU"
    # Focus the bottom (menu) pane for interaction
    tmux select-pane -t "$SESSION":0.1 2>/dev/null || true
  fi
else
  RC=0
fi
set -e

if [ "${RC:-1}" -eq 0 ]; then
  # Best-effort session configuration; never fail hard here
  tmux set-option   -t "$SESSION" status off               2>/dev/null || true
  tmux set-option   -t "$SESSION" -g escape-time 0          2>/dev/null || true
  tmux bind-key     -t "$SESSION" -n C-q  detach-client     2>/dev/null || true
  tmux bind-key     -t "$SESSION" -n F12 detach-client      2>/dev/null || true
  # Keep the server/session alive and panes visible even if commands exit
  tmux set-option   -g exit-empty off                     2>/dev/null || true
  tmux set-option   -t "$SESSION" remain-on-exit on       2>/dev/null || true
  tmux set-option   -g detach-on-destroy off              2>/dev/null || true
  tmux set-environment -t "$SESSION" TERM "$TERM"          2>/dev/null || true
  tmux set-environment -t "$SESSION" LANG "$LANG"          2>/dev/null || true
  tmux set-environment -t "$SESSION" LC_CTYPE "$LC_CTYPE"  2>/dev/null || true
  tmux set-hook -t "$SESSION" after-select-window "run-shell -b '/usr/local/bin/update_epaper_tmux.sh'" 2>/dev/null || true
  tmux select-pane -t "$SESSION":0.1 2>/dev/null || true
fi