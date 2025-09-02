#!/usr/bin/env bash
set -euo pipefail

SESSION="azazel"
MENU="python3 /home/azazel/Azazel-Zero/py/azazel_menu.py"   # 後で用意。暫定なら bash -l でも可

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux new-session -d -s "$SESSION" "$MENU"
  # ステータスバー不要なら
  tmux set-option -t "$SESSION" status off
  # ウィンドウ選択時に電子ペーパー更新（後述のスクリプトを呼ぶ）
  tmux set-hook -t "$SESSION" after-select-window "run-shell '/home/azazel/Azazel-Zero/bin/update_epaper_tmux.sh'"
fi