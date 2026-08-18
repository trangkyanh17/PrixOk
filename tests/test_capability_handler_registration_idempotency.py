from collections import Counter

import pytest

from bot.modules import atri_capability_bootstrap as capability


class _FakeClient:
    def __init__(self):
        self.handlers = []

    def add_handler(self, handler, group=0):
        callback = getattr(handler, "callback", None)
        self.handlers.append(
            (
                type(handler).__name__,
                getattr(callback, "__name__", repr(callback)),
                group,
            )
        )


@pytest.mark.parametrize("repeat", [2, 3, 10])
def test_capability_registration_is_once_per_client(repeat):
    client = _FakeClient()
    assert capability.add_capability_runtime_handlers(client) is True
    first = list(client.handlers)
    assert len(first) == 5

    for _ in range(repeat - 1):
        assert capability.add_capability_runtime_handlers(client) is False

    assert client.handlers == first
    callbacks = Counter(name for _, name, _ in client.handlers)
    assert callbacks == {
        "capability_command": 1,
        "project_command": 1,
        "plan_command": 1,
        "artifacts_command": 1,
        "artifactfind_command": 1,
    }
    assert {group for _, _, group in client.handlers} == {-16}


def test_capability_registration_is_per_client():
    first = _FakeClient()
    second = _FakeClient()

    assert capability.add_capability_runtime_handlers(first) is True
    assert capability.add_capability_runtime_handlers(second) is True
    assert len(first.handlers) == 5
    assert len(second.handlers) == 5
