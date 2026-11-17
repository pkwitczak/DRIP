import importlib

import pytest

import env_reader


@pytest.fixture
def env_class():
    module = importlib.reload(env_reader)
    return module.EnvFile


def test_reads_settings_and_ignores_comments(env_class, tmp_path):
    path = tmp_path / "settings.env"
    path.write_text(
        "# comment\n"
        "\n"
        " WIFI_SSID = garden-network \n"
        "WIFI_PASSWORD=example-password\n"
        "line_without_separator\n",
        encoding="utf-8",
    )

    with env_class(str(path)) as settings:
        assert settings.get_required("WIFI_SSID") == "garden-network"
        assert settings.get_required("WIFI_PASSWORD") == "example-password"


def test_preserves_equals_inside_value(env_class, tmp_path):
    path = tmp_path / "settings.env"
    path.write_text("TOKEN=first=second==\n", encoding="utf-8")

    with env_class(str(path)) as settings:
        assert settings.get_required("TOKEN") == "first=second=="


def test_returns_default_for_missing_key(env_class, tmp_path):
    path = tmp_path / "settings.env"
    path.write_text("", encoding="utf-8")

    with env_class(str(path)) as settings:
        assert settings.get("MISSING", "fallback") == "fallback"


def test_required_missing_key_raises(env_class, tmp_path):
    path = tmp_path / "settings.env"
    path.write_text("", encoding="utf-8")

    with env_class(str(path)) as settings:
        with pytest.raises(RuntimeError, match="WIFI_SSID"):
            settings.get_required("WIFI_SSID")


def test_read_before_open_raises(env_class, tmp_path):
    settings = env_class(str(tmp_path / "settings.env"))

    with pytest.raises(RuntimeError, match="EnvFile"):
        settings.get("WIFI_SSID")


def test_missing_file_raises(env_class, tmp_path):
    settings = env_class(str(tmp_path / "missing.env"))

    with pytest.raises(OSError):
        settings.open()


def test_context_closes_file_on_exception(env_class, tmp_path):
    path = tmp_path / "settings.env"
    path.write_text("KEY=value\n", encoding="utf-8")
    settings = env_class(str(path))
    opened_file = None

    with pytest.raises(ValueError, match="test error"):
        with settings:
            opened_file = settings._file
            raise ValueError("test error")

    assert opened_file.closed
    assert settings._file is None