import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "recovery_under_test", ROOT / "src" / "recovery.py"
)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = recovery.Recovery(self.temp.name + "/ota")
        self.initial = Path(self.manager.slot_path("a"))
        self.initial.mkdir()
        (self.initial / "ota.py").write_text("# original")
        self.manager.initialize("old")

    def tearDown(self):
        self.temp.cleanup()

    def prepare_candidate(self):
        candidate = Path(self.manager.slot_path("b"))
        candidate.mkdir()
        (candidate / "ota.py").write_text("# candidate")
        self.manager.stage("b", "new")

    def test_initial_slot_is_selected(self):
        self.assertEqual(self.manager.select(), ("a", False))

    def test_candidate_enters_trial_before_returning(self):
        self.prepare_candidate()
        self.assertEqual(self.manager.select(), ("b", True))
        self.assertEqual(self.manager.read()["phase"], "trial")
        self.assertTrue(self.initial.exists())

    def test_restart_before_confirmation_rolls_back(self):
        self.prepare_candidate()
        self.manager.select()
        self.assertEqual(self.manager.select(), ("a", False))
        self.assertFalse(Path(self.manager.slot_path("b")).exists())

    def test_confirmation_is_separate_from_cleanup(self):
        self.prepare_candidate()
        self.manager.select()
        self.manager.confirm("b")
        self.assertEqual(self.manager.read()["active"], "b")
        self.assertEqual(self.manager.read()["phase"], "stable")
        self.assertTrue(self.initial.exists())
        self.manager.cleanup()
        self.assertFalse(self.initial.exists())

    def test_cleanup_is_resumed_at_next_boot(self):
        self.prepare_candidate()
        self.manager.select()
        self.manager.confirm("b")
        self.assertEqual(self.manager.select(), ("b", False))
        self.assertFalse(self.initial.exists())

    def test_cleanup_failure_keeps_confirmation(self):
        self.prepare_candidate()
        self.manager.select()
        self.manager.confirm("b")
        with patch.object(recovery, "remove_tree", side_effect=OSError(5)):
            with self.assertRaises(OSError):
                self.manager.cleanup()
        self.assertEqual(self.manager.read()["active"], "b")
        self.assertEqual(self.manager.select(), ("b", False))

    def test_uncommitted_download_is_removed(self):
        Path(self.manager.slot_path("b")).mkdir()
        self.manager.select()
        self.assertFalse(Path(self.manager.slot_path("b")).exists())

    def test_rejected_version_is_remembered(self):
        self.prepare_candidate()
        self.manager.select()
        self.assertTrue(self.manager.rollback())
        self.assertEqual(self.manager.read()["rejected"], "new")
        self.assertEqual(self.manager.read()["active"], "a")

    def test_stable_rollback_is_noop(self):
        self.assertFalse(self.manager.rollback())

    def test_wrong_slot_cannot_be_confirmed(self):
        self.prepare_candidate()
        self.manager.select()
        with self.assertRaises(RuntimeError):
            self.manager.confirm("a")

    def test_confirmation_requires_trial(self):
        self.prepare_candidate()
        with self.assertRaises(RuntimeError):
            self.manager.confirm("b")

    def test_cleanup_is_forbidden_during_trial(self):
        self.prepare_candidate()
        self.manager.select()
        with self.assertRaises(RuntimeError):
            self.manager.cleanup()

    def test_reinitialization_is_forbidden(self):
        with self.assertRaises(RuntimeError):
            self.manager.initialize()

    def test_second_pending_candidate_is_forbidden(self):
        self.prepare_candidate()
        with self.assertRaises(RuntimeError):
            self.manager.stage("b", "another")

    def test_path_outside_slots_is_rejected(self):
        with self.assertRaises(ValueError):
            self.manager.slot_path("../core_settings.env")

    def test_torn_latest_record_uses_previous_record(self):
        self.prepare_candidate()
        state = self.manager.read()
        latest = Path(self.manager.base) / ("state%d.json" % (state["seq"] % 2))
        latest.write_text("{interrupted")
        self.assertEqual(self.manager.read()["phase"], "stable")

    def test_bad_checksum_uses_previous_record(self):
        self.prepare_candidate()
        state = self.manager.read()
        latest = Path(self.manager.base) / ("state%d.json" % (state["seq"] % 2))
        record = json.loads(latest.read_text())
        record["sha256"] = "0" * 64
        latest.write_text(json.dumps(record))
        self.assertEqual(self.manager.read()["phase"], "stable")

    def test_no_valid_record_requires_explicit_recovery(self):
        (Path(self.manager.base) / "state0.json").write_text("corrupt")
        with self.assertRaises(RuntimeError):
            self.manager.read()


if __name__ == "__main__":
    unittest.main()
