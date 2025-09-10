#!/usr/bin/env bash
set -euo pipefail

SESSION="azazel"
MENU="python3 /home/azazel/Azazel-Zero/py/azazel_menu.py"   # 後で用意。暫定なら bash -l でも可

# Ensure environment variables for curses/emoji
export TERM=xterm-256color
export LANG=en_US.UTF-8
export LC_CTYPE=en_US.UTF-8

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux new-session -d -s "$SESSION" "$MENU"
  # ステータスバー不要なら
  tmux set-option -t "$SESSION" status off
  # pass env to session so azazel_menu.py shows header/icons properly
  tmux set-environment -t "$SESSION" TERM "$TERM"
  tmux set-environment -t "$SESSION" LANG "$LANG"
  tmux set-environment -t "$SESSION" LC_CTYPE "$LC_CTYPE"
  # quick detach keys
  tmux set-option -t "$SESSION" -g escape-time 0
  tmux bind-key   -t "$SESSION" -n C-q  detach-client
  tmux bind-key   -t "$SESSION" -n F12  detach-client
  # ウィンドウ選択時に電子ペーパー更新
  tmux set-hook -t "$SESSION" after-select-window "run-shell '/home/azazel/Azazel-Zero/bin/update_epaper_tmux.sh'"
fi