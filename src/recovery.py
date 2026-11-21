"""OTA release selection, journal and recovery."""

try:
    import ujson as json
except ImportError:
    import json

try:
    import uhashlib as hashlib
except ImportError:
    import hashlib

try:
    import ubinascii as binascii
except ImportError:
    import binascii

from storage import (
    exists,
    mkdirs,
    read_text,
    remove_tree,
    sync,
    write_text,
)


SLOTS = ("a", "b")

PHASE_STABLE = "stable"
PHASE_READY = "ready"
PHASE_TRIAL = "trial"

MAX_RECORD_SIZE = 4096


def sha256(data):
    return binascii.hexlify(
        hashlib.sha256(data).digest()
    ).decode()


class _RecoveryState:
    def __init__(
        self,
        sequence,
        current,
        phase=PHASE_STABLE,
        candidate=None,
        version=None,
        candidate_version=None,
        rejected=None,
    ):
        self.sequence = sequence
        self.current = current
        self.phase = phase
        self.candidate = candidate
        self.version = version
        self.candidate_version = candidate_version
        self.rejected = rejected

        self.validate()

    def validate(self):
        if (
            type(self.sequence) is not int
            or self.sequence < 0
        ):
            raise ValueError(
                "Invalid recovery sequence"
            )

        if self.current not in SLOTS:
            raise ValueError(
                "Invalid current slot"
            )

        if self.phase not in (
            PHASE_STABLE,
            PHASE_READY,
            PHASE_TRIAL,
        ):
            raise ValueError(
                "Invalid recovery phase"
            )

        if self.phase == PHASE_STABLE:
            if self.candidate is not None:
                raise ValueError(
                    "Stable state cannot contain a candidate"
                )

        elif (
            self.candidate not in SLOTS
            or self.candidate == self.current
        ):
            raise ValueError(
                "Pending state requires a separate candidate slot"
            )

    @classmethod
    def from_record(cls, record):
        if not isinstance(record, dict):
            raise ValueError(
                "Recovery state must be an object"
            )

        return cls(
            sequence=record.get("seq"),
            current=record.get("active"),
            phase=record.get("phase"),
            candidate=record.get("candidate"),
            version=record.get("version"),
            candidate_version=record.get(
                "candidate_version"
            ),
            rejected=record.get("rejected"),
        )

    def to_record(self):
        return {
            "seq": self.sequence,
            "active": self.current,
            "phase": self.phase,
            "candidate": self.candidate,
            "version": self.version,
            "candidate_version": self.candidate_version,
            "rejected": self.rejected,
        }


class Recovery:
    def __init__(
        self,
        base="/.ota",
        heartbeat=lambda: None,
    ):
        self.base = base
        self.heartbeat = heartbeat

        mkdirs(base)

    def slot_path(self, slot):
        if slot not in SLOTS:
            raise ValueError("Invalid slot")

        return self.base + "/" + slot

    def _record_path(self, number):
        return self.base + "/state%d.json" % number

    def _read_state(self):
        latest = None

        for number in (0, 1):
            path = self._record_path(number)

            if not exists(path):
                continue

            raw = read_text(
                path,
                MAX_RECORD_SIZE + 1,
            )

            if len(raw) > MAX_RECORD_SIZE:
                continue

            try:
                record = json.loads(raw)
                payload = record["payload"]

                if (
                    sha256(payload.encode())
                    != record["sha256"]
                ):
                    continue

                state = _RecoveryState.from_record(
                    json.loads(payload)
                )

            except (
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
            ):
                continue

            if (
                latest is None
                or state.sequence > latest.sequence
            ):
                latest = state

        if latest is None:
            raise RuntimeError(
                "No valid recovery state; USB recovery required"
            )

        return latest

    def read(self):
        return self._read_state().to_record()

    def _write(self, state):
        state.validate()

        payload = json.dumps(
            state.to_record()
        )

        record = json.dumps({
            "payload": payload,
            "sha256": sha256(payload.encode()),
        })

        if len(record) > MAX_RECORD_SIZE:
            raise ValueError(
                "Recovery record exceeds size limit"
            )

        path = self._record_path(
            state.sequence % 2
        )

        self.heartbeat()

        write_text(path, record)
        sync()

        self.heartbeat()

    def _commit(self, state):
        state.sequence += 1
        self._write(state)

    def initialize(self, version="usb-initial"):
        if any(
            exists(self._record_path(number))
            for number in (0, 1)
        ):
            raise RuntimeError(
                "Already initialized"
            )

        if not exists(
            self.slot_path("a") + "/ota.py"
        ):
            raise RuntimeError(
                "Initial slot not installed"
            )

        self._write(
            _RecoveryState(
                sequence=0,
                current="a",
                version=version,
            )
        )

    def cleanup(self):
        state = self._read_state()

        if state.phase != PHASE_STABLE:
            raise RuntimeError(
                "Refusing cleanup during pending trial"
            )

        inactive = next(
            slot
            for slot in SLOTS
            if slot != state.current
        )

        remove_tree(
            self.slot_path(inactive),
            self.heartbeat,
        )

        sync()

    def select(self):
        state = self._read_state()

        if state.phase == PHASE_TRIAL:
            self.rollback()
            state = self._read_state()

        if state.phase == PHASE_STABLE:
            self.cleanup()
            return state.current, False

        # Persist the trial before importing candidate code.
        state.phase = PHASE_TRIAL
        self._commit(state)

        return state.candidate, True

    def stage(self, target, version):
        state = self._read_state()

        if (
            state.phase != PHASE_STABLE
            or target == state.current
        ):
            raise RuntimeError(
                "Cannot stage candidate"
            )

        self.slot_path(target)

        state.phase = PHASE_READY
        state.candidate = target
        state.candidate_version = version

        self._commit(state)

    def confirm(self, slot):
        state = self._read_state()

        if (
            state.phase != PHASE_TRIAL
            or state.candidate != slot
        ):
            raise RuntimeError(
                "No matching trial"
            )

        state.current = state.candidate
        state.version = state.candidate_version
        state.phase = PHASE_STABLE
        state.candidate = None
        state.candidate_version = None
        state.rejected = None

        # Commit only. Cleanup is a separate operation.
        self._commit(state)

    def rollback(self):
        state = self._read_state()

        if state.phase == PHASE_STABLE:
            return False

        state.phase = PHASE_STABLE
        state.rejected = state.candidate_version
        state.candidate = None
        state.candidate_version = None

        self._commit(state)

        return True
