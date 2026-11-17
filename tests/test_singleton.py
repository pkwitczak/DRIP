from singleton import singleton


def test_singleton_returns_same_instance():
    @singleton
    class Example:
        pass

    first = Example()
    second = Example()

    assert first is second


def test_singleton_initializes_only_once():
    calls = []

    @singleton
    class Example:
        def __init__(self, value):
            calls.append(value)
            self.value = value

    first = Example("first")
    second = Example("second")

    assert first is second
    assert second.value == "first"
    assert calls == ["first"]


def test_different_singletons_have_separate_instances():
    @singleton
    class First:
        pass

    @singleton
    class Second:
        pass

    assert First() is not Second()