

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Azazel-Zero - Minimal curses menu launcher
- Runs inside the persistent tmux session as the entry console
- Launches tools (blocking) and returns to the menu on exit
- Keys: ↑/↓ or j/k to move, Enter to run, r to redraw, q/ESC to quit
"""

import curses
import os
import shutil
import subprocess
import sys
from typing import List, Tuple, Optional

# Resolve repo root from this file location
HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))

# Tool commands
SSID_TOOL = ["/usr/bin/python3", os.path.join(HERE, "ssid_list.py")]
# Placeholder for future tools
DELAY_TOOL = ["/usr/bin/python3", os.path.join(HERE, "delay_tool.py")]  # if not present, we gray it out

# Optional: e-paper refresh script (non-fatal if missing)
EPAPER = ["/usr/bin/python3", os.path.join(HERE, "boot_splash_epd.py")]

# Menu entries: (label, command or None)
MENU: List[Tuple[str, Optional[List[str]]]] = [
    ("Wi‑Fi Selector (ssid_list.py)", SSID_TOOL),
    ("Delay Control Tool (coming soon)", DELAY_TOOL if os.path.exists(DELAY_TOOL[-1]) else None),
    ("Exit", None),
]

HELP = "↑/↓ or j/k: move   Enter: run   r: redraw   q: quit"


def _exists(cmd: List[str]) -> bool:
    if cmd is None:
        return False
    exe = cmd[0]
    if os.path.isabs(exe):
        return os.path.exists(exe)
    return shutil.which(exe) is not None


def _safe_run(cmd: List[str]) -> int:
    """Run a tool in the foreground, then try to refresh e‑paper on return."""
    try:
        return subprocess.call(cmd)
    finally:
        _update_epaper()


def _update_epaper() -> None:
    """Best‑effort e‑paper refresh (non‑fatal)."""
    try:
        if os.path.exists(EPAPER[-1]):
            # Pass tmux session/window info if available
            info = _tmux_info()
            if info:
                subprocess.run(EPAPER + [info], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.run(EPAPER, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _tmux_info() -> str:
    """Return current tmux session:window name for display, if running under tmux."""
    if "TMUX" not in os.environ:
        return ""
    try:
        out = subprocess.check_output(["tmux", "display", "-p", "#{session_name}:#{window_index} #{window_name}"])
        return out.decode("utf-8", "ignore").strip()
    except Exception:
        return ""


def _draw_menu(stdscr, idx: int) -> None:
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    title = "Azazel‑Zero Console"
    stdscr.addnstr(0, max(0, (w - len(title)) // 2), title, w)
    stdscr.addnstr(1, 0, "=" * max(0, w), w)

    row = 3
    for i, (label, cmd) in enumerate(MENU):
        marker = ">" if i == idx else " "
        enabled = _exists(cmd) if cmd else True
        text = f"{marker} {label}"
        if not enabled and cmd is not None:
            text += "  [missing]"
        stdscr.addnstr(row, 0, text, w)
        row += 1

    stdscr.addnstr(h - 1, 0, HELP[:max(0, w)], w)
    stdscr.refresh()


def _run_menu(stdscr) -> None:
    curses.curs_set(0)
    stdscr.nodelay(False)
    stdscr.keypad(True)

    idx = 0
    n = len(MENU)

    # Initial epaper refresh for this screen
    _update_epaper()

    while True:
        _draw_menu(stdscr, idx)
        ch = stdscr.getch()
        if ch in (curses.KEY_UP, ord('k')):
            idx = (idx - 1) % n
        elif ch in (curses.KEY_DOWN, ord('j')):
            idx = (idx + 1) % n
        elif ch in (ord('r'), ord('R')):
            continue  # redraw loop will refresh
        elif ch in (ord('q'), 27):
            break
        elif ch in (curses.KEY_ENTER, 10, 13):
            label, cmd = MENU[idx]
            if cmd is None:
                break
            if not _exists(cmd):
                # flash message
                _message(stdscr, f"Command not found: {cmd[-1]}")
                continue
            stdscr.erase(); stdscr.refresh()
            code = _safe_run(cmd)
            _message(stdscr, f"Exited '{label}' (code {code})")


def _message(stdscr, msg: str, timeout_ms: int = 1200) -> None:
    h, w = stdscr.getmaxyx()
    stdscr.addnstr(h - 2, 0, " " * max(0, w), w)
    stdscr.addnstr(h - 2, 0, msg[:max(0, w)], w)
    stdscr.refresh()
    curses.napms(timeout_ms)


if __name__ == "__main__":
    try:
        curses.wrapper(_run_menu)
    except KeyboardInterrupt:
        pass