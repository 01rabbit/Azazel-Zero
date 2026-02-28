import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PY_ROOT = REPO_ROOT / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from azazel_control.wifi_diagnostics import WifiDiagnosticsSession, mask_sensitive_text


class WifiDiagnosticsTests(unittest.TestCase):
    def test_mask_sensitive_text(self):
        raw = "password=hello123 psk=mysecret wifi-sec.psk:abcde"
        masked = mask_sensitive_text(raw)
        self.assertNotIn("hello123", masked)
        self.assertNotIn("mysecret", masked)
        self.assertNotIn("abcde", masked)
        self.assertIn("***", masked)

    def test_session_writes_json_and_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            sess = WifiDiagnosticsSession(ssid="TestAP", iface="wlan0", base_dir=base)
            sess.add_event("SCANNING", "enter", {"note": "start"})
            sess.payload["logs"] = {
                "networkmanager": {
                    "stdout": "nm log",
                    "stderr": "",
                }
            }
            sess.set_result({"ok": False, "error": "password=abc"})
            json_path = sess.write_json()
            bundle_path = sess.build_bundle()

            self.assertTrue(json_path.exists())
            self.assertTrue(bundle_path.exists())

            text = json_path.read_text(encoding="utf-8")
            self.assertIn("trial_id", text)
            self.assertNotIn("password=abc", text)
            self.assertIn("password=***", text)

            with tarfile.open(bundle_path, "r:gz") as tf:
                names = tf.getnames()
                self.assertIn("attempt.json", names)


if __name__ == "__main__":
    unittest.main()
