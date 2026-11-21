import asyncio
import time

from singleton import singleton
from error_handler import errors
from exceptions import CriticalError
from client import CommunicationError
from valve_manager import InvalidValveCommand


@singleton
class CoreControl:
    ENDPOINT = "/control/valves"
    STATUS_INTERVAL_MS = 1000
    HTTP_POLL_MS = 1000
    RETRY_MS = 1000
    SEND_CHECK_MS = 50

    def __init__(self, client, valve_manager, settings):
        remote_domain = settings.get("REMOTE_DOMAIN")

        if not isinstance(remote_domain, str) or not remote_domain:
            raise ValueError("REMOTE_DOMAIN is required")

        remote_domain = remote_domain.rstrip("/")

        if remote_domain.startswith("https://"):
            authority = remote_domain[len("https://"):]
            self._is_ws = False

        elif remote_domain.startswith("wss://"):
            authority = remote_domain[len("wss://"):]
            self._is_ws = True

        else:
            raise ValueError(
                "REMOTE_DOMAIN must use https:// or wss://"
            )

        if (
            not authority
            or any(char in authority for char in "/?#@")
            or any(
                ord(char) <= 32 or ord(char) >= 127
                for char in authority
            )
        ):
            raise ValueError("Invalid REMOTE_DOMAIN")

        self._client = client
        self._valve_manager = valve_manager
        self._endpoint = remote_domain + self.ENDPOINT

        self._pending_status = None
        self._last_snapshot = None

        self._send_failed = False
        self._receive_failed = False

        self._tasks = []
        self._failure = None
        self._closed = False

        self._capture_status(
            self._valve_manager.read_status()
        )

    def _validate_status(self, status):
        if not isinstance(status, dict):
            raise TypeError(
                "ValveManager status must be a dictionary"
            )

        return status

    def _capture_status(self, status):
        self._validate_status(status)

        self._pending_status = status
        self._last_snapshot = time.ticks_ms()

    async def get_control(self):
        command = await self._client.receive(
            self._endpoint
        )

        if command is None:
            return None

        if not isinstance(command, dict):
            raise CommunicationError(
                "Control message must be a dictionary"
            )

        try:
            status = await self._valve_manager.apply_control(
                command
            )

        except InvalidValveCommand as exc:
            errors.report(exc, None)
            return None

        return self._validate_status(status)

    def send_status(self, status=None):
        """Queue the latest actual valve status."""

        if self._closed:
            raise RuntimeError("CoreControl is closed")

        if status is None:
            status = self._valve_manager.read_status()

        self._capture_status(status)

    async def _flush_status(self):
        status = self._pending_status

        if status is None:
            return False

        try:
            await self._client.send(
                self._endpoint,
                status,
            )

        except CriticalError:
            raise

        except (OSError, CommunicationError) as exc:
            if not self._send_failed:
                self._send_failed = True
                errors.report(exc, None)

            return False

        # A newer snapshot may have arrived while send() awaited.
        if self._pending_status is status:
            self._pending_status = None

        self._send_failed = False
        return True

    async def _receive_loop(self):
        while True:
            delay = self.HTTP_POLL_MS

            try:
                status = await self.get_control()

                self._receive_failed = False

                if status is not None:
                    self.send_status(status)

                if self._is_ws:
                    delay = 0 if status is not None else self.RETRY_MS

            except CriticalError:
                raise

            except (OSError, CommunicationError) as exc:
                if not self._receive_failed:
                    self._receive_failed = True
                    errors.report(exc, None)

                delay = self.RETRY_MS

            await asyncio.sleep_ms(delay)

    async def _send_loop(self):
        while True:
            delay = self.SEND_CHECK_MS

            if self._pending_status is not None:
                sent = await self._flush_status()

                if not sent:
                    delay = self.RETRY_MS

            await asyncio.sleep_ms(delay)

    async def _guard(self, operation):
        try:
            await operation()

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            # Forward unexpected failures to main through poll().
            if self._failure is None:
                self._failure = exc

    def _start(self):
        if self._tasks:
            return

        self._tasks.append(
            asyncio.create_task(
                self._guard(self._receive_loop)
            )
        )

        self._tasks.append(
            asyncio.create_task(
                self._guard(self._send_loop)
            )
        )

    async def poll(self):
        if self._closed:
            raise RuntimeError("CoreControl is closed")

        if self._failure is not None:
            raise self._failure

        changed = self._valve_manager.poll_valves()

        if (
            changed
            or time.ticks_diff(
                time.ticks_ms(),
                self._last_snapshot,
            ) >= self.STATUS_INTERVAL_MS
        ):
            self.send_status()

        self._start()

    async def close(self):
        self._closed = True

        tasks = self._tasks
        self._tasks = []

        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(
                *tasks,
                return_exceptions=True,
            )
