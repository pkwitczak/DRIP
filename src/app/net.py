import network
import time

class Network:
    def __init__(self, env):
        self.env = env
        self.wlan = network.WLAN(network.STA_IF)

    def is_connected(self):
        return self.wlan.isconnected()

    def connect(self, heartbeat, timeout_ms=45000):
        self.wlan.active(True)
        if not self.is_connected():
            self.wlan.connect(self.env.get_core_required("WIFI_SSID"),
                              self.env.get_core_required("WIFI_PASSWORD"))
        started = time.ticks_ms()
        while not self.is_connected():
            heartbeat()
            if time.ticks_diff(time.ticks_ms(), started) >= timeout_ms:
                raise RuntimeError("Wi-Fi timeout")
            time.sleep_ms(100)
        heartbeat()
