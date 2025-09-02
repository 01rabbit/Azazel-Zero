#!/usr/bin/env bash
set -euo pipefail
# tmux から現在のセッション/ウィンドウ情報を取得
INFO="$(tmux display -p -t azazel '#{session_name}:#{window_index} #{window_name}')"
# 既存の boot_splash_epd.py をそのまま呼ぶ。必要なら INFO を渡せるよう拡張しても可
/usr/bin/python3 /home/azazel/Azazel-Zero/py/boot_splash_epd.py >/dev/null 2>&1 || true
# もしセッション名も描画したければ、boot_splash_epd.py に簡易引数を追加するか、
# ここで別の描画スクリプト（例: epaper_show_text.py）を呼ぶ運用に。