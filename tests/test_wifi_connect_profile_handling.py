import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
PY_ROOT = REPO_ROOT / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from azazel_control import wifi_connect


class WiFiProfileHandlingTests(unittest.TestCase):
    @staticmethod
    def _ok(stdout: str = "", stderr: str = "") -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr)

    def test_open_connect_keeps_active_profile(self):
        commands = []
        state = {"connected": False}

        def fake_run(cmd, *args, **kwargs):
            commands.append(cmd)
            if cmd == ["nmcli", "-t", "-f", "NAME,TYPE", "con", "show"]:
                if state["connected"]:
                    return self._ok("AirportWiFi:802-11-wireless\n")
                return self._ok("stale-open:802-11-wireless\n")
            if cmd[:5] == ["nmcli", "-t", "-f", "802-11-wireless.ssid", "con"] and cmd[5] == "show":
                con_name = cmd[-1]
                if con_name in {"stale-open", "AirportWiFi"}:
                    return self._ok("802-11-wireless.ssid:AirportWiFi\n")
                return self._ok("802-11-wireless.ssid:\n")
            if cmd == ["nmcli", "dev", "wifi", "connect", "AirportWiFi", "ifname", "wlan0"]:
                state["connected"] = True
                return self._ok("Device 'wlan0' successfully activated")
            if cmd == ["nmcli", "-t", "-f", "GENERAL.CONNECTION", "dev", "show", "wlan0"]:
                if state["connected"]:
                    return self._ok("GENERAL.CONNECTION:AirportWiFi\n")
                return self._ok("GENERAL.CONNECTION:--\n")
            if cmd[:3] == ["nmcli", "con", "delete"]:
                return self._ok()
            if cmd == ["nmcli", "con", "mod", "AirportWiFi", "connection.autoconnect", "no"]:
                return self._ok()
            return self._ok()

        with patch("subprocess.run", side_effect=fake_run):
            out = wifi_connect.connect_nm(
                iface="wlan0",
                ssid="AirportWiFi",
                security="OPEN",
                passphrase=None,
                persist=False,
            )

        self.assertTrue(out.get("ok"), out)
        connect_idx = next(
            i for i, cmd in enumerate(commands)
            if cmd == ["nmcli", "dev", "wifi", "connect", "AirportWiFi", "ifname", "wlan0"]
        )
        delete_indices = [
            i for i, cmd in enumerate(commands)
            if len(cmd) >= 3 and cmd[:3] == ["nmcli", "con", "delete"]
        ]
        self.assertTrue(delete_indices, "expected stale profile cleanup before connect")
        self.assertTrue(
            all(idx < connect_idx for idx in delete_indices),
            f"active profile was deleted after connect: {commands}",
        )
        self.assertIn(
            ["nmcli", "con", "mod", "AirportWiFi", "connection.autoconnect", "no"],
            commands,
        )

    def test_nonpersistent_wpa_connect_does_not_delete_active_profile(self):
        commands = []
        state = {"connected": False}

        def fake_run(cmd, *args, **kwargs):
            commands.append(cmd)
            if cmd == ["nmcli", "-t", "-f", "NAME,TYPE", "con", "show"]:
                if state["connected"]:
                    return self._ok("CafeSecure:802-11-wireless\n")
                return self._ok("")
            if cmd[:5] == ["nmcli", "-t", "-f", "802-11-wireless.ssid", "con"] and cmd[5] == "show":
                con_name = cmd[-1]
                if con_name == "CafeSecure":
                    return self._ok("802-11-wireless.ssid:CafeSecure\n")
                return self._ok("802-11-wireless.ssid:\n")
            if cmd == ["nmcli", "dev", "wifi", "connect", "CafeSecure", "ifname", "wlan0", "password", "secretpass"]:
                state["connected"] = True
                return self._ok("Device 'wlan0' successfully activated")
            if cmd == ["nmcli", "-t", "-f", "GENERAL.CONNECTION", "dev", "show", "wlan0"]:
                if state["connected"]:
                    return self._ok("GENERAL.CONNECTION:CafeSecure\n")
                return self._ok("GENERAL.CONNECTION:--\n")
            if cmd[:3] == ["nmcli", "con", "delete"]:
                return self._ok()
            if cmd == ["nmcli", "con", "mod", "CafeSecure", "connection.autoconnect", "no"]:
                return self._ok()
            return self._ok()

        with patch("subprocess.run", side_effect=fake_run):
            out = wifi_connect.connect_nm(
                iface="wlan0",
                ssid="CafeSecure",
                security="WPA2",
                passphrase="secretpass",
                persist=False,
            )

        self.assertTrue(out.get("ok"), out)
        delete_commands = [
            cmd for cmd in commands
            if len(cmd) >= 3 and cmd[:3] == ["nmcli", "con", "delete"]
        ]
        self.assertEqual(delete_commands, [], f"unexpected profile deletion: {delete_commands}")
        self.assertIn(
            ["nmcli", "con", "mod", "CafeSecure", "connection.autoconnect", "no"],
            commands,
        )
        self.assertIn(
            ["nmcli", "dev", "wifi", "connect", "CafeSecure", "ifname", "wlan0", "password", "secretpass"],
            commands,
        )


if __name__ == "__main__":
    unittest.main()
