"""requests is a separately installed MicroPython dependency, outside slots."""
import requests

class HttpClient:
    def open(self, url):
        # Requires a requests version supporting timeout. A blocking operation
        # longer than the watchdog budget deliberately results in a reset.
        return requests.get(url, stream=True, timeout=3)

    @staticmethod
    def checked(response):
        if response.status_code != 200:
            raise RuntimeError("HTTP status %s" % response.status_code)
        return response

    def probe(self, url, heartbeat):
        heartbeat()
        response = self.open(url)
        try:
            self.checked(response)
        finally:
            response.close()
        heartbeat()
