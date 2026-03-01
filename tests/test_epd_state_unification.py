import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
PY_ROOT = REPO_ROOT / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from azazel_control import epd_mode_refresh
from azazel_gadget.first_minute.controller import FirstMinuteController


class EpdModeRefreshStateTests(unittest.TestCase):
    def test_risk_prefers_user_state_and_suspicion(self):
        snap = {
            "user_state": "SAFE",
            "internal": {"state_name": "DEGRADED", "suspicion": 17},
            "connection": {"internet_check": "N/A"},
        }
        risk, suspicion = epd_mode_refresh._risk_and_suspicion_from_snapshot(snap, use_user_state=True)
        self.assertEqual(risk, "SAFE")
        self.assertEqual(suspicion, 17)

    def test_risk_falls_back_to_connection_when_user_state_disabled(self):
        snap = {
            "internal": {"state_name": "DEGRADED", "suspicion": 9},
            "connection": {"internet_check": "OK"},
        }
        risk, suspicion = epd_mode_refresh._risk_and_suspicion_from_snapshot(snap, use_user_state=False)
        self.assertEqual(risk, "SAFE")
        self.assertEqual(suspicion, 9)

    def test_suri_alert_overrides_when_allowed(self):
        payload = {"mode": "shield"}
        with patch.object(epd_mode_refresh, "_first_snapshot", return_value={"user_state": "SAFE"}):
            with patch.object(epd_mode_refresh, "_active_suri_alert", return_value={"state": "danger", "msg": "ATTACK DETECTED"}):
                render = epd_mode_refresh._desired_render_spec(payload)
        self.assertEqual(render.get("state"), "danger")
        self.assertEqual(render.get("msg"), "ATTACK DETECTED")

    def test_suri_alert_suppressed_in_scapegoat_by_default(self):
        payload = {"mode": "scapegoat"}
        snapshot = {
            "user_state": "SAFE",
            "internal": {"suspicion": 3},
            "ssid": "TestAP",
            "signal_dbm": "-50",
            "connection": {"wifi_state": "CONNECTED", "internet_check": "OK"},
        }
        with patch.object(epd_mode_refresh, "_first_snapshot", return_value=snapshot):
            with patch.object(epd_mode_refresh, "_active_suri_alert", return_value={"state": "danger", "msg": "ATTACK DETECTED"}):
                with patch.object(epd_mode_refresh, "_alerts_in_scapegoat_enabled", return_value=False):
                    render = epd_mode_refresh._desired_render_spec(payload)
        self.assertEqual(render.get("state"), "normal")
        self.assertEqual(render.get("risk_status"), "SAFE")

    def test_payload_internet_fallback_is_not_used_when_snapshot_exists(self):
        payload = {"mode": "shield", "internet": "FAIL"}
        snapshot = {
            "internal": {"suspicion": 1},
            "connection": {"internet_check": "UNKNOWN"},
            "ssid": "TestAP",
            "signal_dbm": "-55",
        }
        with patch.object(epd_mode_refresh, "_first_snapshot", return_value=snapshot):
            with patch.object(epd_mode_refresh, "_active_suri_alert", return_value={}):
                render = epd_mode_refresh._desired_render_spec(payload)
        self.assertEqual(render.get("state"), "normal")
        self.assertEqual(render.get("risk_status"), "CHECKING")

    def test_payload_internet_fallback_used_when_snapshot_missing(self):
        payload = {"mode": "shield", "internet": "FAIL"}
        with patch.object(epd_mode_refresh, "_first_snapshot", return_value={}):
            with patch.object(epd_mode_refresh, "_active_suri_alert", return_value={}):
                render = epd_mode_refresh._desired_render_spec(payload)
        self.assertEqual(render.get("state"), "normal")
        self.assertEqual(render.get("risk_status"), "LIMITED")


class FirstMinuteEpdTriggerTests(unittest.TestCase):
    def test_maybe_update_epd_triggers_unified_refresh(self):
        ctrl = object.__new__(FirstMinuteController)
        ctrl.dry_run = False
        ctrl.epd_enabled = True
        ctrl.epd_last_update = 0.0
        ctrl.epd_min_interval = 0.0
        ctrl.logger = Mock()
        ctrl._trigger_epd_refresh = Mock(return_value=True)

        FirstMinuteController._maybe_update_epd(
            ctrl,
            stage=type("StageStub", (), {"value": "NORMAL"})(),
            summary={"reason": "test"},
            link_meta={},
            force=False,
        )

        ctrl._trigger_epd_refresh.assert_called_once()
        self.assertGreater(ctrl.epd_last_update, 0.0)

    def test_maybe_update_epd_respects_min_interval_without_force(self):
        ctrl = object.__new__(FirstMinuteController)
        ctrl.dry_run = False
        ctrl.epd_enabled = True
        ctrl.epd_last_update = 10_000.0
        ctrl.epd_min_interval = 1000.0
        ctrl.logger = Mock()
        ctrl._trigger_epd_refresh = Mock(return_value=True)

        with patch("time.time", return_value=10_100.0):
            FirstMinuteController._maybe_update_epd(
                ctrl,
                stage=type("StageStub", (), {"value": "NORMAL"})(),
                summary={},
                link_meta={},
                force=False,
            )
        ctrl._trigger_epd_refresh.assert_not_called()


class FirstMinuteConnectionReconcileTests(unittest.TestCase):
    def _mk_controller(self):
        ctrl = object.__new__(FirstMinuteController)
        ctrl.cfg = SimpleNamespace(interfaces={"upstream": "wlan0", "downstream": "usb0"})
        ctrl.last_probe = None
        ctrl._resolved_captive_probe_iface = "wlan0"
        ctrl._resolved_captive_probe_reason = "NOT_CHECKED"
        ctrl._get_interface_ip = Mock(return_value="192.168.40.184")
        ctrl._default_gateway_for_iface = Mock(return_value="192.168.40.1")
        ctrl._is_usb_route_active = Mock(return_value=True)
        return ctrl

    def test_reconcile_promotes_connecting_to_connected_on_live_link(self):
        ctrl = self._mk_controller()
        base = {
            "wifi_state": "CONNECTING",
            "internet_check": "N/A",
            "captive_portal": "NO",
            "captive_portal_reason": "HTTP_204",
        }
        link_meta = {
            "link": {
                "connected": "1",
                "ssid": "JCOM_NYRY",
                "bssid": "60:84:bd:b6:9f:d3",
                "gateway": "192.168.40.1",
            }
        }
        merged = FirstMinuteController._reconcile_connection_with_live_link(ctrl, base, link_meta)
        normalized = FirstMinuteController._normalize_connection_state(ctrl, merged)
        self.assertEqual(normalized.get("wifi_state"), "CONNECTED")
        self.assertEqual(normalized.get("usb_nat"), "ON")
        self.assertEqual(normalized.get("internet_check"), "OK")
        self.assertEqual(normalized.get("ssid"), "JCOM_NYRY")
        self.assertEqual(normalized.get("ip_wlan"), "192.168.40.184")
        self.assertEqual(normalized.get("gateway_ip"), "192.168.40.1")

    def test_reconcile_clears_stale_connected_when_live_link_down(self):
        ctrl = self._mk_controller()
        base = {
            "wifi_state": "CONNECTED",
            "ssid": "OldSSID",
            "bssid": "11:22:33:44:55:66",
            "ip_wlan": "192.168.1.2",
            "gateway_ip": "192.168.1.1",
            "captive_portal": "NA",
        }
        link_meta = {"link": {"connected": "0"}}
        ctrl._is_usb_route_active = Mock(return_value=False)
        merged = FirstMinuteController._reconcile_connection_with_live_link(ctrl, base, link_meta)
        normalized = FirstMinuteController._normalize_connection_state(ctrl, merged)
        self.assertEqual(normalized.get("wifi_state"), "DISCONNECTED")
        self.assertEqual(normalized.get("usb_nat"), "OFF")
        self.assertEqual(normalized.get("internet_check"), "N/A")
        self.assertEqual(normalized.get("ssid"), "")
        self.assertEqual(normalized.get("ip_wlan"), "")


if __name__ == "__main__":
    unittest.main()
