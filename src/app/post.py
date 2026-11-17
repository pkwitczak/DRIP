"""Called by main. No startup ownership and no actuator activity."""
import time

def run(env, net, ota, heartbeat, stable_ms=10000, timeout_ms=60000):
    if stable_ms < 0 or timeout_ms <= stable_ms:
        raise ValueError("Invalid POST timing")
    env.get_core_required("WIFI_SSID")
    env.get_core_required("WIFI_PASSWORD")
    env.get_core_required("OTA_BASE_URL")
    if env.get_int("DEFAULT_WATERING_SECONDS", 300) < 0:
        raise ValueError("Invalid watering duration")
    # Fetch and validate actual manifest, not just an HTTP status.
    # This does not stage anything and is safe during a trial.
    ota.probe()
    started = time.ticks_ms()
    stable_since = None
    while True:
        heartbeat()
        now = time.ticks_ms()
        if time.ticks_diff(now, started) >= timeout_ms:
            raise RuntimeError("POST timeout")
        if net.is_connected():
            if stable_since is None:
                stable_since = now
            if time.ticks_diff(now, stable_since) >= stable_ms:
                return
        else:
            stable_since = None
        time.sleep_ms(100)
