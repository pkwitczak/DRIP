from singleton import singleton

@singleton
class EnvFile:
    def __init__(self, path="settings.env"):
        self.path = path
        self._file = None
        self._data = None

    def open(self):
        self._file = open(self.path, "r")
        self._data = {}
        for line in self._file:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            self._data[key.strip()] = value.strip()
        return self

    def close(self):
        if self._file is not None:
            self._file.close()
        self._file = None

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def get(self, key, default=None):
        if self._data is None:
            raise RuntimeError("EnvFile nieotwarty – użyj z 'with' lub wywołaj .open()")
        return self._data.get(key, default)

    def get_required(self, key):
        value = self.get(key)
        if value is None:
            raise RuntimeError(f"Brak wymaganej zmiennej w {self.path}: {key}")
        return value


env = EnvFile()