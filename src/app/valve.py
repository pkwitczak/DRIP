from machine import Pin, Timer
from time import ticks_ms, ticks_diff


class ValvePinError(Exception):
    def __init__(self, pin, setpoint, actual):
        self.pin = pin
        self.setpoint = setpoint
        self.actual = actual

        super().__init__(
            "Valve GPIO %s verification failed: "
            "setpoint %d, got %d"
            % (pin, setpoint, actual)
        )


class Valve:
    class State:
        OPEN = 1
        CLOSED = 0

    def __init__(
        self,
        pin,
        check_interval_ms=50,
        verify_delay_ms=10,
    ):
        if (
            type(check_interval_ms) is not int
            or check_interval_ms <= 0
        ):
            raise ValueError(
                "check_interval_ms must be a positive integer"
            )

        if (
            type(verify_delay_ms) is not int
            or verify_delay_ms < 0
        ):
            raise ValueError(
                "verify_delay_ms must be a non-negative integer"
            )

        self.pin = pin
        self._verify_delay_ms = verify_delay_ms

        self._active = False
        self._writing = False

        # Immutable snapshot:
        # setpoint, actual, pending, error, checked_at_ms
        self._status = (
            Valve.State.CLOSED,
            None,
            True,
            None,
            None,
        )

        self.relay = Pin(
            pin,
            Pin.OUT,
            value=Valve.State.CLOSED,
        )

        self._command_at_ms = ticks_ms()
        self._timer = Timer()
        self._active = True

        try:
            self._timer.init(
                period=check_interval_ms,
                mode=Timer.PERIODIC,
                callback=self._refresh,
                hard=False,
            )
        except Exception:
            self._active = False
            self._timer.deinit()
            raise

    @property
    def state(self):
        return self._status[1]

    @property
    def setpoint(self):
        return self._status[0]

    @property
    def pending(self):
        return self._status[2]

    def openValve(self):
        self.setState(
            Valve.State.OPEN
        )

    def closeValve(self):
        self.setState(
            Valve.State.CLOSED
        )

    def setState(self, newstate):
        if (
            type(newstate) is not int
            or newstate not in (
                Valve.State.CLOSED,
                Valve.State.OPEN,
            )
        ):
            raise ValueError(
                "Invalid valve state: %s"
                % (newstate,)
            )

        if not self._active:
            raise RuntimeError(
                "Valve monitoring is stopped"
            )

        self._writing = True

        try:
            # A previous reading does not confirm a new command.
            self._status = (
                newstate,
                None,
                True,
                None,
                None,
            )

            self.relay.value(newstate)
            self._command_at_ms = ticks_ms()

        except Exception as exception:
            self._status = (
                newstate,
                None,
                False,
                exception,
                None,
            )
            raise

        finally:
            self._writing = False

    def getState(self):
        """Return the last sampled GPIO state, or None."""
        return self._status[1]

    def getError(self):
        """Return the current verification error, or None."""
        return self._status[3]

    def getStatus(self):
        """Return a detached snapshot for higher layers."""
        (
            setpoint,
            actual,
            pending,
            error,
            checked_at_ms,
        ) = self._status

        return {
            "pin": self.pin,
            "setpoint": setpoint,
            "state": actual,
            "pending": pending,
            "verified": (
                not pending
                and error is None
                and actual == setpoint
            ),
            "monitoring": self._active,
            "error": (
                str(error)
                if error is not None
                else None
            ),
            "checked_at_ms": checked_at_ms,
        }

    def deinit(self):
        """Stop monitoring without changing the GPIO output."""
        self._active = False
        self._timer.deinit()

    def _refresh(self, timer):
        if not self._active or self._writing:
            return

        now = ticks_ms()

        if (
            ticks_diff(now, self._command_at_ms)
            < self._verify_delay_ms
        ):
            return

        setpoint = self._status[0]

        try:
            actual = self.relay.value()

            if actual != setpoint:
                error = ValvePinError(
                    self.pin,
                    setpoint,
                    actual,
                )
            else:
                error = None

        except Exception as exception:
            actual = None
            error = exception

        self._status = (
            setpoint,
            actual,
            False,
            error,
            now,
        )
