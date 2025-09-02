#!/usr/bin/env bash
set -euo pipefail
# tmux から現在のセッション/ウィンドウ情報を取得
INFO="$(tmux display -p -t azazel '#{session_name}:#{window_index} #{window_name}')"
# セッション情報(INFO)を引数として渡し、E-Paper に TMUX セッション名を表示する。
/usr/bin/python3 /home/azazel/Azazel-Zero/py/boot_splash_epd.py "$INFO" >/dev/null 2>&1 || true