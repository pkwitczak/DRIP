"""LED driver and non-blocking pattern playback."""

from machine import Pin, Timer
from time import sleep

from singleton import singleton


PATTERN_TICK_MS = 50


@singleton
class LED:
    def __init__(self):
        self.led = Pin(
            "LED",
            Pin.OUT,
            value=0,
        )
        self.timer = Timer()

    def _write(self, value):
        self.led.value(value)

    def on(self):
        self.timer.deinit()
        self._write(1)

    def off(self):
        self.timer.deinit()
        self._write(0)

    def blink(self, f):
        self.timer.deinit()

        self.timer.init(
            freq=f,
            mode=Timer.PERIODIC,
            callback=self._toggle,
        )

    def _toggle(self, timer):
        self.led.toggle()

    def disable(self):
        self.off()

    def blinkfor(self, f, duration):
        try:
            self.blink(f)
            sleep(duration)
        finally:
            self.disable()


class LedManager:
    def __init__(self, led):
        self._led = led
        self._pattern = None
        self._index = 0
        self._remaining_ms = 0

        self.last_error = None

    def play(self, pattern):
        pattern = self._validate_pattern(pattern)

        self.stop()
        self.last_error = None

        try:
            self._pattern = pattern
            self._index = 0
            self._remaining_ms = pattern[0]

            self._led._write(1)

            self._led.timer.init(
                period=PATTERN_TICK_MS,
                mode=Timer.PERIODIC,
                callback=self._tick,
            )

        except Exception:
            self._stop_safely()
            raise

    def stop(self):
        self._pattern = None
        self._index = 0
        self._remaining_ms = 0

        self._led.off()


    def _validate_pattern(self, pattern):
    if not isinstance(pattern, (tuple, list)):
        raise ValueError(
            "LED pattern must be a tuple or list"
        )

    if not pattern or len(pattern) % 2:
        raise ValueError(
            "LED pattern requires on/off pairs"
        )

    for duration in pattern:
        if (
            type(duration) is not int
            or duration <= 0
            or duration % PATTERN_TICK_MS
        ):
            raise ValueError(
                "LED durations must be positive "
                "multiples of %d ms"
                % PATTERN_TICK_MS
            )

    return tuple(pattern)

    def _tick(self, timer):
        if self._pattern is None:
            return

        try:
            self._remaining_ms -= PATTERN_TICK_MS

            if self._remaining_ms > 0:
                return

            self._index = (
                self._index + 1
            ) % len(self._pattern)

            self._remaining_ms = (
                self._pattern[self._index]
            )

            self._led._write(
                1 if self._index % 2 == 0 else 0
            )

        except Exception as exception:
            self.last_error = exception
            self._stop_safely()

    def _stop_safely(self):
        self._pattern = None

        try:
            self._led.off()
        except Exception:
            pass
