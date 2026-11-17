import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "env_reader_under_test", ROOT / "src" / "app" / "env_reader.py"
)
env_reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(env_reader)


class EnvReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.core = Path(self.temp.name) / "core_settings.env"
        self.instance = Path(self.temp.name) / "instance_settings.env"
        self.core.write_text(
            "# Core settings\n"
            "WIFI_SSID=home\n"
            "WIFI_PASSWORD='secret=a=b'\n"
            "OTA_BASE_URL=https://test/ota\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def load(self):
        return env_reader.EnvReader().load(str(self.core), str(self.instance))

    def test_reads_core_comments_quotes_and_equals(self):
        env = self.load()
        self.assertEqual(env.get_core_required("WIFI_SSID"), "home")
        self.assertEqual(env.get_core_required("WIFI_PASSWORD"), "secret=a=b")

    def test_reads_instance_settings(self):
        self.instance.write_text("SECTIONS=4\nENABLED=true\n")
        env = self.load()
        self.assertEqual(env.get_instance("SECTIONS"), "4")
        self.assertEqual(env.get_int("SECTIONS"), 4)
        self.assertTrue(env.get_bool("ENABLED"))

    def test_core_override_is_rejected(self):
        self.instance.write_text("WIFI_SSID=other\n")
        with self.assertRaises(ValueError):
            self.load()

    def test_non_network_core_override_is_rejected(self):
        with self.core.open("a") as handle:
            handle.write("DEVICE_ID=original\n")
        self.instance.write_text("DEVICE_ID=replacement\n")
        with self.assertRaises(ValueError):
            self.load()

    def test_reserved_key_is_rejected_even_when_absent_from_core(self):
        self.instance.write_text("OTA_TOKEN=unexpected\n")
        with self.assertRaises(ValueError):
            self.load()

    def test_missing_instance_is_allowed(self):
        self.assertEqual(self.load().get("WIFI_SSID"), "home")

    def test_missing_core_raises(self):
        self.core.unlink()
        with self.assertRaises(OSError):
            self.load()

    def test_duplicate_key_is_rejected(self):
        self.core.write_text("KEY=first\nKEY=second\n")
        with self.assertRaises(ValueError):
            self.load()

    def test_invalid_line_is_rejected(self):
        self.core.write_text("invalid-line\n")
        with self.assertRaises(ValueError):
            self.load()

    def test_unterminated_quote_is_rejected(self):
        self.core.write_text("KEY='unfinished\n")
        with self.assertRaises(ValueError):
            self.load()

    def test_read_before_load_raises(self):
        with self.assertRaises(RuntimeError):
            env_reader.EnvReader().get("KEY")

    def test_missing_key_returns_default(self):
        self.assertEqual(self.load().get("MISSING", "fallback"), "fallback")

    def test_missing_required_key_raises(self):
        with self.assertRaises(ValueError):
            self.load().get_required("MISSING")

    def test_empty_required_core_key_raises(self):
        self.core.write_text("WIFI_SSID=\n")
        with self.assertRaises(ValueError):
            self.load().get_core_required("WIFI_SSID")

    def test_invalid_boolean_raises(self):
        self.instance.write_text("ENABLED=perhaps\n")
        with self.assertRaises(ValueError):
            self.load().get_bool("ENABLED")

    def test_module_exposes_shared_reader(self):
        self.assertIsInstance(env_reader.env, env_reader.EnvReader)


if __name__ == "__main__":
    unittest.main()
