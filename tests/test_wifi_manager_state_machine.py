import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
PY_ROOT = REPO_ROOT / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from azazel_control.wifi_manager import WifiManager


class WifiManagerStateMachineTests(unittest.TestCase):
    def test_connect_success_path(self):
        mgr = WifiManager("wlan0")

        with patch.object(mgr, "_scan_networks", return_value={
            "ok": True,
            "entries": [{"ssid": "TestAP", "security": "WPA2 WPA3", "bssid": "aa", "signal": "60"}],
            "error": "",
        }), patch.object(mgr, "_activate_with_strategy", side_effect=[
            {"ok": False, "error": "timed out"},
            {"ok": True, "error": "", "connection": "TestAP"},
        ]), patch.object(mgr, "_is_associated", return_value=True), patch.object(mgr, "_wait_for_ip", return_value="192.168.1.20"), patch.object(mgr, "_wait_for_route", return_value="192.168.1.1"), patch.object(mgr, "_active_connection", return_value="TestAP"), patch.object(mgr, "_set_autoconnect_off", return_value=None), patch("azazel_control.wifi_manager.run_connectivity_checks", return_value={
            "captive_status": "NO",
            "captive_reason": "HTTP_204",
            "internet_ok": True,
            "checks": [],
        }):
            out = mgr.connect("TestAP", "WPA3", "passphrase123", False)

        self.assertTrue(out["ok"], out)
        self.assertEqual(out["last_success_state"], "CONNECTED")
        self.assertTrue(out["transition_mode"])
        self.assertGreaterEqual(len(out["attempts"]), 2)

    def test_connect_scan_failure_classification(self):
        mgr = WifiManager("wlan0")

        with patch.object(mgr, "_scan_networks", return_value={
            "ok": True,
            "entries": [{"ssid": "OtherAP", "security": "WPA2", "bssid": "aa", "signal": "60"}],
            "error": "",
        }), patch.object(mgr, "_activate_with_strategy", return_value={"ok": False, "error": "timeout"}), patch.object(mgr, "_is_associated", return_value=False), patch.object(mgr, "_get_ipv4", return_value=""), patch.object(mgr, "_get_default_route", return_value=""), patch.object(mgr.recovery, "run", return_value={"ok": True, "level": 1, "steps": []}):
            out = mgr.connect("TestAP", "WPA2", "passphrase123", False)

        self.assertFalse(out["ok"], out)
        self.assertEqual(out["failure_category"], "SCAN_FAILURE")
        self.assertIn("SSID", out["recommended_action"])


if __name__ == "__main__":
    unittest.main()
