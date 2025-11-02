#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import subprocess
import time

def run_cmd(cmd, shell=True):
    """コマンドを実行して結果を返す"""
    try:
        result = subprocess.run(cmd, shell=shell, capture_output=True, text=True, timeout=10)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Timeout"
    except Exception as e:
        return -1, "", str(e)

def check_spi_devices():
    """SPI デバイスの確認"""
    print("=== SPI Device Check ===")
    
    # /dev/spidev* の確認
    spi_devices = []
    for i in range(2):
        for j in range(2):
            dev = f"/dev/spidev{i}.{j}"
            if os.path.exists(dev):
                spi_devices.append(dev)
                print(f"✓ Found: {dev}")
    
    if not spi_devices:
        print("✗ No SPI devices found!")
        print("  Check if SPI is enabled in raspi-config")
        return False
    
    # lsmod で SPI モジュール確認
    print("\n=== SPI Modules ===")
    code, out, err = run_cmd("lsmod | grep spi")
    if code == 0 and out:
        for line in out.split('\n'):
            if line.strip():
                print(f"✓ {line}")
    else:
        print("✗ No SPI modules loaded")
    
    return True

def check_gpio_permissions():
    """GPIO アクセス権限の確認"""
    print("\n=== GPIO Permissions ===")
    
    gpio_paths = ["/dev/gpiomem", "/sys/class/gpio"]
    for path in gpio_paths:
        if os.path.exists(path):
            stat = os.stat(path)
            print(f"✓ {path} exists (mode: {oct(stat.st_mode)})")
        else:
            print(f"✗ {path} not found")

def check_waveshare_lib():
    """Waveshare ライブラリの確認"""
    print("\n=== Waveshare EPD Library ===")
    
    ws_paths = [
        "/opt/waveshare-epd/RaspberryPi_JetsonNano/python",
        "/opt/waveshare-epd/RaspberryPi_JetsonNano/python/lib"
    ]
    
    for path in ws_paths:
        if os.path.exists(path):
            print(f"✓ {path} exists")
        else:
            print(f"✗ {path} not found")
            return False
    
    # Python パスに追加してインポートテスト
    for path in ws_paths:
        if path not in sys.path:
            sys.path.append(path)
    
    try:
        from waveshare_epd import epd2in13_V4
        print("✓ epd2in13_V4 import successful")
        return True
    except ImportError as e:
        try:
            from waveshare_epd import epd2in13b_V4
            print("✓ epd2in13b_V4 import successful (bicolor)")
            return True
        except ImportError as e2:
            print(f"✗ EPD import failed: {e}, {e2}")
            return False

def test_spi_loopback():
    """SPI ループバックテスト（簡易）"""
    print("\n=== SPI Basic Test ===")
    
    try:
        import spidev
        spi = spidev.SpiDev()
        spi.open(0, 0)  # /dev/spidev0.0
        spi.max_speed_hz = 1000000
        spi.mode = 0
        
        # 簡単なテストデータ送信
        test_data = [0x00, 0xFF, 0xAA, 0x55]
        response = spi.xfer2(test_data)
        print(f"✓ SPI communication test completed")
        print(f"  Sent: {[hex(x) for x in test_data]}")
        print(f"  Received: {[hex(x) for x in response]}")
        
        spi.close()
        return True
        
    except ImportError:
        print("✗ spidev module not available")
        print("  Install with: sudo apt-get install python3-spidev")
        return False
    except Exception as e:
        print(f"✗ SPI test failed: {e}")
        return False

def minimal_epd_test():
    """最小限のE-Paperテスト"""
    print("\n=== Minimal EPD Test ===")
    
    try:
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
        except ImportError:
            from waveshare_epd import epd2in13b_V4 as drv
            bicolor = True
        
        print(f"✓ Using driver: {drv.__name__} (bicolor: {bicolor})")
        
        # EPDインスタンス作成（初期化なし）
        epd = drv.EPD()
        print(f"✓ EPD instance created")
        print(f"  Width: {getattr(epd, 'width', 'unknown')}")
        print(f"  Height: {getattr(epd, 'height', 'unknown')}")
        
        # 初期化テスト（タイムアウト付き）
        print("⚠ Attempting EPD initialization (may hang if hardware issue)...")
        print("  Press Ctrl+C if it hangs for more than 10 seconds")
        
        start_time = time.time()
        epd.init()
        init_time = time.time() - start_time
        
        print(f"✓ EPD initialization successful ({init_time:.2f}s)")
        
        # スリープモードに移行
        epd.sleep()
        print("✓ EPD sleep successful")
        
        return True
        
    except KeyboardInterrupt:
        print("\n✗ EPD initialization interrupted (likely hardware issue)")
        print("  Check physical connections and power supply")
        return False
    except Exception as e:
        print(f"✗ EPD test failed: {e}")
        return False

def main():
    print("Azazel-Zero E-Paper Diagnostics")
    print("=" * 40)
    
    # 権限チェック
    if os.geteuid() != 0:
        print("⚠ Running without root privileges")
        print("  Some tests may require sudo for GPIO/SPI access")
    
    # 診断実行
    results = []
    results.append(("SPI Devices", check_spi_devices()))
    check_gpio_permissions()
    results.append(("Waveshare Library", check_waveshare_lib()))
    results.append(("SPI Communication", test_spi_loopback()))
    results.append(("EPD Hardware", minimal_epd_test()))
    
    # 結果サマリー
    print("\n" + "=" * 40)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 40)
    
    all_passed = True
    for test_name, passed in results:
        status = "PASS" if passed else "FAIL"
        icon = "✓" if passed else "✗"
        print(f"{icon} {test_name}: {status}")
        if not passed:
            all_passed = False
    
    if all_passed:
        print("\n🎉 All diagnostics passed! E-Paper should work.")
    else:
        print("\n⚠ Some diagnostics failed. Check hardware connections.")
        print("\nTroubleshooting suggestions:")
        print("1. Verify E-Paper display is properly connected")
        print("2. Check power supply (3.3V/5V as required)")
        print("3. Ensure SPI is enabled: sudo raspi-config -> Interface Options -> SPI")
        print("4. Try different jumper wires if connections are loose")
        print("5. Check if E-Paper display is compatible with current driver")

if __name__ == "__main__":
    main()