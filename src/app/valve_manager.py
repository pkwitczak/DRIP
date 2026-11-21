import asyncio
import json
from time import ticks_ms, ticks_diff, ticks_add

from singleton import singleton
from valve import Valve
from error_handler import errors
from exceptions import CriticalError


class InvalidValveCommand(ValueError):
    pass


class ValveVerificationTimeout(Exception):
    pass


@singleton
class ValveManager:
    _CHECK_MS = 50
    _SAMPLE_WAIT_MS = 5
    _SAMPLE_TIMEOUT_MS = 500
    _MAX_SECONDS = (ticks_add(0, -1) // 2) // 1000

    def __init__(self, settings):
        pins = self._decode_setting(settings.get("VALVES"))
        defaults = self._decode_setting(
            settings.get("DEFAULT_WATERING_SECONDS", 300)
        )

        if not isinstance(pins, dict) or not pins:
            raise ValueError("VALVES must be a non-empty dictionary")

        used = set()
        durations = {}

        if isinstance(defaults, dict) and set(defaults) != set(pins):
            raise ValueError("Watering defaults must match VALVES names")

        for name, pin in pins.items():
            if not isinstance(name, str) or not name:
                raise ValueError("Valve names must be non-empty strings")
            if type(pin) is not int or pin < 0 or pin in used:
                raise ValueError("Invalid or duplicate GPIO for " + name)
            used.add(pin)
            duration = defaults[name] if isinstance(defaults, dict) else defaults
            if not self._valid_seconds(duration):
                raise ValueError("Invalid watering duration for " + name)
            durations[name] = duration

        self._entries = {}
        self._lock = asyncio.Lock()
        self._task = None
        self._failure = None
        self._closed = False
        self._last_signature = None

        try:
            for name, pin in pins.items():
                self._entries[name] = {
                    "valve": Valve(pin),
                    "seconds": durations[name],
                    "deadline": None,
                    "requested_state": Valve.State.CLOSED,
                    "verification_error": None,
                    "reported_error": None,
                }
        except BaseException as original:
            cleanup_error = self._close_outputs()
            for entry in self._entries.values():
                try:
                    entry["valve"].deinit()
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
            if cleanup_error is not None:
                raise CriticalError(
                    "Valve initialization failed: %s; cleanup failed: %s"
                    % (original, cleanup_error)
                )
            raise

    def _decode_setting(self, value):
        if isinstance(value, str):
            return json.loads(value)
        return value

    def _valid_seconds(self, value):
        return type(value) is int and 0 < value <= self._MAX_SECONDS

    def check(self):
        if self._failure is not None:
            raise self._failure
        if self._closed:
            raise RuntimeError("ValveManager is closed")

    def _plan(self, command):
        if not isinstance(command, dict) or not command:
            raise InvalidValveCommand("Command must be a non-empty object")

        plan = []
        for name, request in command.items():
            if name not in self._entries:
                raise InvalidValveCommand("Unknown valve: %s" % name)

            seconds = self._entries[name]["seconds"]
            if isinstance(request, dict):
                if "state" not in request or any(
                    key not in ("state", "seconds") for key in request
                ):
                    raise InvalidValveCommand("Invalid command for " + name)
                state = request["state"]
                if "seconds" in request:
                    if state != Valve.State.OPEN:
                        raise InvalidValveCommand(
                            "seconds is only valid when opening " + name
                        )
                    seconds = request["seconds"]
            else:
                state = request

            if type(state) is not int or state not in (
                Valve.State.CLOSED, Valve.State.OPEN
            ):
                raise InvalidValveCommand("Invalid state for " + name)
            if not self._valid_seconds(seconds):
                raise InvalidValveCommand("Invalid watering duration for " + name)
            plan.append((name, state, seconds))

        return plan

    def _set(self, name, state, seconds):
        entry = self._entries[name]
        valve = entry["valve"]

        if valve.setpoint == state and valve.getError() is None:
            return

        entry["verification_error"] = None
        entry["deadline"] = (
            ticks_add(ticks_ms(), seconds * 1000)
            if state == Valve.State.OPEN else None
        )
        valve.setState(state)

    def _close_outputs(self):
        first_error = None
        for entry in self._entries.values():
            entry["deadline"] = None
            entry["requested_state"] = Valve.State.CLOSED
            entry["verification_error"] = None
            try:
                entry["valve"].closeValve()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        return first_error

    async def _wait_samples(self, names):
        started = ticks_ms()
        while True:
            waiting = []
            for name in names:
                entry = self._entries[name]
                status = entry["valve"].getStatus()
                if status["pending"] and status["monitoring"]:
                    waiting.append(name)
                elif not status["monitoring"]:
                    entry["verification_error"] = ValveVerificationTimeout(
                        "Valve monitoring is stopped: " + name
                    )
            if not waiting:
                break
            if ticks_diff(ticks_ms(), started) >= self._SAMPLE_TIMEOUT_MS:
                for name in waiting:
                    self._entries[name]["verification_error"] = (
                        ValveVerificationTimeout(
                            "No fresh GPIO sample for " + name
                        )
                    )
                break
            await asyncio.sleep_ms(self._SAMPLE_WAIT_MS)
        self._report_errors()

    def _report_errors(self):
        for entry in self._entries.values():
            valve = entry["valve"]
            status = valve.getStatus()
            if not status["pending"] and status["monitoring"]:
                entry["verification_error"] = None
            error = valve.getError() or entry["verification_error"]
            signature = (
                (type(error).__name__, str(error))
                if error is not None else None
            )
            if signature != entry["reported_error"]:
                entry["reported_error"] = signature
                if error is not None:
                    errors.report(error, None)

    async def apply_control(self, command):
        plan = self._plan(command)
        async with self._lock:
            self.check()
            try:
                for name, state, seconds in plan:
                    entry = self._entries[name]
                    if (
                        state == Valve.State.OPEN
                        and entry["requested_state"] == Valve.State.OPEN
                    ):
                        continue
                    entry["requested_state"] = state
                    self._set(name, state, seconds)
                await self._wait_samples([item[0] for item in plan])
            except BaseException as original:
                cleanup_error = self._close_outputs()
                if cleanup_error is not None:
                    raise CriticalError(
                        "Valve operation failed: %s; closing failed: %s"
                        % (original, cleanup_error)
                    )
                raise
            return self.read_status()

    def read_status(self):
        now = ticks_ms()
        result = {}
        for name, entry in self._entries.items():
            status = entry["valve"].getStatus()
            deadline = entry["deadline"]
            remaining = (
                max(0, ticks_diff(deadline, now))
                if deadline is not None else 0
            )
            status["remaining_seconds"] = (remaining + 999) // 1000
            status["default_watering_seconds"] = entry["seconds"]
            status["requested_state"] = entry["requested_state"]
            verification_error = entry["verification_error"]
            if verification_error is not None and status["pending"]:
                status["error"] = str(verification_error)
                status["verified"] = False
            elif verification_error is not None and not status["monitoring"]:
                status["error"] = str(verification_error)
                status["verified"] = False
            result[name] = status
        return {"valves": result}

    def poll_valves(self):
        self.check()
        signature = []
        for name, status in self.read_status()["valves"].items():
            signature.append((
                name, status["setpoint"], status["state"],
                status["pending"], status["verified"],
                status["monitoring"], status["error"], status["requested_state"],
            ))
        signature = tuple(signature)
        changed = signature != self._last_signature
        self._last_signature = signature
        return changed

    async def run(self):
        self.check()
        if self._task is not None:
            raise RuntimeError("ValveManager is already running")
        self._task = asyncio.current_task()
        try:
            while True:
                async with self._lock:
                    expired = []
                    now = ticks_ms()
                    for name, entry in self._entries.items():
                        deadline = entry["deadline"]
                        if deadline is not None and ticks_diff(now, deadline) >= 0:
                            self._set(name, Valve.State.CLOSED, entry["seconds"])
                            expired.append(name)
                    if expired:
                        await self._wait_samples(expired)
                    else:
                        self._report_errors()
                await asyncio.sleep_ms(self._CHECK_MS)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._failure = exc
            raise
        finally:
            self._task = None
            cleanup_error = self._close_outputs()
            if cleanup_error is not None:
                failure = CriticalError(
                    "Failed to close valve outputs: %s" % cleanup_error
                )
                self._failure = failure
                raise failure

    async def close(self):
        task = self._task
        if task is not None and task is asyncio.current_task():
            raise RuntimeError("ValveManager cannot close its own task")
        self._closed = True
        failure = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except BaseException as exc:
                failure = exc
        async with self._lock:
            output_error = self._close_outputs()
            if failure is None and output_error is not None:
                failure = output_error
            try:
                await self._wait_samples(list(self._entries))
            finally:
                for entry in self._entries.values():
                    try:
                        entry["valve"].deinit()
                    except Exception as exc:
                        if failure is None:
                            failure = exc
        if failure is not None:
            raise failure
