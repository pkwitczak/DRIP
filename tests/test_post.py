import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "post_under_test", ROOT / "src" / "app" / "post.py"
)
post = importlib.util.module_from_spec(spec)
spec.loader.exec_module(post)


class Clock:
    def __init__(self):
        self.now = 0

    def ticks_ms(self):
        return self.now

    @staticmethod
    def ticks_diff(new, old):
        return new - old

    def sleep_ms(self, milliseconds):
        self.now += milliseconds


class PostTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.env = types.SimpleNamespace(
            get_core_required=Mock(return_value="configured"),
            get_int=Mock(return_value=300),
        )
        self.net = types.SimpleNamespace(is_connected=Mock(return_value=True))
        self.ota = types.SimpleNamespace(probe=Mock(return_value="version"))
        self.heartbeat = Mock()

    def run_post(self, stable_ms=200, timeout_ms=1000):
        with patch.object(post, "time", self.clock):
            post.run(self.env, self.net, self.ota, self.heartbeat,
                     stable_ms=stable_ms, timeout_ms=timeout_ms)

    def test_success_requires_stable_network_and_manifest_probe(self):
        self.run_post()
        self.assertEqual(self.clock.now, 200)
        self.ota.probe.assert_called_once_with()
        self.assertGreater(self.heartbeat.call_count, 1)

    def test_network_failure_times_out(self):
        self.net.is_connected.return_value = False
        with self.assertRaises(RuntimeError):
            self.run_post()

    def test_network_interruption_restarts_stability_window(self):
        self.net.is_connected.side_effect = lambda: self.clock.now != 100
        self.run_post()
        self.assertEqual(self.clock.now, 400)

    def test_manifest_failure_is_not_ignored(self):
        self.ota.probe.side_effect = OSError("server unavailable")
        with self.assertRaises(OSError):
            self.run_post()

    def test_invalid_configuration_is_rejected_before_probe(self):
        self.env.get_int.return_value = -1
        with self.assertRaises(ValueError):
            self.run_post()
        self.ota.probe.assert_not_called()

    def test_missing_core_configuration_is_not_ignored(self):
        self.env.get_core_required.side_effect = ValueError("missing core")
        with self.assertRaises(ValueError):
            self.run_post()
        self.ota.probe.assert_not_called()

    def test_deadline_from_main_is_not_ignored(self):
        self.heartbeat.side_effect = RuntimeError("POST deadline exceeded")
        with self.assertRaises(RuntimeError):
            self.run_post()

    def test_invalid_timing_is_rejected(self):
        with self.assertRaises(ValueError):
            self.run_post(stable_ms=1000, timeout_ms=1000)


if __name__ == "__main__":
    unittest.main()
