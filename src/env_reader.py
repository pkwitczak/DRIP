"""Read and validate typed core and instance settings."""

import storage

from parser import ParserFactory
from singleton import singleton


_MISSING = object()

_ALLOWED_KEY_CHARACTERS = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789_"
)


class EnvFormatError(ValueError):
    def __init__(
        self,
        message,
        source,
        line=None,
        key=None,
    ):
        self.source = source
        self.line = line
        self.key = key

        location = source

        if line is not None:
            location += ", line %d" % line

        if key is not None:
            location += ", key " + key

        super().__init__(
            "%s: %s"
            % (location, message)
        )


@singleton
class EnvReader:
    def __init__(self):
        self._core = {}
        self._instance = {}
        self._loaded = False

        self._parser_factory = ParserFactory()

    def _normalize_key(self, key):
        if not isinstance(key, str):
            raise TypeError(
                "Setting key must be a string"
            )

        if not key or not all(
            character in _ALLOWED_KEY_CHARACTERS
            for character in key
        ):
            raise ValueError(
                "Invalid setting key"
            )

        return key.upper()

    def _copy_value(self, value):
        if isinstance(value, dict):
            return {
                key: self._copy_value(item)
                for key, item in value.items()
            }

        if isinstance(value, list):
            return [
                self._copy_value(item)
                for item in value
            ]

        return value

    def _validate_numbers(self, value):
        if isinstance(value, float):
            # Reject NaN and positive/negative infinity.
            if value - value != 0:
                raise ValueError(
                    "Non-finite number"
                )

        elif isinstance(value, dict):
            for item in value.values():
                self._validate_numbers(item)

        elif isinstance(value, list):
            for item in value:
                self._validate_numbers(item)

    def _parse(self, text, source):
        if not isinstance(text, str):
            raise TypeError(
                "ENV content must be a string"
            )

        values = {}

        for number, raw in enumerate(
            text.splitlines(),
            1,
        ):
            line = raw.strip()

            if not line or line.startswith("#"):
                continue

            if "=" not in line:
                raise EnvFormatError(
                    "Missing '=' separator",
                    source,
                    line=number,
                )

            raw_key, raw_value = line.split(
                "=",
                1,
            )

            raw_key = raw_key.strip()
            raw_value = raw_value.strip()

            try:
                key = self._normalize_key(raw_key)

            except ValueError:
                raise EnvFormatError(
                    "Invalid setting key",
                    source,
                    line=number,
                )

            if key in values:
                raise EnvFormatError(
                    "Duplicate setting",
                    source,
                    line=number,
                    key=key,
                )

            if not raw_value:
                raise EnvFormatError(
                    "Missing setting value",
                    source,
                    line=number,
                    key=key,
                )

            try:
                value = self._parser_factory.parse(
                    raw_value.encode("utf-8"),
                    "application/json",
                )

                self._validate_numbers(value)

            except ValueError:
                # Do not expose the setting value in the message.
                raise EnvFormatError(
                    "Invalid JSON value",
                    source,
                    line=number,
                    key=key,
                )

            values[key] = value

        return values

    def load(
        self,
        core_path="/core_settings.env",
        instance_path="/instance_settings.env",
    ):
        core_text = storage.read_text(
            core_path
        )

        instance_text = storage.read_text(
            instance_path
        )

        return self.load_text(
            core_text,
            instance_text,
            core_source=core_path,
            instance_source=instance_path,
        )

    def load_text(
        self,
        core_text,
        instance_text,
        core_source="core_settings.env",
        instance_source="instance_settings.env",
    ):
        core = self._parse(
            core_text,
            core_source,
        )

        instance = self._parse(
            instance_text,
            instance_source,
        )

        for key in instance:
            if (
                key in core
                or key.startswith(
                    ("WIFI_", "OTA_")
                )
            ):
                raise EnvFormatError(
                    "Instance overrides reserved core key",
                    instance_source,
                    key=key,
                )

        # Preserve the previous configuration if parsing,
        # validation or file reading fails.
        self._core = core
        self._instance = instance
        self._loaded = True

        return self

    def _require_loaded(self):
        if not self._loaded:
            raise RuntimeError(
                "Settings not loaded"
            )

    def get(self, key, default=None):
        self._require_loaded()
        key = self._normalize_key(key)

        if key in self._core:
            return self._copy_value(
                self._core[key]
            )

        if key in self._instance:
            return self._copy_value(
                self._instance[key]
            )

        return default

    def get_core(self, key, default=None):
        self._require_loaded()
        key = self._normalize_key(key)

        if key not in self._core:
            return default

        return self._copy_value(
            self._core[key]
        )

    def get_instance(self, key, default=None):
        self._require_loaded()
        key = self._normalize_key(key)

        if key not in self._instance:
            return default

        return self._copy_value(
            self._instance[key]
        )

    def get_required(self, key):
        value = self.get(key)

        if value is None or value == "":
            raise ValueError(
                "Missing setting: "
                + self._normalize_key(key)
            )

        return value

    def get_core_required(self, key):
        value = self.get_core(key)

        if value is None or value == "":
            raise ValueError(
                "Missing core setting: "
                + self._normalize_key(key)
            )

        return value

    def get_int(self, key, default=None):
        value = self.get(
            key,
            _MISSING,
        )

        if value is _MISSING:
            return default

        if type(value) is not int:
            raise ValueError(
                "Setting must be an integer: "
                + self._normalize_key(key)
            )

        return value

    def get_bool(self, key, default=False):
        value = self.get(
            key,
            _MISSING,
        )

        if value is _MISSING:
            return default

        if type(value) is not bool:
            raise ValueError(
                "Setting must be a boolean: "
                + self._normalize_key(key)
            )

        return value

    def snapshot(self):
        self._require_loaded()

        return {
            "core": self._copy_value(
                self._core
            ),
            "instance": self._copy_value(
                self._instance
            ),
        }


env = EnvReader()
