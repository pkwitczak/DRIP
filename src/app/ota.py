import os
import gc
try:
    import ujson as json
    import uhashlib as hashlib
    import ubinascii as binascii
except ImportError:
    import json
    import hashlib
    import binascii
import recovery
import app.ota_policy as ota_policy

class OtaManager:
    def __init__(self, recovery_manager, slot, env, heartbeat, transport=None):
        self.recovery = recovery_manager
        self.slot = slot
        self.root = recovery_manager.slot_path(slot)
        self.heartbeat = heartbeat
        self.base = env.get_core_required("OTA_BASE_URL").rstrip("/") + "/"
        if not self.base.startswith(("https://", "http://")):
            raise ValueError("Invalid OTA base URL")
        if transport is None:
            import requests
            transport = requests
        self.transport = transport

    def _open(self, url):
        self.heartbeat()
        response = self.transport.get(url, stream=True, timeout=3)
        if response.status_code != 200:
            response.close()
            raise RuntimeError("OTA HTTP error")
        return response

    def _manifest(self):
        response = self._open(self.base + "manifest.json")
        try:
            raw = bytearray()
            while True:
                self.heartbeat()
                block = response.raw.read(512)
                if not block:
                    break
                raw.extend(block)
                if len(raw) > 32768:
                    raise ValueError("Manifest too large")
            manifest = json.loads(raw.decode())
            ota_policy.validate(manifest)
            return manifest
        finally:
            response.close()

    def probe(self):
        return self._manifest()["version"]

    def _destination(self, target, path):
        # Independent write boundary; do not rely only on manifest validator.
        if target not in ("a", "b") or target == self.slot:
            raise ValueError("Refusing write to running slot")
        if not isinstance(path, str) or not path or len(path) > 160:
            raise ValueError("Unsafe destination")
        if any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./-" for c in path):
            raise ValueError("Unsafe path characters")
        parts = path.split("/")
        if any(p in ("", ".", "..", "main.py", "recovery.py", "core_settings.env") or p.startswith(".") for p in parts):
            raise ValueError("Unsafe destination")
        return self.recovery.slot_path(target) + "/" + path

    def _download(self, target, item):
        dest = self._destination(target, item["path"])
        recovery.mkdirs(dest.rsplit("/", 1)[0])
        response = self._open(self.base + item["path"])
        digest, count = hashlib.sha256(), 0
        try:
            with open(dest, "wb") as out:
                while True:
                    self.heartbeat()
                    block = response.raw.read(512)
                    if not block:
                        break
                    count += len(block)
                    if count > item["size"]:
                        raise ValueError("File larger than manifest")
                    digest.update(block)
                    out.write(block)
        finally:
            response.close()
        if count != item["size"] or binascii.hexlify(digest.digest()).decode() != item["sha256"]:
            raise ValueError("File integrity error")

    def check_and_prepare(self):
        state = self.recovery.read()
        if state["phase"] != "stable" or state["active"] != self.slot:
            raise RuntimeError("Cannot install during a trial")
        manifest = self._manifest()
        version = manifest["version"]
        if version in (state.get("version"), state.get("rejected")):
            return False
        target = "b" if self.slot == "a" else "a"
        target_root = self.recovery.slot_path(target)
        recovery.remove_tree(target_root, self.heartbeat)
        recovery.mkdirs(target_root)
        try:
            for item in manifest["files"]:
                self._download(target, item)
                gc.collect()
            names = [item["path"] for item in manifest["files"]]
            previous = self.root + "/instance_settings.env"
            if "instance_settings.env" not in names and recovery.exists(previous):
                with open(previous, "rb") as inp, open(self._destination(target, "instance_settings.env"), "wb") as out:
                    while True:
                        self.heartbeat()
                        block = inp.read(512)
                        if not block:
                            break
                        out.write(block)
            recovery.sync()
        except Exception:
            recovery.remove_tree(target_root, self.heartbeat)
            raise
        # A stage/write failure may occur after a durable write: do NOT delete
        # the candidate after attempting this commit. main will reset if pending.
        self.recovery.stage(target, version)
        return True
