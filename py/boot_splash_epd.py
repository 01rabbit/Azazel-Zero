#!/usr/bin/env python3
import os, re, subprocess, time, sys
from PIL import Image, ImageDraw, ImageFont

# === Asset paths ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSET_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
TITLE_FONT_PATH = os.path.join(ASSET_ROOT, "fonts", "Tamanegi_kaisyo_geki_v7.ttf")
ICON_PATH = os.path.join(ASSET_ROOT, "icons", "wifi.png")
ICON_H = 16   # icon height in pixels
ICON_GAP = 8  # gap between icon and text

def load_icon_1bit(path, target_h):
    from PIL import Image
    icon = Image.open(path).convert("RGBA")
    w, h = icon.size
    new_w = max(1, int(w * (target_h / h)))
    icon = icon.resize((new_w, target_h), Image.LANCZOS)
    # composite on white background then binarize for EPD
    bg = Image.new("RGBA", icon.size, (255, 255, 255, 255))
    icon = Image.alpha_composite(bg, icon).convert("L")
    icon = icon.point(lambda p: 0 if p < 160 else 255, mode='1')
    return icon

WLAN_IFACE="wlan0"
RETRY_SEC=30

def sh(cmd):
    return subprocess.run(cmd, shell=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.strip()

def get_ssid(): return sh("iwgetid -r") or "N/A"
def get_ipv4(iface):
    out=sh(f"ip -4 addr show {iface}")
    m=re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', out)
    return m.group(1) if m else None

def wait_network(timeout):
    t0=time.time()
    while time.time()-t0<timeout:
        ssid, ip=get_ssid(), get_ipv4(WLAN_IFACE)
        if ssid!="N/A" and ip: return ssid,ip
        time.sleep(1.5)
    return get_ssid(), get_ipv4(WLAN_IFACE) or "0.0.0.0"

def draw_image(ssid, ip, session=None, width=250, height=122):
    img=Image.new('1',(width,height),255); d=ImageDraw.Draw(img)
    try:
        font_b = ImageFont.truetype(TITLE_FONT_PATH, 18)
    except Exception:
        font_b = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    try:
        font_m = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 16)
    except Exception:
        font_m = ImageFont.load_default()
    # タイトル反転
    t="Azazel-Zero"; tw,th=d.textsize(t,font=font_b); margin=6
    d.rectangle([(0,0),(width,th+margin*2)],fill=0)
    d.text(((width-tw)//2,margin),t,font=font_b,fill=255)

    y = th + margin * 2 + 12

    # helper: fit text into given width with ellipsis
    def fit_text(draw, text, font, max_w):
        # Prefer textlength if available (Pillow >=8.0), fallback to textsize
        if hasattr(draw, "textlength"):
            tl = lambda s: draw.textlength(s, font=font)
        else:
            tl = lambda s: draw.textsize(s, font=font)[0]
        if tl(text) <= max_w:
            return text
        base = text
        while base and tl(base + "…") > max_w:
            base = base[:-1]
        return base + ("…" if base else "")

    # SSID line
    max_w = width - 16
    ssid_text = fit_text(d, f"SSID: {ssid}", font_m, max_w)
    d.text((8, y), ssid_text, font=font_m, fill=0)
    y += 20

    # Optional tmux session line (e.g., "azazel:0 window")
    if session:
        tmux_text = fit_text(d, f"TMUX: {session}", font_m, max_w)
        d.text((8, y), tmux_text, font=font_m, fill=0)
        y += 20

    # IP line with icon on the left
    x0 = 8
    try:
        if os.path.exists(ICON_PATH):
            ip_icon = load_icon_1bit(ICON_PATH, ICON_H)
            img.paste(ip_icon, (x0, y + max(0, 16 - ICON_H)//2))
            x_text = x0 + ip_icon.size[0] + ICON_GAP
        else:
            x_text = x0
    except Exception:
        x_text = x0
    ip_text = fit_text(d, ip, font_m, width - x_text - 8)
    d.text((x_text, y), ip_text, font=font_m, fill=0)
    return img

def show_on_epd(img):
    # Ensure Waveshare Python paths are searchable
    ws_root = "/opt/waveshare-epd/RaspberryPi_JetsonNano/python"
    ws_lib  = "/opt/waveshare-epd/RaspberryPi_JetsonNano/python/lib"
    for p in (ws_root, ws_lib):
        if p not in sys.path:
            sys.path.append(p)

    # Try mono driver first, then bicolor V4 as fallback
    BICOLOR = False
    try:
        from waveshare_epd import epd2in13_V4 as drv
    except Exception:
        from waveshare_epd import epd2in13b_V4 as drv
        BICOLOR = True

    epd = drv.EPD()
    epd.init()

    if BICOLOR:
        # For bicolor panels, send black layer plus an empty red layer
        from PIL import Image
        red = Image.new('1', img.size, 255)
        epd.display(epd.getbuffer(img), epd.getbuffer(red))
    else:
        epd.display(epd.getbuffer(img))

    epd.sleep()

def main():
    ssid, ip = wait_network(RETRY_SEC)
    session_info = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    img = draw_image(ssid, ip, session=session_info)
    try: show_on_epd(img)
    except Exception as e:
        print("EPD出力に失敗:",e)
        print("Azazel-Zero\n------------")
        print(f"SSID: {ssid}\nIP:   {ip}")

if __name__=="__main__": main()