import importlib.util
import json
from pathlib import Path
import sys

import pytest


@pytest.fixture
def parser_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "ujson", json)

    path = Path(__file__).resolve().parents[1] / "src" / "parser.py"
    spec = importlib.util.spec_from_file_location(
        "parser_under_test",
        path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "content_type",
    ["application/json", "text/json"],
)
def test_parses_json(parser_module, content_type):
    factory = parser_module.ParserFactory()

    result = factory.parse(
        b'{"version": "0.1.0", "files": []}',
        content_type,
    )

    assert result == {"version": "0.1.0", "files": []}


def test_rejects_invalid_json(parser_module):
    factory = parser_module.ParserFactory()

    with pytest.raises(ValueError):
        factory.parse(b"{invalid json}", "application/json")


def test_rejects_unknown_content_type(parser_module):
    factory = parser_module.ParserFactory()

    with pytest.raises(ValueError, match="No parser"):
        factory.parse(b"example", "application/unknown")


def test_supports_registered_parser(parser_module):
    class TextParser:
        def parse(self, data):
            return data.decode("utf-8")

    factory = parser_module.ParserFactory()
    factory.register("text/plain", TextParser())

    assert factory.parse(b"hello", "text/plain") == "hello"