import importlib.util
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PY_ROOT = REPO_ROOT / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))


def _load_first_minute_cli_module():
    cli_path = REPO_ROOT / "py" / "azazel-first-minute.py"
    spec = importlib.util.spec_from_file_location("azazel_first_minute_cli_test", cli_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class EnvTruthyTests(unittest.TestCase):
    def test_env_truthy_parses_zero_as_false(self):
        mod = _load_first_minute_cli_module()
        os.environ["AZAZEL_DEBUG"] = "0"
        self.assertFalse(mod._env_truthy("AZAZEL_DEBUG", default=False))

    def test_env_truthy_parses_one_as_true(self):
        mod = _load_first_minute_cli_module()
        os.environ["AZAZEL_DEBUG"] = "1"
        self.assertTrue(mod._env_truthy("AZAZEL_DEBUG", default=False))

    def test_env_truthy_returns_default_for_unknown_value(self):
        mod = _load_first_minute_cli_module()
        os.environ["AZAZEL_DEBUG"] = "maybe"
        self.assertFalse(mod._env_truthy("AZAZEL_DEBUG", default=False))
        self.assertTrue(mod._env_truthy("AZAZEL_DEBUG", default=True))


if __name__ == "__main__":
    unittest.main()
