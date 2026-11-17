import uos as os
import utime
import machine
from singleton import singleton


STATE_IDLE = "idle"
STATE_CHECKING = "checking"
STATE_UP_TO_DATE = "up_to_date"
STATE_UPDATE_AVAILABLE = "update_available"
STATE_DOWNLOADING = "downloading"
STATE_APPLYING = "applying"
STATE_FAILED = "failed"


class OtaResult:
    def __init__(self, current_version=None, remote_version=None, needs_update=False, error=None):
        self.current_version = current_version
        self.remote_version = remote_version
        self.needs_update = needs_update
        self.error = error


@singleton
class OtaManager:
    def __init__(
        self,
        net,
        base_url,
        local_version="0.0.0",
        manifest_name="manifest.json",
        web_client=None,
        parser_factory=None,
    ):
        self.net = net
        self.base_url = base_url.rstrip("/") + "/"
        self.local_version = local_version
        self.manifest_name = manifest_name
        self.web_client = web_client
        self.parser_factory = parser_factory
        self._state = STATE_IDLE
        self._last_result = None
        self._manifest = None

    def state(self):
        return self._state

    def last_result(self):
        return self._last_result

    def _url(self, path):
        return self.base_url + path.lstrip("/")

    def check(self):
        self._state = STATE_CHECKING
        try:
            if not self.net.isconnected():
                self.net.connect()

            assert self.web_client is not None
            raw = self.web_client.get(self._url(self.manifest_name))
            # na razie sztywno JSON
            assert self.parser_factory is not None
            self._manifest = self.parser_factory.parse(raw, "application/json")

            remote_version = self._manifest.get("version", "0.0.0")
            needs_update = remote_version != self.local_version

            if needs_update:
                self._state = STATE_UPDATE_AVAILABLE
            else:
                self._state = STATE_UP_TO_DATE

            self._last_result = OtaResult(
                current_version=self.local_version,
                remote_version=remote_version,
                needs_update=needs_update,
                error=None,
            )
            return self._last_result

        except Exception as e:
            self._state = STATE_FAILED
            self._last_result = OtaResult(
                current_version=self.local_version,
                remote_version=None,
                needs_update=False,
                error=str(e),
            )
            return self._last_result

    def apply(self):
        if not self._manifest:
            self.check()

        self._state = STATE_DOWNLOADING
        assert self._manifest is not None
        files = self._manifest.get("files", [])

        try:
            for item in files:
                src = item["src"]
                dst = item.get("dst", item["src"])
                tmp = dst + ".tmp"

                assert self.web_client is not None
                data = self.web_client.get(self._url(src))
                with open(dst, "wb") as f:
                    f.write(data)

                try:
                    os.remove(dst)
                except OSError:
                    pass

                os.rename(tmp, dst)

            self._state = STATE_APPLYING
            utime.sleep_ms(200)
            machine.reset()

        except Exception as e:
            self._state = STATE_FAILED
            raise