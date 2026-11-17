import os
try:
    import ujson as json
    import uhashlib as hashlib
    import ubinascii as binascii
except ImportError:
    import json
    import hashlib
    import binascii

SLOTS = ("a", "b")

def exists(path):
    try:
        os.stat(path)
        return True
    except OSError as exc:
        if exc.args[0] == 2:
            return False
        raise

def sync():
    fn = getattr(os, "sync", None)
    if fn:
        fn()

def mkdirs(path):
    current = ""
    for part in path.split("/"):
        if part:
            current += "/" + part
            if not exists(current):
                os.mkdir(current)

def remove_tree(path, heartbeat=lambda: None):
    if not exists(path):
        return
    heartbeat()
    if os.stat(path)[0] & 0x4000:
        for name in os.listdir(path):
            remove_tree(path + "/" + name, heartbeat)
        os.rmdir(path)
    else:
        os.remove(path)

def sha256(data):
    return binascii.hexlify(hashlib.sha256(data).digest()).decode()

class Recovery:
    def __init__(self, base="/.ota", heartbeat=lambda: None):
        self.base = base
        self.heartbeat = heartbeat
        mkdirs(base)

    def slot_path(self, slot):
        if slot not in SLOTS:
            raise ValueError("Invalid slot")
        return self.base + "/" + slot

    @staticmethod
    def _validate(s):
        if type(s.get("seq")) is not int or s["seq"] < 0:
            raise ValueError("Invalid sequence")
        if s.get("active") not in SLOTS:
            raise ValueError("Invalid active slot")
        if s.get("phase") not in ("stable", "ready", "trial"):
            raise ValueError("Invalid phase")
        if s["phase"] == "stable":
            if s.get("candidate") is not None:
                raise ValueError("Unexpected candidate")
        elif s.get("candidate") not in SLOTS or s["candidate"] == s["active"]:
            raise ValueError("Invalid candidate")

    def read(self):
        valid = []
        for number in (0, 1):
            path = self.base + "/state%d.json" % number
            if not exists(path):
                continue
            try:
                with open(path) as f:
                    raw = f.read(4097)
                if len(raw) > 4096:
                    continue
                record = json.loads(raw)
                payload = record["payload"]
                if sha256(payload.encode()) != record["sha256"]:
                    continue
                state = json.loads(payload)
                self._validate(state)
                valid.append(state)
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
        if not valid:
            raise RuntimeError("No valid recovery state; USB recovery required")
        return max(valid, key=lambda s: s["seq"])

    def _write(self, state):
        self._validate(state)
        payload = json.dumps(state)
        record = {"payload": payload, "sha256": sha256(payload.encode())}
        path = self.base + "/state%d.json" % (state["seq"] % 2)
        self.heartbeat()
        with open(path, "w") as f:
            json.dump(record, f)
            f.flush()
        sync()
        self.heartbeat()

    def _change(self, state, **updates):
        new = dict(state)
        new.update(updates)
        new["seq"] = state["seq"] + 1
        self._write(new)
        return new

    def initialize(self, version="usb-initial"):
        if any(exists(self.base + "/state%d.json" % n) for n in (0, 1)):
            raise RuntimeError("Already initialized")
        if not exists(self.slot_path("a") + "/ota.py"):
            raise RuntimeError("Initial slot not installed")
        self._write({"seq": 0, "phase": "stable", "active": "a",
                     "candidate": None, "version": version,
                     "candidate_version": None, "rejected": None})

    def cleanup(self):
        state = self.read()
        if state["phase"] != "stable":
            raise RuntimeError("Refusing cleanup during pending trial")
        other = "b" if state["active"] == "a" else "a"
        remove_tree(self.slot_path(other), self.heartbeat)
        sync()

    def select(self):
        state = self.read()
        if state["phase"] == "trial":
            self.rollback()
            state = self.read()
        if state["phase"] == "stable":
            self.cleanup()
            return state["active"], False
        candidate = state["candidate"]
        # Journal the trial BEFORE importing any candidate Python module.
        self._change(state, phase="trial")
        return candidate, True

    def stage(self, target, version):
        state = self.read()
        if state["phase"] != "stable" or target == state["active"]:
            raise RuntimeError("Cannot stage candidate")
        self.slot_path(target)
        self._change(state, phase="ready", candidate=target,
                     candidate_version=version)

    def confirm(self, slot):
        state = self.read()
        if state["phase"] != "trial" or state["candidate"] != slot:
            raise RuntimeError("No matching trial")
        # Only commit here; main performs cleanup in a separate try/except.
        self._change(state, phase="stable", active=slot,
                     version=state["candidate_version"], candidate=None,
                     candidate_version=None, rejected=None)

    def rollback(self):
        state = self.read()
        if state["phase"] not in ("ready", "trial"):
            return False
        self._change(state, phase="stable", candidate=None,
                     rejected=state.get("candidate_version"),
                     candidate_version=None)
        return True
