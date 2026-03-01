import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

REPO_ROOT = Path(__file__).resolve().parents[1]
PY_ROOT = REPO_ROOT / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from azazel_gadget import cli_unified


class _FakeFlaskApp:
    def __init__(self, *_args, **_kwargs):
        self.logger = types.SimpleNamespace(debug=lambda *_a, **_k: None, warning=lambda *_a, **_k: None)

    def route(self, *_args, **_kwargs):
        def _decorator(func):
            return func

        return _decorator

    def errorhandler(self, *_args, **_kwargs):
        def _decorator(func):
            return func

        return _decorator


def _install_flask_stub():
    flask_mod = types.ModuleType("flask")
    flask_mod.Flask = _FakeFlaskApp
    flask_mod.jsonify = lambda *args, **kwargs: {"args": args, "kwargs": kwargs}
    flask_mod.request = types.SimpleNamespace()
    flask_mod.render_template = lambda *_a, **_k: ""
    flask_mod.send_from_directory = lambda *_a, **_k: None
    flask_mod.send_file = lambda *_a, **_k: None
    flask_mod.Response = object
    flask_mod.stream_with_context = lambda f: f
    flask_mod.has_request_context = lambda: False
    return flask_mod


def _load_web_app_module():
    app_path = REPO_ROOT / "azazel_web" / "app.py"
    spec = importlib.util.spec_from_file_location("azazel_web_app_test", app_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class MonitoringSharedTests(unittest.TestCase):
    def test_webui_and_tui_use_shared_monitoring_provider(self):
        sample = {"suricata": "ON", "opencanary": "OFF", "ntfy": "ON"}
        fake = types.ModuleType("azazel_gadget.monitoring_state")
        fake.get_monitoring_state = Mock(return_value=sample)
        flask_stub = _install_flask_stub()

        prev = sys.modules.get("azazel_gadget.monitoring_state")
        prev_flask = sys.modules.get("flask")
        sys.modules["azazel_gadget.monitoring_state"] = fake
        sys.modules["flask"] = flask_stub
        try:
            web_app = _load_web_app_module()
            self.assertEqual(cli_unified._collect_monitoring_state(), sample)
            self.assertEqual(web_app.get_monitoring_state(), sample)
            self.assertGreaterEqual(fake.get_monitoring_state.call_count, 2)
        finally:
            if prev is None:
                sys.modules.pop("azazel_gadget.monitoring_state", None)
            else:
                sys.modules["azazel_gadget.monitoring_state"] = prev
            if prev_flask is None:
                sys.modules.pop("flask", None)
            else:
                sys.modules["flask"] = prev_flask


if __name__ == "__main__":
    unittest.main()
