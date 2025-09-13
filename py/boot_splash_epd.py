#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, subprocess, time, sys, argparse, traceback
from PIL import Image, ImageDraw, ImageFont
from typing import Optional, List, Tuple

# === Config / Constants ===
ASSET_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
TITLE_FONT_CANDIDATES = [
    os.path.join(ASSET_ROOT, "fonts", "Tamanegi_kaisyo_geki_v7.ttf"),
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
MONO_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
]
ICON_PATH = os.path.join(ASSET_ROOT, "icons", "wifi.png")
ICON_H = 16
ICON_GAP = 8

WS_ROOT = "/opt/waveshare-epd/RaspberryPi_JetsonNano/python"
WS_LIB  = "/opt/waveshare-epd/RaspberryPi_JetsonNano/python/lib"

DEFAULT_IFACE = "wlan0"
DEFAULT_TIMEOUT = 30

def run_cmd(args: List[str], timeout: int = 5, capture_stderr: bool = False) -> str:
    try:
        stderr = subprocess.STDOUT if capture_stderr else subprocess.DEVNULL
        out = subprocess.check_output(args, stderr=stderr, timeout=timeout, text=True).strip()
        return out
    except Exception:
        return ""

def get_default_iface() -> str:
    out = run_cmd(["ip", "-4", "route", "get", "1.1.1.1"], timeout=5)
    m = re.search(r"\bdev\s+([a-zA-Z0-9_.:-]+)", out)
    return m.group(1) if m else DEFAULT_IFACE

def get_ssid() -> str:
    ssid = run_cmd(["iwgetid", "-r"], timeout=3)
    return ssid if ssid else "N/A"

def get_ipv4(iface: str) -> Optional[str]:
    out = run_cmd(["ip", "-4", "addr", "show", iface], timeout=3)
    m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None

def wait_network(timeout_sec: int, iface: str) -> Tuple[str, str]:
    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        ssid = get_ssid()
        ip = get_ipv4(iface)
        if ssid != "N/A" and ip:
            return ssid, ip
        time.sleep(1.2)
    return get_ssid(), (get_ipv4(iface) or "0.0.0.0")

def load_icon_1bit(path:str, target_h:int):
    icon = Image.open(path).convert("RGBA")
    w, h = icon.size
    new_w = max(1, int(w * (target_h / h)))
    icon = icon.resize((new_w, target_h), Image.LANCZOS)
    bg = Image.new("RGBA", icon.size, (255, 255, 255, 255))
    icon = Image.alpha_composite(bg, icon).convert("L")
    icon = icon.point(lambda p: 0 if p < 160 else 255, mode="1")
    return icon

def pick_font(paths, size):
    for p in paths:
        try:
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()

def fit_text(draw, text, font, max_w):
    tl = draw.textlength if hasattr(draw, "textlength") else (lambda s, font: draw.textsize(s, font=font)[0])
    measure = (lambda s: tl(s, font)) if tl is not draw.textlength else (lambda s: tl(s, font=font))
    if measure(text) <= max_w:
        return text
    base = text
    while base and measure(base + "…") > max_w:
        base = base[:-1]
    return base + ("…" if base else "")

# ---------- EPD ----------
def init_epd(debug=False):
    for p in (WS_ROOT, WS_LIB):
        if p not in sys.path:
            sys.path.append(p)
    bic = False
    try:
        from waveshare_epd import epd2in13_V4 as drv
    except Exception:
        try:
            from waveshare_epd import epd2in13b_V4 as drv
            bic = True
        except Exception as e:
            if debug:
                traceback.print_exc()
            raise RuntimeError(f"EPD driver not found: {e}")
    epd = drv.EPD()
    epd.init()
    return epd, bic

def epd_dims(epd):
    # 横長前提に整える
    w = getattr(epd, "width", 250)
    h = getattr(epd, "height", 122)
    return (h, w) if h > w else (w, h)

def epd_full_clear(epd, bicolor):
    """
    一度だけフルリフレッシュして残像を掃く。Clear API が無い場合は全面白を送る。
    """
    try:
        # 一部実装では Clear がないため例外で分岐
        epd.init()
        try:
            epd.Clear(0xFF)
        except AttributeError:
            blank = Image.new("1", (getattr(epd, "width", 250), getattr(epd, "height", 122)), 255)
            # bicolor でも白面なら単層で十分
            if bicolor:
                red = Image.new("1", blank.size, 255)
                epd.display(epd.getbuffer(blank), epd.getbuffer(red))
            else:
                epd.display(epd.getbuffer(blank))
        # フレーム安定のための短い待機
        time.sleep(0.2)
    except Exception:
        # ここで失敗しても致命ではない
        pass

def show_on_epd(img, epd, bicolor, gentle: bool = False):
    """
    gentle=True の場合、ドライバが部分更新APIを提供していればそれを使用して
    反転（フルリフレッシュ）頻度を下げる。未対応なら通常表示にフォールバック。
    """
    # 一部実装では displayPartial / display_Partial など表記ゆれがある
    partial_api = None
    for name in ("displayPartial", "display_Partial", "DisplayPartial", "display_Fast"):
        if hasattr(epd, name):
            partial_api = getattr(epd, name)
            break

    if gentle and partial_api:
        try:
            # bicolor デバイスでも多くの実装は単一バッファの部分更新を受け付ける
            partial_api(epd.getbuffer(img))
            return
        except Exception:
            # 失敗したら通常描画にフォールバック
            pass

    if bicolor:
        red = Image.new("1", img.size, 255)
        epd.display(epd.getbuffer(img), epd.getbuffer(red))
    else:
        epd.display(epd.getbuffer(img))

# ---------- Drawing primitives ----------
def draw_logo_panel(width, height, title_font, invert=True, subtitle: Optional[str] = None):
    """
    上2/3をロゴ領域にする。invert=True で反転背景に白抜き。
    """
    img = Image.new("1", (width, height), 255)
    d = ImageDraw.Draw(img)
    logo_h = (height * 2) // 3
    if invert:
        d.rectangle([(0, 0), (width, logo_h)], fill=0)
    title = "Azazel-Zero"
    tw, th = d.textsize(title, font=title_font)
    d.text(((width - tw)//2, (logo_h - th)//2), title, font=title_font, fill=(255 if invert else 0))
    if subtitle:
        mono = pick_font(MONO_FONT_CANDIDATES, 14)
        sub = fit_text(d, subtitle, mono, width - 12)
        sw, sh = d.textsize(sub, font=mono)
        d.text(((width - sw)//2, logo_h + ((height - logo_h - sh)//2)), sub, font=mono, fill=0)
    return img

def draw_progress_frame(width, height, title_font, ratio:float, label:str):
    """
    上2/3反転ロゴ + 下1/3プログレスバー
    """
    img = draw_logo_panel(width, height, title_font, invert=True)
    d = ImageDraw.Draw(img)
    bar_top = (height * 2)//3 + 6
    bar_h   = max(10, height//12)
    bar_left, bar_right = 8, width - 8
    # 枠
    d.rectangle([bar_left, bar_top, bar_right, bar_top + bar_h], outline=0, width=1)
    # 充填
    fill_w = int((bar_right - bar_left - 2) * max(0.0, min(1.0, ratio)))
    if fill_w > 0:
        d.rectangle([bar_left+1, bar_top+1, bar_left+1 + fill_w, bar_top + bar_h -1], fill=0)
    # ラベル
    mono = pick_font(MONO_FONT_CANDIDATES, 14)
    txt = fit_text(d, label, mono, width - 16)
    tw, th = d.textsize(txt, font=mono)
    d.text(((width - tw)//2, bar_top + bar_h + 4), txt, font=mono, fill=0)
    return img

# ---------- Animations ----------
def animate_start(epd, bicolor, steps:int=10, min_frame_sec:float=0.25, label="Booting…", gentle: bool = False):
    w, h = epd_dims(epd)
    title_font = pick_font(TITLE_FONT_CANDIDATES, 26)
    # 1) 起動時はまず一度だけフルリフレッシュで残像を掃く
    epd_full_clear(epd, bicolor)
    # 2) 反転ロゴをフル更新で安定表示（以降のバー更新は部分更新で控えめに）
    base = draw_logo_panel(w, h, title_font, invert=True)
    show_on_epd(base, epd, bicolor, gentle=False)
    for i in range(steps+1):
        ratio = i/steps
        frame = draw_progress_frame(w, h, title_font, ratio, label)
        show_on_epd(frame, epd, bicolor, gentle=gentle)
        time.sleep(min_frame_sec)
    epd.sleep()

def animate_shutdown(epd, bicolor, hold_sec:float=1.0):
    w, h = epd_dims(epd)
    title_font = pick_font(TITLE_FONT_CANDIDATES, 26)
    frame = draw_logo_panel(w, h, title_font, invert=True, subtitle="Shutting down…")
    show_on_epd(frame, epd, bicolor)
    time.sleep(hold_sec)
    # 画面消去
    try:
        # 多くのWaveshareドライバに Clear(0xFF) がある
        epd.init()
        try:
            epd.Clear(0xFF)
        except AttributeError:
            # ない場合は全面白のバッファを送る
            blank = Image.new("1", (w, h), 255)
            show_on_epd(blank, epd, bicolor)
        epd.sleep()
    except Exception:
        # 失敗しても沈黙。終了処理だ、静粛に。
        pass

# ---------- Legacy splash (情報パネル) ----------
def draw_info_panel(ssid: str, ip: str, session: Optional[str], width: int, height: int, debug: bool = False):
    img = Image.new("1", (width, height), 255); d = ImageDraw.Draw(img)
    font_b = pick_font(TITLE_FONT_CANDIDATES, 18)
    font_m = pick_font(MONO_FONT_CANDIDATES, 16)

    # タイトル帯（反転）
    t="Azazel-Zero"; tw,th=d.textsize(t,font=font_b); margin=6
    d.rectangle([(0,0),(width,th+margin*2)],fill=0)
    d.text(((width-tw)//2,margin),t,font=font_b,fill=255)

    y = th + margin * 2 + 12
    # SSID
    ssid_text = fit_text(d, f"SSID: {ssid}", font_m, width-16)
    d.text((8,y), ssid_text, font=font_m, fill=0); y += 20
    # TMUX
    if session:
        tmux_text = fit_text(d, f"TMUX: {session}", font_m, width-16)
        d.text((8,y), tmux_text, font=font_m, fill=0); y += 20
    # IP + icon
    x0=8; x_text=x0
    try:
        if os.path.exists(ICON_PATH):
            ip_icon = load_icon_1bit(ICON_PATH, ICON_H)
            img.paste(ip_icon, (x0, y + max(0, 16 - ICON_H)//2))
            x_text = x0 + ip_icon.size[0] + ICON_GAP
    except Exception:
        if debug: traceback.print_exc()
    ip_display = ip if ip and ip != "0.0.0.0" else "0.0.0.0  (No DHCP yet)"
    ip_text = fit_text(d, f"IP: {ip_display}", font_m, width - x_text - 8)
    d.text((x_text, y), ip_text, font=font_m, fill=0)
    return img

def show_info_panel(epd, bicolor, ssid, ip, session: Optional[str], debug: bool = False):
    w,h = epd_dims(epd)
    # 情報表示の前に一度だけフルリフレッシュして文字のエッジを安定させる
    epd_full_clear(epd, bicolor)
    img = draw_info_panel(ssid, ip, session, w, h, debug=debug)
    # 初回はフル更新でくっきり表示、その後の再描画は必要に応じて gentle を使う想定
    show_on_epd(img, epd, bicolor, gentle=False)
    epd.sleep()

# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser(description="Azazel-Zero EPD splash & animations")
    ap.add_argument("--mode", choices=["start","info","shutdown"], default="info",
                    help="start: 起動アニメ, info: 情報パネル, shutdown: 終了アニメ")
    ap.add_argument("--steps", type=int, default=int(os.getenv("STEPS", "10")),
                    help="startアニメのステップ数")
    ap.add_argument("--frame-sec", type=float, default=float(os.getenv("FRAME_SEC","0.25")),
                    help="startアニメのフレーム間隔秒")
    ap.add_argument("--gentle", action="store_true", help="部分更新が可能なら使用して反転演出を抑制")
    ap.add_argument("--timeout", type=int, default=int(os.getenv("TIMEOUT", DEFAULT_TIMEOUT)))
    ap.add_argument("--iface", type=str, default=os.getenv("IFACE",""))
    ap.add_argument("--debug", action="store_true", default=(os.getenv("DEBUG","0")=="1"))
    ap.add_argument("session", nargs="*", help="infoモードのTMUX表示用")
    args = ap.parse_args()

    # EPD 初期化
    try:
        epd, bic = init_epd(debug=args.debug)
    except Exception as e:
        if args.debug:
            print("EPD init failed:", repr(e))
            traceback.print_exc()
        sys.exit(1)

    if args.mode == "start":
        animate_start(epd, bic, steps=args.steps, min_frame_sec=args.frame_sec, label="Booting…", gentle=args.gentle)
        return

    if args.mode == "shutdown":
        animate_shutdown(epd, bic, hold_sec=1.0)
        return

    # info: 既存の情報スプラッシュ
    iface = args.iface if args.iface else get_default_iface()
    ssid, ip = wait_network(args.timeout, iface)
    session_info = " ".join(args.session) if args.session else ""
    show_info_panel(epd, bic, ssid, ip, session_info, debug=args.debug)

if __name__ == "__main__":
    main()