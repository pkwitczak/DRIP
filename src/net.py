# net.py
import network as _net
import utime
from time import sleep
from singleton import singleton

STATE_IDLE = "idle"
STATE_CONNECTING = "connecting"
STATE_CONNECTED = "connected"
STATE_LOST = "lost"


@singleton
class Net:
    def __init__(self, ssid, passwd, timeout_ms=15000, poll_interval_ms=500):
        self.ssid = ssid
        self.passwd = passwd
        self._state = STATE_IDLE
        self._connected_at = None
        self.wlan = None
        self._timeout_ms = timeout_ms
        self._poll_interval_ms = poll_interval_ms

    def __enter__(self):
        if self.wlan is None:
            self.wlan = _net.WLAN(_net.STA_IF)
            self.wlan.active(True)
            self.wlan.config(pm=0xa11140)
            sleep(2)
        return self

    def __exit__(self, type, value, traceback):
        if self.wlan is not None:
            try:
                self.wlan.disconnect()
            except:
                pass
            try:
                self.wlan.active(False)
            except:
                pass
        self._state = STATE_LOST
        self._connected_at = None

    def connect(self):
        if self.wlan is None:
            self.wlan = _net.WLAN(_net.STA_IF)
            self.wlan.active(True)
            self.wlan.config(pm=0xa11140)
            sleep(2)

        self._state = STATE_CONNECTING
        self.wlan.connect(self.ssid, self.passwd)

        start = utime.ticks_ms()
        while not self.wlan.isconnected():
            if utime.ticks_diff(utime.ticks_ms(), start) > self._timeout_ms:
                self._state = STATE_LOST
                raise RuntimeError("WiFi connect timeout")
            utime.sleep_ms(self._poll_interval_ms)


        self._state = STATE_CONNECTED
        self._connected_at = utime.ticks_ms()

    def isconnected(self):
        if self.wlan is None:
            return False

        connected = self.wlan.isconnected()

        if connected and self._state != STATE_CONNECTED:
            self._state = STATE_CONNECTED
            if self._connected_at is None:
                self._connected_at = utime.ticks_ms()

        elif not connected and self._state == STATE_CONNECTED:
            self._state = STATE_LOST
            self._connected_at = None

        return connected

    def uptime_ms(self):
        if self._connected_at is None:
            return 0
        return utime.ticks_diff(utime.ticks_ms(), self._connected_at)

    def rssi(self):
        if self.wlan is None or not self.isconnected():
            return None
        return self.wlan.status("rssi")

    def get_status(self):
        connected = self.isconnected()
        if connected:
            assert self.wlan is not None
            return {
                "state": self._state,
                "ssid": self.ssid if connected else None,
                "ip": self.wlan.ifconfig()[0] if connected else None,
                "rssi": self.rssi() if connected else None,
                "uptime_ms": self.uptime_ms(),
            }
        else:
            return {
                "state": self._state,
                "ssid": None,
                "ip": None,
                "rssi": None,
                "uptime_ms": 0,
            }