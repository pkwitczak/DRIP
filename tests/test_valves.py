import importlib.util
from pathlib import Path
import sys
import types

import pytest


class FakePin:
    OUT = 1

    def __init__(self, number, mode):
        self.number = number
        self.mode = mode
        self.level = None

    def high(self):
        self.level = 1

    def low(self):
        self.level = 0


@pytest.fixture
def valve_class(monkeypatch):
    machine = types.ModuleType("machine")
    machine.Pin = FakePin
    monkeypatch.setitem(sys.modules, "machine", machine)

    path = Path(__file__).resolve().parents[1] / "src" / "app" / "valves.py"
    spec = importlib.util.spec_from_file_location(
        "valves_under_test",
        path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Valve


def test_initial_state_is_closed_and_output_is_low(valve_class):
    valve = valve_class(15)

    assert valve.getState() == valve_class.State.CLOSED
    assert valve.relay.level == 0
    assert valve.relay.number == 15


def test_open_sets_output_high(valve_class):
    valve = valve_class(15)

    valve.openValve()

    assert valve.getState() == valve_class.State.OPEN
    assert valve.relay.level == 1


def test_close_sets_output_low(valve_class):
    valve = valve_class(15)
    valve.openValve()

    valve.closeValve()

    assert valve.getState() == valve_class.State.CLOSED
    assert valve.relay.level == 0


@pytest.mark.parametrize("state", [0, 1])
def test_set_state(valve_class, state):
    valve = valve_class(15)

    valve.setState(state)

    assert valve.getState() == state
    assert valve.relay.level == state
