"""Shared configuration with mutable instance settings."""

from singleton import singleton


_MISSING = object()

_ALLOWED_KEY_CHARACTERS = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789_"
)


@singleton
class Config:
    def __init__(self):
        self._loaded = False

        self._base = {}
        self._overrides = {}

        self._core_keys = set()
        self._instance_keys = set()

    def load(self, env_reader):
        if self._loaded:
            raise RuntimeError(
                "Configuration already loaded"
            )

        snapshot = env_reader.snapshot()

        core = self._normalize_settings(
            snapshot["core"]
        )

        instance = self._normalize_settings(
            snapshot["instance"]
        )

        for key in instance:
            if (
                key in core
                or key.startswith(
                    ("WIFI_", "OTA_")
                )
            ):
                raise ValueError(
                    "Instance overrides reserved core key: "
                    + key
                )

        values = {}
        values.update(core)
        values.update(instance)

        self._validate_attribute_names(values)

        config_type = type(self)

        for key in values:
            attribute = self._make_attribute(key)

            setattr(
                config_type,
                key.lower(),
                attribute,
            )

            setattr(
                config_type,
                key,
                attribute,
            )

        self._base = values
        self._overrides = {}

        self._core_keys = set(core)
        self._instance_keys = set(instance)

        self._loaded = True

        return self

    def _normalize_key(self, key):
        if not isinstance(key, str):
            raise TypeError(
                "Configuration key must be a string"
            )

        if not key or not all(
            character in _ALLOWED_KEY_CHARACTERS
            for character in key
        ):
            raise ValueError(
                "Invalid configuration key"
            )

        return key.upper()

    def _normalize_settings(self, settings):
        if not isinstance(settings, dict):
            raise TypeError(
                "Configuration settings must be a dictionary"
            )

        result = {}

        for raw_key, value in settings.items():
            key = self._normalize_key(raw_key)

            if key in result:
                raise ValueError(
                    "Duplicate configuration key: " + key
                )

            result[key] = self._copy_value(value)

        return result

    def _validate_attribute_names(self, values):
        reserved = {
            name.upper()
            for name in dir(type(self))
        }

        for key in values:
            if key.startswith("_") or key in reserved:
                raise ValueError(
                    "Configuration key conflicts with "
                    "Config API: " + key
                )

    def _make_attribute(self, key):
        def read(instance):
            return instance.get_required(key)

        def write(instance, value):
            instance.set(key, value)

        return property(read, write)

    def _require_loaded(self):
        if not self._loaded:
            raise RuntimeError(
                "Configuration not loaded"
            )

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        self._require_loaded()

        raise AttributeError(
            "Unknown configuration attribute: " + name
        )

    def _copy_value(self, value, ancestors=None):
        value_type = type(value)

        if (
            value is None
            or value_type is str
            or value_type is bool
            or value_type is int
        ):
            return value

        if value_type is float:
            if value - value != 0:
                raise ValueError(
                    "Configuration cannot contain "
                    "non-finite numbers"
                )

            return value

        if value_type not in (dict, list):
            raise TypeError(
                "Unsupported configuration value type"
            )

        if ancestors is None:
            ancestors = set()

        identity = id(value)

        if identity in ancestors:
            raise ValueError(
                "Configuration cannot contain "
                "cyclic structures"
            )

        ancestors.add(identity)

        try:
            if value_type is list:
                return [
                    self._copy_value(
                        item,
                        ancestors,
                    )
                    for item in value
                ]

            result = {}

            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError(
                        "Configuration object keys "
                        "must be strings"
                    )

                result[key] = self._copy_value(
                    item,
                    ancestors,
                )

            return result

        finally:
            ancestors.remove(identity)

    def _effective_value(self, key, default=None):
        if key in self._overrides:
            return self._overrides[key]

        return self._base.get(
            key,
            default,
        )

    def get(self, key, default=None):
        self._require_loaded()
        key = self._normalize_key(key)

        value = self._effective_value(
            key,
            _MISSING,
        )

        if value is _MISSING:
            return default

        return self._copy_value(value)

    def get_required(self, key):
        value = self.get(
            key,
            _MISSING,
        )

        if value is _MISSING:
            raise AttributeError(
                "Unknown configuration setting: "
                + self._normalize_key(key)
            )

        return value

    def _require_instance_key(self, key):
        self._require_loaded()
        key = self._normalize_key(key)

        if key in self._core_keys:
            raise ValueError(
                "Core setting is read-only: " + key
            )

        if key not in self._instance_keys:
            raise ValueError(
                "Unknown instance setting: " + key
            )

        return key

    def set(self, key, value):
        key = self._require_instance_key(key)

        # Validate and copy before publishing the change.
        value = self._copy_value(value)

        self._overrides[key] = value

    def reset(self, key):
        key = self._require_instance_key(key)

        self._overrides.pop(
            key,
            None,
        )

    def snapshot(self):
        self._require_loaded()

        values = self._copy_value(
            self._base
        )

        values.update(
            self._copy_value(
                self._overrides
            )
        )

        return values


config = Config()
