import sys
import recovery

POST_TIMEOUT_MS = 90000

def _log(exc):
    printer = getattr(sys, "print_exception", None)
    if printer:
        printer(exc)
    else:
        print(type(exc).__name__, str(exc))

def _load_modules(root):
    # Hardware reset is mandatory between releases: no cross-slot import cache.
    names = ("env_reader", "net", "ota_policy", "ota", "post")
    sys.path.insert(0, root)
    modules = {}
    for name in names:
        if name in sys.modules:
            raise RuntimeError("Release module imported before slot selection: " + name)
        module = __import__(name)
        path = getattr(module, "__file__", "")
        if not path.startswith(root + "/"):
            raise RuntimeError("Module loaded outside selected slot: " + name)
        modules[name] = module
    return modules

def _normal_work(net, ota, heartbeat, clock):
    # Existing nonblocking irrigation work belongs in this same loop.
    # No app.py/application.py or second launcher is involved.
    last_check = clock.ticks_ms()
    while True:
        heartbeat()
        if clock.ticks_diff(clock.ticks_ms(), last_check) >= 60000:
            last_check = clock.ticks_ms()
            try:
                net.connect(heartbeat)
                if ota.check_and_prepare():
                    return  # main performs reset after candidate state is durable
            except Exception as exc:
                _log(exc)
                if ota.recovery.read()["phase"] != "stable":
                    raise
        clock.sleep_ms(50)

def run():
    import machine
    import time

    wdt = machine.WDT(timeout=8000)
    manager = None
    trial = False
    confirmed = False
    started = time.ticks_ms()

    def heartbeat():
        if trial and not confirmed:
            if time.ticks_diff(time.ticks_ms(), started) >= POST_TIMEOUT_MS:
                raise RuntimeError("POST deadline exceeded")
        wdt.feed()

    try:
        manager = recovery.Recovery(heartbeat=heartbeat)
        slot, trial = manager.select()
        started = time.ticks_ms()
        root = manager.slot_path(slot)
        heartbeat()
        modules = _load_modules(root)
        heartbeat()
        env = modules["env_reader"].env
        env.load("/core_settings.env", root + "/instance_settings.env")
        net = modules["net"].Network(env)
        ota = modules["ota"].OtaManager(manager, slot, env, heartbeat)
        net.connect(heartbeat)
        # POST also runs on stable boots, but only a trial can be confirmed.
        modules["post"].run(env, net, ota, heartbeat)
        if trial:
            manager.confirm(slot)
            confirmed = True
            # Cleanup failure must not roll back a confirmed release.
            try:
                manager.cleanup()
            except Exception as exc:
                _log(exc)
        _normal_work(net, ota, heartbeat, time)
    except Exception as exc:
        _log(exc)
        if manager is not None:
            try:
                # Read durable phase rather than relying on local flags:
                # import/select/confirmation may have failed midway.
                state = manager.read()
                if state["phase"] == "trial":
                    manager.rollback()
            except Exception as recovery_exc:
                _log(recovery_exc)
                # A pending trial remains recoverable on the next reset.
    finally:
        machine.reset()

if __name__ == "__main__":
    run()
