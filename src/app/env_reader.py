"""Core and per-release instance settings. Shared module-level reader."""
class EnvReader:
    def __init__(self):
        self._core = {}
        self._instance = {}
        self._loaded = False

    @staticmethod
    def _parse(path, optional=False):
        try:
            f = open(path)
        except OSError as exc:
            if optional and exc.args[0] == 2:
                return {}
            raise
        values = {}
        with f:
            for number, raw in enumerate(f, 1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    raise ValueError("Invalid env line %d" % number)
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip()
                if not key or not all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_" for c in key):
                    raise ValueError("Invalid key on line %d" % number)
                if key in values:
                    raise ValueError("Duplicate setting: " + key)
                if value[:1] in ("'", '"'):
                    if len(value) < 2 or value[-1] != value[0]:
                        raise ValueError("Unterminated quoted value")
                    value = value[1:-1]
                values[key] = value
        return values

    def load(self, core_path="/core_settings.env", instance_path=None):
        core = self._parse(core_path)
        instance = self._parse(instance_path, True) if instance_path else {}
        for key in instance:
            if key in core or key.startswith(("WIFI_", "OTA_")):
                raise ValueError("Instance overrides reserved core key: " + key)
        self._core, self._instance, self._loaded = core, instance, True
        return self

    def get(self, key, default=None):
        if not self._loaded:
            raise RuntimeError("Settings not loaded")
        return self._core[key] if key in self._core else self._instance.get(key, default)

    def get_core(self, key, default=None):
        if not self._loaded:
            raise RuntimeError("Settings not loaded")
        return self._core.get(key, default)

    def get_instance(self, key, default=None):
        if not self._loaded:
            raise RuntimeError("Settings not loaded")
        return self._instance.get(key, default)

    def get_required(self, key):
        value = self.get(key)
        if value is None or value == "":
            raise ValueError("Missing setting: " + key)
        return value

    def get_core_required(self, key):
        value = self.get_core(key)
        if value is None or value == "":
            raise ValueError("Missing core setting: " + key)
        return value

    def get_int(self, key, default=None):
        value = self.get(key)
        return default if value is None else int(value)

    def get_bool(self, key, default=False):
        value = self.get(key)
        if value is None:
            return default
        value = value.lower()
        if value in ("true", "1", "yes", "on"):
            return True
        if value in ("false", "0", "no", "off"):
            return False
        raise ValueError("Invalid boolean: " + key)

env = EnvReader()
