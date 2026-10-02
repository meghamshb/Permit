import pytest

import assistant.platform.base as base


def test_old_target_cannot_be_used_after_switching_windows():
    registry = base.SnapshotRegistry()
    registry.begin(base.Target(1, "first"))
    old = registry.add(object())
    registry.begin(base.Target(1, "second"))
    with pytest.raises(base.StaleReference):
        registry.get(old)


def test_delayed_instruction_cannot_use_expired_observation(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(base, "monotonic", lambda: clock[0])
    registry = base.SnapshotRegistry(max_age=10)
    registry.begin(base.Target(1, "first"))
    ref = registry.add(object())
    clock[0] = 111.0
    with pytest.raises(base.StaleReference):
        registry.get(ref)
