import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
PY_ROOT = REPO_ROOT / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from azazel_gadget.first_minute.tc import TcManager
from azazel_gadget.path_schema import LEGACY_DEPRECATION_DATE, _legacy_notice_level


class LegacyNoticeLevelTests(unittest.TestCase):
    def test_legacy_notice_level_defaults_to_debug_far_from_deprecation(self):
        self.assertEqual(_legacy_notice_level(date(2026, 3, 1)), "debug")

    def test_legacy_notice_level_is_warning_near_deprecation(self):
        near = LEGACY_DEPRECATION_DATE.replace(month=12, day=1)
        self.assertEqual(_legacy_notice_level(near), "warning")

    def test_legacy_notice_level_is_error_after_deprecation(self):
        after = date(2027, 1, 1)
        self.assertEqual(_legacy_notice_level(after), "error")


class TcBenignNoiseTests(unittest.TestCase):
    @patch("azazel_gadget.first_minute.tc._LOG")
    @patch("azazel_gadget.first_minute.tc.subprocess.run")
    def test_qdisc_delete_missing_root_is_treated_as_benign(self, run_mock, log_mock):
        run_mock.return_value = SimpleNamespace(
            returncode=2,
            stderr="Error: Cannot delete qdisc with handle of zero.\n",
        )
        tc = TcManager("usb0", "wlan0")
        tc._run(["qdisc", "del", "dev", "usb0", "root"])
        log_mock.warning.assert_not_called()
        log_mock.debug.assert_called_once()

    @patch("azazel_gadget.first_minute.tc._LOG")
    @patch("azazel_gadget.first_minute.tc.subprocess.run")
    def test_non_benign_tc_error_is_warned(self, run_mock, log_mock):
        run_mock.return_value = SimpleNamespace(
            returncode=2,
            stderr="RTNETLINK answers: Operation not permitted\n",
        )
        tc = TcManager("usb0", "wlan0")
        tc._run(["qdisc", "replace", "dev", "usb0", "root", "handle", "1:", "tbf"])
        log_mock.warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
