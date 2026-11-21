"""Exception raising and error reporting entry point."""

import sys

from exceptions import CriticalError
from singleton import singleton


LEVEL_INFO = 20
LEVEL_WARNING = 30
LEVEL_ERROR = 40


@singleton
class ErrorHandler:
    def __init__(self):
        self._level = LEVEL_INFO
        self._reporter = None

    def set_level(self, level):
        if level not in (
            LEVEL_INFO,
            LEVEL_WARNING,
            LEVEL_ERROR,
        ):
            raise ValueError(
                "Invalid logging level"
            )

        self._level = level

    def bind_reporter(self, reporter):
        if (
            reporter is not None
            and not callable(reporter)
        ):
            raise TypeError(
                "Reporter must be callable"
            )

        self._reporter = reporter

    def info(self, message):
        if self._level <= LEVEL_INFO:
            print("[INFO]", message)

    def warning(self, message):
        if self._level <= LEVEL_WARNING:
            print("[WARNING]", message)

    def error(self, error):
        # Preserve the original exception and its type,
        # including CriticalError.
        if isinstance(error, Exception):
            raise error

        raise RuntimeError(str(error))

    def report(self, exception, event=None):
        if not isinstance(exception, BaseException):
            raise TypeError(
                "Expected an exception"
            )

        if self._reporter is not None:
            return self._reporter(
                exception,
                event,
            )

        label = (
            "[CRITICAL]"
            if isinstance(exception, CriticalError)
            else "[ERROR]"
        )

        print(
            label,
            type(exception).__name__,
            str(exception),
        )

        print_exception = getattr(
            sys,
            "print_exception",
            None,
        )

        if print_exception is not None:
            print_exception(exception)


errors = ErrorHandler()
