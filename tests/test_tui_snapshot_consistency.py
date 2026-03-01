import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
PY_ROOT = REPO_ROOT / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from azazel_gadget import cli_unified


class TuiSnapshotConsistencyTests(unittest.TestCase):
    def test_load_snapshot_keeps_shared_fields_from_control_plane(self):
        payload = {
            "now_time": "12:34:56",
            "snapshot_epoch": 1000.0,
            "user_state": "SAFE",
            "ssid": "TestAP",
            "signal_dbm": "-52",
            "recommendation": "From control-plane",
            "reasons": ["steady"],
            "internal": {"state_name": "NORMAL", "suspicion": 3, "decay": 0},
            "connection": {"wifi_state": "CONNECTED", "usb_nat": "ON", "internet_check": "OK"},
            "monitoring": {"suricata": "ON", "opencanary": "OFF", "ntfy": "ON"},
            "channel_congestion": "low",
            "channel_ap_count": 10,
            "cpu_percent": 22.5,
            "mem_percent": 38,
            "temp_c": 49.2,
            "suricata_critical": 2,
            "suricata_warning": 4,
            "download_mbps": 5.5,
            "upload_mbps": 1.2,
            "evidence": ["from-snapshot"],
        }
        with patch.object(cli_unified, "read_snapshot_payload", return_value=(payload, "CONTROL_PLANE")):
            with patch.object(
                cli_unified,
                "_collect_monitoring_state",
                return_value={"suricata": "ON", "opencanary": "OFF", "ntfy": "ON"},
            ):
                snap = cli_unified.load_snapshot()

        self.assertEqual(snap.channel_congestion, "low")
        self.assertEqual(snap.channel_ap_count, 10)
        self.assertEqual(snap.cpu_percent, 22.5)
        self.assertEqual(snap.mem_percent, 38)
        self.assertEqual(snap.temp_c, 49.2)
        self.assertEqual(snap.suricata_critical, 2)
        self.assertEqual(snap.suricata_warning, 4)
        self.assertEqual(snap.download_mbps, 5.5)
        self.assertEqual(snap.upload_mbps, 1.2)
        self.assertEqual(snap.recommendation, "From control-plane")
        self.assertIn("from-snapshot", snap.evidence)

    def test_update_epd_respects_tui_min_interval(self):
        fake_run = Mock(return_value=SimpleNamespace(returncode=0))
        with patch.dict(os.environ, {"AZAZEL_TUI_EPD_MIN_INTERVAL": "60"}, clear=False):
            with patch.object(cli_unified, "_last_tui_epd_update_mono", 0.0):
                with patch.object(cli_unified.shutil, "which", return_value="/bin/systemctl"):
                    with patch.object(cli_unified.subprocess, "run", fake_run):
                        with patch.object(cli_unified.time, "monotonic", side_effect=[100.0, 110.0, 200.0]):
                            cli_unified.update_epd(SimpleNamespace(), enable_epd=True, force=True)
                            cli_unified.update_epd(SimpleNamespace(), enable_epd=True, force=False)
                            cli_unified.update_epd(SimpleNamespace(), enable_epd=True, force=False)

        self.assertEqual(fake_run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
