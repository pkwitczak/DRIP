import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load_source(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


recovery = load_source("recovery_for_ota_test", "src/recovery.py")
policy = load_source("policy_for_ota_test", "src/app/ota_policy.py")
with patch.dict(sys.modules, {"recovery": recovery, "ota_policy": policy}):
    ota_module = load_source("ota_under_test", "src/app/ota.py")
with patch.dict(sys.modules, {"recovery": recovery}):
    main_module = load_source("main_under_test", "src/main.py")


class Response:
    def __init__(self, data, status=200):
        self.raw = io.BytesIO(data)
        self.status_code = status
        self.closed = False

    def close(self):
        self.closed = True


class Transport:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []
        self.responses = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        data = self.mapping[url]
        if isinstance(data, Exception):
            raise data
        response = data if isinstance(data, Response) else Response(data)
        self.responses.append(response)
        return response


class OtaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = recovery.Recovery(self.temp.name + "/ota")
        self.active = Path(self.manager.slot_path("a"))
        self.active.mkdir()
        (self.active / "ota.py").write_text("# original")
        self.manager.initialize("old")
        self.payloads = {name: ("# " + name).encode() for name in policy.REQUIRED}
        self.manifest = {
            "schema": 1,
            "version": "new",
            "files": [
                {"path": name, "size": len(data),
                 "sha256": hashlib.sha256(data).hexdigest()}
                for name, data in self.payloads.items()
            ],
        }
        self.mapping = {"https://test/" + name: data
                        for name, data in self.payloads.items()}
        self.refresh_manifest()
        self.transport = Transport(self.mapping)
        self.env = types.SimpleNamespace(
            get_core_required=lambda key: "https://test"
        )
        self.ota = ota_module.OtaManager(
            self.manager, "a", self.env, lambda: None, self.transport
        )

    def tearDown(self):
        self.temp.cleanup()

    def refresh_manifest(self):
        self.mapping["https://test/manifest.json"] = json.dumps(self.manifest).encode()

    def test_full_candidate_is_prepared_without_removing_active(self):
        self.assertTrue(self.ota.check_and_prepare())
        self.assertEqual(self.manager.read()["phase"], "ready")
        self.assertEqual((self.active / "ota.py").read_text(), "# original")
        for name, data in self.payloads.items():
            self.assertEqual((Path(self.manager.slot_path("b")) / name).read_bytes(), data)
        self.assertTrue(all(response.closed for response in self.transport.responses))

    def test_same_version_does_not_download_files(self):
        self.manifest["version"] = "old"
        self.refresh_manifest()
        self.assertFalse(self.ota.check_and_prepare())
        self.assertEqual(self.transport.calls, ["https://test/manifest.json"])

    def test_rejected_version_is_not_reinstalled(self):
        Path(self.manager.slot_path("b")).mkdir()
        self.manager.stage("b", "new")
        self.manager.select()
        self.manager.rollback()
        self.assertFalse(self.ota.check_and_prepare())

    def test_second_file_failure_preserves_active_release(self):
        self.mapping["https://test/" + policy.REQUIRED[1]] = OSError("download failed")
        with self.assertRaises(OSError):
            self.ota.check_and_prepare()
        self.assertEqual((self.active / "ota.py").read_text(), "# original")
        self.assertEqual(self.manager.read()["phase"], "stable")
        self.assertFalse(Path(self.manager.slot_path("b")).exists())

    def test_hash_failure_is_rejected(self):
        self.manifest["files"][0]["sha256"] = "0" * 64
        self.refresh_manifest()
        with self.assertRaises(ValueError):
            self.ota.check_and_prepare()
        self.assertEqual(self.manager.read()["active"], "a")

    def test_file_larger_than_manifest_is_rejected(self):
        name = policy.REQUIRED[0]
        self.mapping["https://test/" + name] += b"unexpected bytes"
        with self.assertRaises(ValueError):
            self.ota.check_and_prepare()
        self.assertFalse(Path(self.manager.slot_path("b")).exists())

    def test_invalid_json_does_not_download_files(self):
        self.mapping["https://test/manifest.json"] = b"invalid json"
        with self.assertRaises(ValueError):
            self.ota.check_and_prepare()
        self.assertEqual(len(self.transport.calls), 1)

    def test_http_error_closes_response(self):
        response = Response(b"not found", status=404)
        self.mapping["https://test/manifest.json"] = response
        with self.assertRaises(RuntimeError):
            self.ota.check_and_prepare()
        self.assertTrue(response.closed)

    def test_unsafe_manifest_is_rejected_before_downloading_files(self):
        self.manifest["files"][0]["path"] = "../core_settings.env"
        self.refresh_manifest()
        with self.assertRaises(ValueError):
            self.ota.check_and_prepare()
        self.assertEqual(len(self.transport.calls), 1)

    def test_independent_write_guard(self):
        for path in ("../core_settings.env", "/main.py", "a/recovery.py", "a//b"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.ota._destination("b", path)
        with self.assertRaises(ValueError):
            self.ota._destination("a", "net.py")

    def test_instance_settings_are_inherited(self):
        (self.active / "instance_settings.env").write_text("SECTIONS=4")
        self.ota.check_and_prepare()
        candidate = Path(self.manager.slot_path("b")) / "instance_settings.env"
        self.assertEqual(candidate.read_text(), "SECTIONS=4")

    def test_instance_settings_can_be_replaced(self):
        (self.active / "instance_settings.env").write_text("SECTIONS=4")
        data = b"SECTIONS=6"
        self.mapping["https://test/instance_settings.env"] = data
        self.manifest["files"].append({
            "path": "instance_settings.env", "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
        self.refresh_manifest()
        self.ota.check_and_prepare()
        self.assertEqual((Path(self.manager.slot_path("b")) / "instance_settings.env").read_bytes(), data)
        self.assertEqual((self.active / "instance_settings.env").read_text(), "SECTIONS=4")

    def test_installation_is_forbidden_during_trial(self):
        Path(self.manager.slot_path("b")).mkdir()
        self.manager.stage("b", "new")
        self.manager.select()
        with self.assertRaises(RuntimeError):
            self.ota.check_and_prepare()
        self.assertEqual(self.transport.calls, [])

    def test_probe_validates_without_staging(self):
        self.assertEqual(self.ota.probe(), "new")
        self.assertEqual(self.manager.read()["phase"], "stable")
        self.assertFalse(Path(self.manager.slot_path("b")).exists())


class PolicyTests(unittest.TestCase):
    def manifest(self):
        return {
            "schema": 1, "version": "new",
            "files": [{"path": path, "size": 1, "sha256": "a" * 64}
                      for path in policy.REQUIRED],
        }

    def test_complete_manifest_without_app_entrypoint(self):
        self.assertNotIn("app.py", policy.REQUIRED)
        self.assertNotIn("application.py", policy.REQUIRED)
        policy.validate(self.manifest())

    def test_new_module_is_allowed(self):
        self.assertEqual(policy.destination("sensors/new.py"), "sensors/new.py")

    def test_unsafe_paths_are_rejected(self):
        for path in ("../settings", "/settings", "a//b", "a/./b", "a\\b",
                     "a/core_settings.env", "main.py", "recovery.py", "%2e%2e/a", ".hidden"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                policy.destination(path)

    def test_incomplete_manifest_is_rejected(self):
        manifest = self.manifest()
        manifest["files"].pop()
        with self.assertRaises(ValueError):
            policy.validate(manifest)

    def test_duplicate_path_is_rejected(self):
        manifest = self.manifest()
        manifest["files"].append(manifest["files"][0])
        with self.assertRaises(ValueError):
            policy.validate(manifest)

    def test_invalid_hash_is_rejected(self):
        manifest = self.manifest()
        manifest["files"][0]["sha256"] = "bad"
        with self.assertRaises(ValueError):
            policy.validate(manifest)

    def test_file_size_limit_is_enforced(self):
        manifest = self.manifest()
        manifest["files"][0]["size"] = policy.MAX_FILE_BYTES + 1
        with self.assertRaises(ValueError):
            policy.validate(manifest)

    def test_file_directory_collision_is_rejected(self):
        manifest = self.manifest()
        manifest["files"] += [
            {"path": path, "size": 1, "sha256": "a" * 64}
            for path in ("folder", "folder/file.py")
        ]
        with self.assertRaises(ValueError):
            policy.validate(manifest)


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


class MainStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = recovery.Recovery(self.temp.name + "/ota")
        self.active = Path(self.manager.slot_path("a"))
        self.active.mkdir()
        (self.active / "ota.py").write_text("# original")
        self.manager.initialize("old")
        Path(self.manager.slot_path("b")).mkdir()
        self.manager.stage("b", "new")

    def tearDown(self):
        self.temp.cleanup()

    def boot(self, import_error=False, post_error=False,
             cleanup_error=False, init_error=False, post_hang=False):
        clock = Clock()
        machine = types.ModuleType("machine")
        machine.reset = Mock()
        machine.WDT = Mock(return_value=types.SimpleNamespace(feed=Mock()))
        env = types.SimpleNamespace(load=Mock())
        if init_error:
            env.load.side_effect = ValueError("invalid settings")
        net = types.SimpleNamespace(connect=Mock())

        def post_run(*args):
            self.assertEqual(self.manager.read()["phase"], "trial")
            if post_error:
                raise RuntimeError("POST failed")
            if post_hang:
                clock.now += main_module.POST_TIMEOUT_MS
                args[-1]()

        modules = {
            "env_reader": types.SimpleNamespace(env=env),
            "net": types.SimpleNamespace(Network=lambda settings: net),
            "ota": types.SimpleNamespace(OtaManager=lambda *args: object()),
            "post": types.SimpleNamespace(run=post_run),
        }

        def loader(root):
            self.assertEqual(self.manager.read()["phase"], "trial")
            if import_error:
                raise ImportError("broken candidate module")
            return modules

        real_cleanup = self.manager.cleanup

        def cleanup():
            if cleanup_error and self.manager.read()["active"] == "b":
                raise OSError("cleanup interrupted")
            real_cleanup()

        fake_recovery = types.SimpleNamespace(Recovery=lambda **kwargs: self.manager)
        with patch.object(main_module, "recovery", fake_recovery), \
             patch.object(main_module, "_load_modules", loader), \
             patch.object(main_module, "_normal_work", Mock()) as normal_work, \
             patch.object(main_module, "_log", Mock()), \
             patch.object(self.manager, "cleanup", cleanup), \
             patch.dict(sys.modules, {"machine": machine, "time": clock}):
            main_module.run()
        machine.reset.assert_called_once_with()
        return normal_work

    def test_main_confirms_success_and_removes_old_release(self):
        normal_work = self.boot()
        self.assertEqual(self.manager.read()["active"], "b")
        self.assertEqual(self.manager.read()["phase"], "stable")
        self.assertFalse(self.active.exists())
        normal_work.assert_called_once()

    def test_import_failure_rolls_back(self):
        normal_work = self.boot(import_error=True)
        self.assertEqual(self.manager.read()["active"], "a")
        self.assertEqual(self.manager.read()["phase"], "stable")
        normal_work.assert_not_called()

    def test_post_failure_rolls_back(self):
        normal_work = self.boot(post_error=True)
        self.assertEqual(self.manager.read()["active"], "a")
        self.assertEqual(self.manager.read()["rejected"], "new")
        normal_work.assert_not_called()

    def test_initialization_failure_rolls_back(self):
        normal_work = self.boot(init_error=True)
        self.assertEqual(self.manager.read()["active"], "a")
        normal_work.assert_not_called()

    def test_post_deadline_rolls_back(self):
        normal_work = self.boot(post_hang=True)
        self.assertEqual(self.manager.read()["active"], "a")
        self.assertEqual(self.manager.read()["rejected"], "new")
        normal_work.assert_not_called()

    def test_cleanup_failure_does_not_undo_confirmation(self):
        normal_work = self.boot(cleanup_error=True)
        self.assertEqual(self.manager.read()["active"], "b")
        self.assertEqual(self.manager.read()["phase"], "stable")
        normal_work.assert_called_once()
        self.manager.select()
        self.assertFalse(self.active.exists())


if __name__ == "__main__":
    unittest.main()
