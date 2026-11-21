"""Application startup, execution and recovery control."""

import asyncio
import sys
import machine
import time

import recovery

from config import config
from env_reader import env
from exceptions import CriticalError
from error_handler import (
    errors,
    LEVEL_INFO,
    LEVEL_WARNING,
    LEVEL_ERROR,
)
from error_manager import (
    ErrorManager,
    ERROR_NETWORK,
)


STARTUP_TIMEOUT_MS = 90000
OTA_CHECK_INTERVAL_MS = 60000
LOOP_INTERVAL_MS = 50

RELEASE_MODULES = (
    "led",
    "net",
    "client",
    "valve_manager",
    "ota_policy",
    "ota",
    "core_control",
)


class _Operation:
    def __init__(
        self,
        boot,
        message,
        policy="fatal",
        event=None,
    ):
        if policy not in ("fatal", "continue", "stable"):
            raise ValueError("Invalid operation policy")

        self.boot = boot
        self.message = message
        self.policy = policy
        self.event = event

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc is None:
            return False

        recoverable = False

        if (
            isinstance(exc, Exception)
            and not isinstance(exc, CriticalError)
        ):
            if self.policy == "continue":
                recoverable = True

            elif self.policy == "stable":
                state = self.boot.manager.read()
                recoverable = (
                    state["phase"] == recovery.PHASE_STABLE
                )

        if recoverable:
            self.boot.report(
                self.message,
                exc,
                self.event,
            )
            return True

        if self.boot.failure_exception is not exc:
            self.boot.failure_exception = exc
            self.boot.failure_message = self.message
            self.boot.failure_event = self.event

        return False


class _BootContext:
    """Own the execution scope and recovery decisions."""

    def __init__(self, machine_module, clock):
        self.machine = machine_module
        self.clock = clock

        self.wdt = None
        self.manager = None

        self.trial = False
        self.confirmed = False
        self.recovering = False

        self.started = clock.ticks_ms()

        self.failure_exception = None
        self.failure_message = (
            "Device startup or operation failed"
        )
        self.failure_event = None

    def __enter__(self):
        return self

    def operation(
        self,
        message,
        policy="fatal",
        event=None,
    ):
        return _Operation(
            self,
            message,
            policy,
            event,
        )

    def heartbeat(self):
        if (
            self.trial
            and not self.confirmed
            and not self.recovering
        ):
            elapsed = self.clock.ticks_diff(
                self.clock.ticks_ms(),
                self.started,
            )

            if elapsed >= STARTUP_TIMEOUT_MS:
                raise CriticalError(
                    "Application startup deadline exceeded"
                )

        if self.wdt is not None:
            self.wdt.feed()

    def report(self, message, exception, event=None):
        try:
            errors.report(exception, event)
            return
        except BaseException:
            pass

        try:
            label = (
                "[CRITICAL]"
                if isinstance(exception, CriticalError)
                else "[ERROR]"
            )

            print(label, message)

            printer = getattr(
                sys,
                "print_exception",
                None,
            )

            if printer is not None:
                printer(exception)
            else:
                print(
                    type(exception).__name__,
                    str(exception),
                )

        except BaseException:
            pass

    def __exit__(self, exc_type, exc, traceback):
        self.recovering = True

        try:
            if exc is not None:
                self.report(
                    self.failure_message,
                    exc,
                    self.failure_event,
                )

                if self.manager is not None:
                    with self.operation(
                        "Recovery failed",
                        policy="continue",
                    ):
                        state = self.manager.read()

                        if (
                            state["phase"]
                            == recovery.PHASE_TRIAL
                        ):
                            self.manager.rollback()

        finally:
            self.machine.reset()

        return False


def _load_modules(root):
    root = root.rstrip("/")

    # Check before importing: release modules import each other.
    for name in RELEASE_MODULES:
        if name in sys.modules:
            raise RuntimeError(
                "Release module imported before slot selection: "
                + name
            )

    sys.path.insert(0, root)

    modules = {}

    for name in RELEASE_MODULES:
        module = __import__(name)
        path = getattr(module, "__file__", "")

        if not path.startswith(root + "/"):
            raise RuntimeError(
                "Module loaded outside selected slot: "
                + name
            )

        modules[name] = module

    return modules


def _configure_log_level(settings):
    level_name = settings.get("LOG_LEVEL", "INFO")

    if not isinstance(level_name, str):
        raise ValueError(
            "LOG_LEVEL must be a string"
        )

    levels = {
        "INFO": LEVEL_INFO,
        "WARNING": LEVEL_WARNING,
        "ERROR": LEVEL_ERROR,
    }

    level = levels.get(level_name.upper())

    if level is None:
        raise ValueError("Invalid LOG_LEVEL")

    errors.set_level(level)


async def _prepare_ota(ota, result):
    """Save the outcome for the main loop to handle."""

    try:
        result["prepared"] = await ota.check_and_prepare()

    except asyncio.CancelledError:
        raise

    except BaseException as exc:
        result["exception"] = exc

    finally:
        result["done"] = True


async def _normal_work(net, ota, control, boot):
    clock = boot.clock
    last_check = clock.ticks_ms()

    ota_task = None
    ota_result = None

    try:
        while True:
            boot.heartbeat()

            with boot.operation(
                "Wi-Fi monitoring or reconnect failed",
                event=ERROR_NETWORK,
            ):
                connected = net.poll()

            with boot.operation(
                "Application operation failed"
            ):
                await control.poll()

            if ota_task is not None and ota_result["done"]:
                # Retrieve task completion before handling the result.
                await ota_task
                ota_task = None

                with boot.operation(
                    "Periodic OTA check failed",
                    policy="stable",
                ):
                    exception = ota_result["exception"]

                    if exception is not None:
                        raise exception

                    if ota_result["prepared"]:
                        return

                ota_result = None

            now = clock.ticks_ms()

            if (
                ota_task is None
                and clock.ticks_diff(now, last_check)
                >= OTA_CHECK_INTERVAL_MS
            ):
                last_check = now

                if connected:
                    ota_result = {
                        "done": False,
                        "prepared": False,
                        "exception": None,
                    }

                    ota_task = asyncio.create_task(
                        _prepare_ota(ota, ota_result)
                    )

            await asyncio.sleep_ms(LOOP_INTERVAL_MS)

    finally:
        if ota_task is not None:
            ota_task.cancel()

            try:
                await ota_task
            except asyncio.CancelledError:
                pass


async def _close_application(
    control,
    client,
    boot,
    preserve_failure,
):
    """Attempt both closes without replacing an existing failure."""

    failure = None

    for resource, message in (
        (control, "Application shutdown failed"),
        (client, "Client shutdown failed"),
    ):
        if resource is None:
            continue

        try:
            await resource.close()

        except BaseException as exc:
            if preserve_failure or failure is not None:
                boot.report(message, exc)
            else:
                failure = exc

        boot.heartbeat()

    if failure is not None:
        with boot.operation(
            "Application shutdown failed",
            policy="continue",
        ):
            raise failure


async def _application(
    modules,
    net,
    slot,
    error_manager,
    boot,
):
    client = None
    control = None
    failed = True

    try:
        with boot.operation(
            "Client initialization failed"
        ):
            client = modules["client"].Client(config)
            boot.heartbeat()

        with boot.operation(
            "Valve manager initialization failed"
        ):
            # Assumption: ValveManager accepts the shared Config.
            valve_manager = modules[
                "valve_manager"
            ].ValveManager(config)

            boot.heartbeat()

        with boot.operation(
            "OTA initialization failed"
        ):
            ota = modules["ota"].OtaManager(
                boot.manager,
                slot,
                config,
                boot.heartbeat,
                client,
            )

            boot.heartbeat()

        with boot.operation(
            "Application initialization failed"
        ):
            control = modules[
                "core_control"
            ].CoreControl(
                client,
                valve_manager,
                config,
            )

            boot.heartbeat()

        if boot.trial:
            with boot.operation(
                "Release confirmation failed"
            ):
                boot.manager.confirm(slot)
                boot.confirmed = True

            with boot.operation(
                "Confirmed release cleanup failed",
                policy="continue",
            ):
                boot.manager.cleanup()

        with boot.operation(
            "Clearing LED indication failed",
            policy="continue",
        ):
            error_manager.clear_signal()

        with boot.operation(
            "Normal operation failed"
        ):
            await _normal_work(
                net,
                ota,
                control,
                boot,
            )

        failed = False

    finally:
        await _close_application(
            control,
            client,
            boot,
            preserve_failure=failed,
        )


def run():
    with _BootContext(machine, time) as boot:
        with boot.operation(
            "Watchdog initialization failed"
        ):
            boot.wdt = machine.WDT(timeout=8000)

        with boot.operation(
            "Recovery initialization or slot selection failed"
        ):
            boot.manager = recovery.Recovery(
                heartbeat=boot.heartbeat
            )

            slot, boot.trial = boot.manager.select()
            boot.started = boot.clock.ticks_ms()

            root = boot.manager.slot_path(slot)

            boot.heartbeat()

        with boot.operation(
            "Configuration loading failed"
        ):
            env.load(
                "/core_settings.env",
                "/instance_settings.env",
            )

            config.load(env)
            boot.heartbeat()

        with boot.operation(
            "Error manager initialization failed"
        ):
            error_manager = ErrorManager(config)
            errors.bind_reporter(error_manager.report)

            _configure_log_level(config)
            boot.heartbeat()

        with boot.operation(
            "Application import failed"
        ):
            modules = _load_modules(root)
            boot.heartbeat()

        with boot.operation(
            "LED initialization failed",
            policy="continue",
        ):
            led = modules["led"].LED()

            led_manager = modules["led"].LedManager(
                led
            )

            error_manager.bind_led(led_manager)

        with boot.operation(
            "Network lifecycle failed",
            event=ERROR_NETWORK,
        ):
            with modules["net"].Network(config) as net:
                with boot.operation(
                    "Initial network connection failed",
                    event=ERROR_NETWORK,
                ):
                    net.connect(boot.heartbeat)
                    boot.heartbeat()

                with boot.operation(
                    "Application lifecycle failed"
                ):
                    asyncio.run(
                        _application(
                            modules,
                            net,
                            slot,
                            error_manager,
                            boot,
                        )
                    )


if __name__ == "__main__":
    run()
