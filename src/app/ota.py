"""Download and prepare OTA releases in the inactive slot.
"""

import asyncio
import binascii
import gc
import hashlib
import json

import recovery
import ota_policy

from singleton import singleton


BLOCK_SIZE = 512
MAX_MANIFEST_SIZE = 32768
MAX_PATH_LENGTH = 160

SAFE_PATH_CHARACTERS = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789_./-"
)

PROTECTED_NAMES = (
    "main.py",
    "recovery.py",
    "core_settings.env",
    "instance_settings.env",
)


@singleton
class OtaManager:
    def __init__(
        self,
        recovery_manager,
        slot,
        settings,
        heartbeat,
        client,
    ):
        if slot not in ("a", "b"):
            raise ValueError("Invalid running slot")

        if not callable(heartbeat):
            raise TypeError("heartbeat must be callable")

        if not callable(getattr(client, "open", None)):
            raise TypeError("Client must provide open()")

        if not callable(getattr(client, "checked", None)):
            raise TypeError("Client must provide checked()")

        base = settings.get("OTA_BASE_URL")

        if not isinstance(base, str) or not base:
            raise ValueError("OTA_BASE_URL is required")

        if not base.startswith("https://"):
            raise ValueError(
                "OTA_BASE_URL must use https://"
            )

        if "?" in base or "#" in base:
            raise ValueError(
                "OTA_BASE_URL must not contain "
                "a query string or fragment"
            )

        for char in base:
            if ord(char) <= 32 or ord(char) >= 127:
                raise ValueError(
                    "OTA_BASE_URL must contain "
                    "printable ASCII characters"
                )

        authority = base[len("https://"):].split("/", 1)[0]

        if not authority or "@" in authority:
            raise ValueError(
                "Invalid OTA_BASE_URL authority"
            )

        self.recovery = recovery_manager
        self.slot = slot
        self.root = recovery_manager.slot_path(slot)
        self.heartbeat = heartbeat
        self.client = client
        self.base = base.rstrip("/") + "/"

        self._install_lock = asyncio.Lock()

    async def _read_block(self, response):
        self.heartbeat()

        block = await response.content.read(BLOCK_SIZE)

        self.heartbeat()

        return block

    def _write_block(self, output, block):
        """Write a complete block or raise on missing progress."""

        view = memoryview(block)
        offset = 0

        while offset < len(view):
            written = output.write(view[offset:])

            if written is None or written <= 0:
                raise OSError(
                    "File write made no progress"
                )

            offset += written

    async def _manifest(self):
        self.heartbeat()

        async with self.client.open(
            self.base + "manifest.json"
        ) as response:
            self.client.checked(response)

            raw = bytearray()

            while True:
                block = await self._read_block(response)

                if not block:
                    break

                if len(raw) + len(block) > MAX_MANIFEST_SIZE:
                    raise ValueError("Manifest too large")

                raw.extend(block)

                await asyncio.sleep_ms(0)

        self.heartbeat()

        manifest = json.loads(raw.decode("utf-8"))
        ota_policy.validate(manifest)

        self.heartbeat()

        return manifest

    async def probe(self):
        manifest = await self._manifest()

        return manifest["version"]

    def _destination(self, target, path):
        """Enforce the write boundary independently of the validator."""

        if target not in ("a", "b") or target == self.slot:
            raise ValueError(
                "Refusing write to running slot"
            )

        if (
            not isinstance(path, str)
            or not path
            or len(path) > MAX_PATH_LENGTH
        ):
            raise ValueError("Unsafe destination")

        if any(
            char not in SAFE_PATH_CHARACTERS
            for char in path
        ):
            raise ValueError("Unsafe path characters")

        parts = path.split("/")

        for part in parts:
            if (
                part in ("", ".", "..")
                or part in PROTECTED_NAMES
                or part.startswith(".")
            ):
                raise ValueError("Unsafe destination")

        return (
            self.recovery.slot_path(target)
            + "/"
            + path
        )

    async def _download(self, target, item):
        destination = self._destination(
            target,
            item["path"],
        )

        recovery.mkdirs(
            destination.rsplit("/", 1)[0]
        )

        digest = hashlib.sha256()
        count = 0

        self.heartbeat()

        async with self.client.open(
            self.base + item["path"]
        ) as response:
            self.client.checked(response)

            with open(destination, "wb") as output:
                while True:
                    block = await self._read_block(response)

                    if not block:
                        break

                    if count + len(block) > item["size"]:
                        raise ValueError(
                            "File larger than manifest: %s"
                            % item["path"]
                        )

                    self._write_block(output, block)

                    digest.update(block)
                    count += len(block)

                    self.heartbeat()

                    await asyncio.sleep_ms(0)

        actual_hash = binascii.hexlify(
            digest.digest()
        ).decode("ascii")

        if (
            count != item["size"]
            or actual_hash != item["sha256"]
        ):
            raise ValueError(
                "File integrity error: %s"
                % item["path"]
            )

        self.heartbeat()

    async def check_and_prepare(self):
        """Serialize complete installation attempts."""

        async with self._install_lock:
            return await self._check_and_prepare()

    async def _check_and_prepare(self):
        self.heartbeat()

        state = self.recovery.read()

        if (
            state["phase"] != recovery.PHASE_STABLE
            or state["active"] != self.slot
        ):
            raise RuntimeError(
                "Cannot install outside the stable active slot"
            )

        manifest = await self._manifest()
        version = manifest["version"]

        if version in (
            state.get("version"),
            state.get("rejected"),
        ):
            return False

        target = "b" if self.slot == "a" else "a"
        target_root = self.recovery.slot_path(target)

        # Validate every destination before modifying the candidate.
        for item in manifest["files"]:
            self._destination(
                target,
                item["path"],
            )

        recovery.remove_tree(
            target_root,
            self.heartbeat,
        )

        recovery.mkdirs(target_root)

        try:
            for item in manifest["files"]:
                await self._download(
                    target,
                    item,
                )

                gc.collect()

                self.heartbeat()

                await asyncio.sleep_ms(0)

            self.heartbeat()
            recovery.sync()
            self.heartbeat()

        except BaseException:
            # Cancellation also removes the incomplete candidate.
            recovery.remove_tree(
                target_root,
                self.heartbeat,
            )
            raise

        # Keep staging outside the cleanup block: a journal commit
        # may already be durable when stage() raises an exception.
        self.heartbeat()
        self.recovery.stage(target, version)

        return True
