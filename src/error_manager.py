"""Route error reports to console, storage and LED signaling."""

import sys
import storage
from exceptions import CriticalError

ERROR_NETWORK = "network"
ERROR_FILE_WRITE = "file_write"
ERROR_FILE_READ = "file_read"
ERROR_PIN_SET = "pin_set"


PATTERN_NETWORK = (
    200, 1800,
)

PATTERN_FILE_WRITE = (
    200, 200,
    200, 1400,
)

PATTERN_FILE_READ = (
    200, 200,
    200, 200,
    200, 1000,
)

PATTERN_PIN_SET = (
    200, 200,
    200, 200,
    200, 200,
    200, 1000,
)


PATTERNS = {
    ERROR_NETWORK: PATTERN_NETWORK,
    ERROR_FILE_WRITE: PATTERN_FILE_WRITE,
    ERROR_FILE_READ: PATTERN_FILE_READ,
    ERROR_PIN_SET: PATTERN_PIN_SET,
}


class ErrorManager:
    def __init__(self, config, led_manager=None):
        self._console_enabled = self._require_bool(
            config.error_console_enabled,
            "ERROR_CONSOLE_ENABLED",
        )

        self._file_enabled = self._require_bool(
            config.error_file_enabled,
            "ERROR_FILE_ENABLED",
        )

        self._led_enabled = self._require_bool(
            config.error_led_enabled,
            "ERROR_LED_ENABLED",
        )

        self._log_path = None

        if self._file_enabled:
            log_path = config.error_log_path

            if (
                not isinstance(log_path, str)
                or not log_path.strip()
            ):
                raise ValueError(
                    "ERROR_LOG_PATH must be a non-empty string"
                )

            self._log_path = log_path

        self._led_manager = None
        self.bind_led(led_manager)

        self.last_error = None
        self.last_console_error = None
        self.last_file_error = None
        self.last_led_error = None

    def _require_bool(self, value, key):
        if type(value) is not bool:
            raise ValueError(
                "%s must be a boolean" % key
            )

        return value

    def bind_led(self, led_manager):
        if led_manager is not None:
            if (
                not callable(
                    getattr(led_manager, "play", None)
                )
                or not callable(
                    getattr(led_manager, "stop", None)
                )
            ):
                raise TypeError(
                    "LED manager must provide play() and stop()"
                )

        self._led_manager = led_manager

    def report(self, exception, event=None):
        if not isinstance(exception, BaseException):
            raise TypeError(
                "Expected an exception"
            )

        self.last_error = exception

        message = self._format(
            exception,
            event,
        )

        if self._console_enabled:
            self._report_console(
                message,
                exception,
            )

        signal_event = event

        if self._file_enabled:
            try:
                storage.append_text(
                    self._log_path,
                    message + "\n",
                )
                storage.sync()

            except Exception as write_error:
                self.last_file_error = write_error
                signal_event = ERROR_FILE_WRITE

                # Never report a failed log write
                # by writing to the same log again.
                if self._console_enabled:
                    self._report_console(
                        self._format(
                            write_error,
                            ERROR_FILE_WRITE,
                        ),
                        write_error,
                    )

        self._signal(signal_event)

    def clear_signal(self):
        if self._led_manager is None:
            return

        try:
            self._led_manager.stop()

        except Exception as exception:
            self.last_led_error = exception

    def _format(self, exception, event):
        level = (
            "CRITICAL"
            if isinstance(exception, CriticalError)
            else "ERROR"
        )

        category = (
            event
            if event is not None
            else "general"
        )

        message = str(exception)
        message = message.replace("\r", "\\r")
        message = message.replace("\n", "\\n")

        return "[%s] [%s] %s: %s" % (
            level,
            category,
            type(exception).__name__,
            message,
        )

    def _report_console(self, message, exception):
        try:
            print(message)

            print_exception = getattr(
                sys,
                "print_exception",
                None,
            )

            if print_exception is not None:
                print_exception(exception)

        except Exception as console_error:
            self.last_console_error = console_error

    def _signal(self, event):
        if not self._led_enabled:
            return

        if self._led_manager is None:
            return

        pattern = PATTERNS.get(event)

        if pattern is None:
            return

        try:
            self._led_manager.play(pattern)

        except Exception as led_error:
            self.last_led_error = led_error
