#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import signal
import threading
from PIL import Image, ImageDraw, ImageFont

# タイムアウト用の例外
class TimeoutException(Exception):
    pass

def timeout_handler(signum, frame):
    raise TimeoutException("Operation timed out")

def safe_epd_test(timeout_sec=15):
    """タイムアウト付きの安全なE-Paperテスト"""
    
    # Waveshareライブラリのパス追加
    ws_root = "/opt/waveshare-epd/RaspberryPi_JetsonNano/python"
    ws_lib = "/opt/waveshare-epd/RaspberryPi_JetsonNano/python/lib"
    for path in [ws_root, ws_lib]:
        if path not in sys.path:
            sys.path.append(path)
    
    # ドライバインポート
    try:
        from waveshare_epd import epd2in13_V4 as drv
        bicolor = False
        print("✓ Using epd2in13_V4 (monochrome)")
    except ImportError:
        try:
            from waveshare_epd import epd2in13b_V4 as drv
            bicolor = True
            print("✓ Using epd2in13b_V4 (bicolor)")
        except ImportError as e:
            print(f"✗ Cannot import EPD driver: {e}")
            return False
    
    # タイムアウトハンドラ設定
    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    
    try:
        print(f"⚠ Starting EPD test with {timeout_sec}s timeout...")
        
        # EPDインスタンス作成
        signal.alarm(timeout_sec)
        epd = drv.EPD()
        signal.alarm(0)
        print("✓ EPD instance created")
        
        # 初期化
        print("⚠ Initializing EPD...")
        signal.alarm(timeout_sec)
        epd.init()
        signal.alarm(0)
        print("✓ EPD initialized successfully")
        
        # 画面サイズ取得
        width = getattr(epd, 'width', 250)
        height = getattr(epd, 'height', 122)
        print(f"✓ Display size: {width}x{height}")
        
        # シンプルな画像を作成
        print("⚠ Creating test image...")
        image = Image.new('1', (width, height), 255)  # 白背景
        draw = ImageDraw.Draw(image)
        
        # テストパターン描画
        draw.rectangle([(10, 10), (width-10, height-10)], outline=0, width=2)
        draw.text((20, 30), "EPD TEST OK", fill=0)
        draw.text((20, 50), f"Size: {width}x{height}", fill=0)
        draw.text((20, 70), f"Time: {time.strftime('%H:%M:%S')}", fill=0)
        
        # 画面クリア（タイムアウト付き）
        print("⚠ Clearing display...")
        signal.alarm(timeout_sec * 2)  # クリアは時間がかかることがある
        try:
            epd.Clear(0xFF)
        except AttributeError:
            # Clear メソッドがない場合は白い画像を表示
            blank = Image.new('1', (width, height), 255)
            if bicolor:
                red = Image.new('1', (width, height), 255)
                epd.display(epd.getbuffer(blank), epd.getbuffer(red))
            else:
                epd.display(epd.getbuffer(blank))
        signal.alarm(0)
        print("✓ Display cleared")
        
        # 短い待機
        time.sleep(2)
        
        # テスト画像表示
        print("⚠ Displaying test image...")
        signal.alarm(timeout_sec)
        if bicolor:
            red = Image.new('1', (width, height), 255)  # 赤レイヤーは空
            epd.display(epd.getbuffer(image), epd.getbuffer(red))
        else:
            epd.display(epd.getbuffer(image))
        signal.alarm(0)
        print("✓ Test image displayed")
        
        # スリープ
        print("⚠ Putting EPD to sleep...")
        signal.alarm(timeout_sec)
        epd.sleep()
        signal.alarm(0)
        print("✓ EPD sleep successful")
        
        print("\n🎉 E-Paper test completed successfully!")
        print("   Check the display for the test pattern.")
        return True
        
    except TimeoutException:
        print(f"\n✗ EPD operation timed out after {timeout_sec}s")
        print("   This usually indicates a hardware connection issue.")
        return False
    except Exception as e:
        print(f"\n✗ EPD test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

def quick_connection_test():
    """接続の事前確認"""
    print("=== Quick Connection Test ===")
    
    # SPI デバイス確認
    spi_dev = "/dev/spidev0.0"
    if not os.path.exists(spi_dev):
        print(f"✗ {spi_dev} not found")
        print("   Enable SPI: sudo raspi-config -> Interface Options -> SPI")
        return False
    print(f"✓ {spi_dev} exists")
    
    # GPIO メモリアクセス確認
    gpio_mem = "/dev/gpiomem"
    if not os.path.exists(gpio_mem):
        print(f"✗ {gpio_mem} not found")
        return False
    print(f"✓ {gpio_mem} exists")
    
    # Waveshare ライブラリ確認
    ws_lib = "/opt/waveshare-epd/RaspberryPi_JetsonNano/python/lib"
    if not os.path.exists(ws_lib):
        print(f"✗ Waveshare library not found at {ws_lib}")
        print("   Install Waveshare EPD library")
        return False
    print(f"✓ Waveshare library found")
    
    return True

def main():
    print("Azazel-Zero E-Paper Safe Test")
    print("=" * 35)
    
    # 権限確認
    if os.geteuid() != 0:
        print("⚠ Not running as root - some operations may fail")
        print("   Try: sudo python3 epd_safe_test.py")
    
    # 事前チェック
    if not quick_connection_test():
        print("\n❌ Pre-flight checks failed")
        return 1
    
    print("\n=== Hardware Test ===")
    
    # 安全なテスト実行
    if safe_epd_test(timeout_sec=15):
        print("\n✅ E-Paper hardware is working correctly!")
        return 0
    else:
        print("\n❌ E-Paper test failed")
        print("\nTroubleshooting steps:")
        print("1. Check all physical connections")
        print("2. Verify power supply voltage (usually 3.3V)")
        print("3. Try different jumper wires")
        print("4. Ensure E-Paper model matches the driver")
        print("5. Check if the display is damaged")
        return 1

if __name__ == "__main__":
    sys.exit(main())