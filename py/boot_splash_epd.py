#!/usr/bin/env python3
import os, re, subprocess, time, sys
from PIL import Image, ImageDraw, ImageFont

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

def draw_image(ssid,ip,width=250,height=122):
    img=Image.new('1',(width,height),255); d=ImageDraw.Draw(img)
    font_b=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",18)
    font_m=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",16)
    # タイトル反転
    t="Azazel-Zero"; tw,th=d.textsize(t,font=font_b); margin=6
    d.rectangle([(0,0),(width,th+margin*2)],fill=0)
    d.text(((width-tw)//2,margin),t,font=font_b,fill=255)
    y=th+margin*2+12
    d.text((8,y),f"SSID: {ssid}",font=font_m,fill=0); y+=24
    d.text((8,y),ip,font=font_m,fill=0)
    return img

def show_on_epd(img):
    sys.path.append("/opt/waveshare-epd/RaspberryPi_JetsonNano/python")
    from waveshare_epd import epd2in13_V4 as drv  # ←V4が無ければ V3/V2 に変更
    epd=drv.EPD(); epd.init()
    epd.display(epd.getbuffer(img))
    epd.sleep()

def main():
    ssid,ip=wait_network(RETRY_SEC)
    img=draw_image(ssid,ip)
    try: show_on_epd(img)
    except Exception as e:
        print("EPD出力に失敗:",e)
        print("Azazel-Zero\n------------")
        print(f"SSID: {ssid}\nIP:   {ip}")

if __name__=="__main__": main()