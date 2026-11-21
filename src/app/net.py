import network
import time

from singleton import singleton


STATE_IDLE = "idle"
STATE_CONNECTING = "connecting"
STATE_CONNECTED = "connected"
STATE_LOST = "lost"


class ReconnectLimitError(RuntimeError):
    pass


@singleton
class Network:
    def __init__(
        self,
        env,
        timeout_ms=15000,
        poll_interval_ms=100,
        retry_interval_ms=2000,
        max_attempts=10,
    ):
        if timeout_ms <= 2000:
            raise ValueError(
                "Wi-Fi timeout must exceed 2000 ms"
            )

        if poll_interval_ms <= 0:
            raise ValueError(
                "Wi-Fi poll interval must be positive"
            )

        if retry_interval_ms < 0:
            raise ValueError(
                "Wi-Fi retry interval cannot be negative"
            )

        if max_attempts <= 0:
            raise ValueError(
                "Wi-Fi max attempts must be positive"
            )

        self.env = env
        self.wlan = None

        self._timeout_ms = timeout_ms
        self._poll_interval_ms = poll_interval_ms
        self._retry_interval_ms = retry_interval_ms
        self._max_attempts = max_attempts

        self._enabled = False
        self._state = STATE_IDLE
        self._connected_at = None

        self._attempts = 0
        self._attempt_started = None
        self._connect_pending = False
        self._connect_at = None
        self._retry_at = time.ticks_ms()

        self._ssid = None
        self._password = None
        self._last_error = None
        self._limit_error = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.disconnect()
        except OSError:
            # Preserve an exception from the context body.
            if exc is None:
                raise

        return False

    def start(self):
        if self._enabled:
            return self

        # Invalid configuration is not a failed Wi-Fi attempt.
        ssid = self.env.get_core_required("WIFI_SSID")
        password = self.env.get_core_required("WIFI_PASSWORD")

        self._ssid = ssid
        self._password = password
        self._enabled = True

        self._state = STATE_IDLE
        self._connected_at = None
        self._attempts = 0
        self._attempt_started = None
        self._connect_pending = False
        self._connect_at = None
        self._retry_at = time.ticks_ms()
        self._last_error = None
        self._limit_error = None

        return self

    def disconnect(self):
        # Explicit shutdown disables automatic reconnect.
        self._enabled = False
        self._state = STATE_IDLE
        self._connected_at = None
        self._attempt_started = None
        self._connect_pending = False
        self._connect_at = None

        if self.wlan is not None:
            try:
                self.wlan.disconnect()
            finally:
                self.wlan.active(False)

    def _mark_connected(self, now):
        if self._state != STATE_CONNECTED:
            self._connected_at = now

        self._state = STATE_CONNECTED
        self._attempts = 0
        self._attempt_started = None
        self._connect_pending = False
        self._connect_at = None
        self._last_error = None

    def _start_attempt(self, now):
        self._attempts += 1
        self._attempt_started = now
        self._state = STATE_CONNECTING
        self._connected_at = None

        if self.wlan is None:
            self.wlan = network.WLAN(network.STA_IF)

        # End the previous driver-side attempt before starting another.
        self.wlan.active(False)
        self.wlan.active(True)

        # Setting retained from the original module.
        self.wlan.config(pm=0xa11140)

        # Original initialization delay, without blocking sleep(2).
        self._connect_pending = True
        self._connect_at = time.ticks_add(
            time.ticks_ms(),
            2000,
        )

    def _fail_attempt(self, reason):
        self._state = STATE_LOST
        self._connected_at = None
        self._attempt_started = None
        self._connect_pending = False
        self._connect_at = None
        self._last_error = reason

        if self._attempts >= self._max_attempts:
            self._limit_error = ReconnectLimitError(
                "Wi-Fi failed after %d attempts: %s"
                % (self._attempts, reason)
            )

            raise self._limit_error

        self._retry_at = time.ticks_add(
            time.ticks_ms(),
            self._retry_interval_ms,
        )

    def poll(self):
        if not self._enabled:
            return False

        # Do not silently begin another series after exhausting the limit.
        if self._limit_error is not None:
            raise self._limit_error

        now = time.ticks_ms()

        if (
            self._attempt_started is None
            and self._state != STATE_CONNECTED
            and time.ticks_diff(now, self._retry_at) < 0
        ):
            return False

        try:
            connected = (
                self.wlan is not None
                and self.wlan.isconnected()
            )
        except OSError as exc:
            if self._attempt_started is None:
                self._attempts += 1

            self._fail_attempt(str(exc))
            return False

        if connected:
            self._mark_connected(now)
            return True

        if self._state == STATE_CONNECTED:
            self._state = STATE_LOST
            self._connected_at = None
            self._retry_at = now

        if self._attempt_started is None:
            try:
                self._start_attempt(now)
            except OSError as exc:
                self._fail_attempt(str(exc))

            return False

        elapsed = time.ticks_diff(
            now,
            self._attempt_started,
        )

        if elapsed >= self._timeout_ms:
            self._fail_attempt(
                "Wi-Fi connection timeout"
            )
            return False

        if (
            self._connect_pending
            and time.ticks_diff(now, self._connect_at) >= 0
        ):
            try:
                self.wlan.connect(
                    self._ssid,
                    self._password,
                )
                self._connect_pending = False
            except OSError as exc:
                self._fail_attempt(str(exc))

        return False

    def connect(self, heartbeat=None, timeout_ms=None):
        if timeout_ms is not None and timeout_ms <= 0:
            raise ValueError(
                "Connection wait timeout must be positive"
            )

        self.start()
        started = time.ticks_ms()

        while True:
            if heartbeat is not None:
                heartbeat()

            if self.poll():
                return self

            if timeout_ms is not None:
                elapsed = time.ticks_diff(
                    time.ticks_ms(),
                    started,
                )

                if elapsed >= timeout_ms:
                    raise RuntimeError(
                        "Wi-Fi connection wait timeout"
                    )

            time.sleep_ms(self._poll_interval_ms)

    def isconnected(self):
        if self.wlan is None:
            return False

        connected = self.wlan.isconnected()

        if self._enabled and self._limit_error is None:
            if connected:
                self._mark_connected(
                    time.ticks_ms()
                )

            elif self._state == STATE_CONNECTED:
                self._state = STATE_LOST
                self._connected_at = None
                self._retry_at = time.ticks_ms()

        return connected

    def is_connected(self):
        return self.isconnected()

    def uptime_ms(self):
        if (
            not self.isconnected()
            or self._connected_at is None
        ):
            return 0

        return time.ticks_diff(
            time.ticks_ms(),
            self._connected_at,
        )

    def rssi(self):
        if not self.isconnected():
            return None

        return self.wlan.status("rssi")

    def get_status(self):
        connected = self.isconnected()

        return {
            "state": self._state,
            "ssid": self._ssid if connected else None,
            "ip": (
                self.wlan.ifconfig()[0]
                if connected
                else None
            ),
            "rssi": (
                self.wlan.status("rssi")
                if connected
                else None
            ),
            "uptime_ms": (
                self.uptime_ms()
                if connected
                else 0
            ),
            "attempts": self._attempts,
            "last_error": self._last_error,
        }
