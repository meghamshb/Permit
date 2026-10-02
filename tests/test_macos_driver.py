import pytest

from assistant.platform.base import (
    Focus,
    FocusChanged,
    PermissionDenied,
    StaleReference,
    Target,
    UncertainAction,
    UnsupportedTarget,
)
from assistant.platform.macos.driver import MacOSDriver


class FakeMac:
    def __init__(self):
        self.permission = True
        self.target = Target(42, "window")
        self.value = "0007 廣東話"
        self.title = "Input"
        self.secure = False
        self.enabled = True
        self.dispatches = 0
        self.uncertain = False

    def check_permission(self):
        if not self.permission:
            raise PermissionDenied("revoked")

    def root(self, target):
        return "window"

    def identity(self, handle):
        return handle

    def signature(self, handle):
        return handle, self.title

    def focused(self):
        return Focus(self.target)

    def describe(self, handle):
        if handle == "window":
            return dict(
                role="window", name="Fixture", value=None, enabled=True, actions=(), bounds=None
            )
        return dict(
            role="password field" if self.secure else "text field",
            name=self.title,
            value=None if self.secure else self.value,
            enabled=self.enabled,
            actions=("set_value", "focus"),
            bounds=None,
        )

    def children(self, handle):
        return ("input",) if handle == "window" else ()

    def read(self, handle):
        return self.value

    def perform(self, handle, action, value):
        self.dispatches += 1
        self.value = value
        if self.uncertain:
            raise UncertainAction("may have changed")
        return "AX"


def input_ref(driver):
    return driver.snapshot(Target(42)).nodes[0].children[0].ref


def test_semantic_change_requires_fresh_readback():
    backend = FakeMac()
    driver = MacOSDriver(backend)
    ref = input_ref(driver)
    assert driver.read(ref) == "0007 廣東話"
    receipt = driver.act(ref, "set_value", "0042 中文")
    assert receipt.status == "submitted"
    with pytest.raises(StaleReference):
        driver.read(ref)
    assert driver.read(input_ref(driver)) == "0042 中文"


def test_new_snapshot_invalidates_old_refs():
    driver = MacOSDriver(FakeMac())
    old = input_ref(driver)
    input_ref(driver)
    with pytest.raises(StaleReference):
        driver.act(old, "set_value", "bad")


@pytest.mark.parametrize(
    "change,error",
    [
        ("focus", FocusChanged),
        ("identity", StaleReference),
        ("permission", PermissionDenied),
        ("secure", PermissionDenied),
        ("disabled", PermissionDenied),
    ],
)
def test_changed_boundaries_never_dispatch(change, error):
    backend = FakeMac()
    driver = MacOSDriver(backend)
    ref = input_ref(driver)
    if change == "focus":
        backend.target = Target(99, "elsewhere")
    elif change == "identity":
        backend.title = "Another control"
    elif change == "permission":
        backend.permission = False
    elif change == "secure":
        backend.secure = True
    else:
        backend.enabled = False
    with pytest.raises(error):
        driver.act(ref, "set_value", "must not write")
    assert backend.dispatches == 0


def test_uncertain_dispatch_invalidates_ref():
    backend = FakeMac()
    backend.uncertain = True
    driver = MacOSDriver(backend)
    ref = input_ref(driver)
    with pytest.raises(UncertainAction):
        driver.act(ref, "set_value", "changed")
    with pytest.raises(StaleReference):
        driver.act(ref, "set_value", "blind retry")
    assert backend.dispatches == 1
    assert driver.read(input_ref(driver)) == "changed"


def test_synthetic_input_opt_in_and_bounds():
    driver = MacOSDriver(FakeMac(), max_nodes=1)
    tree = driver.snapshot(Target(42))
    assert tree.truncated and not tree.nodes[0].children
    driver = MacOSDriver(FakeMac())
    with pytest.raises(UnsupportedTarget):
        driver.act(input_ref(driver), "type_text", "hello")


def test_expired_ref_never_dispatches():
    driver = MacOSDriver(FakeMac(), max_age=-1)
    ref = input_ref(driver)
    with pytest.raises(StaleReference):
        driver.read(ref)


@pytest.mark.macos
def test_ax_geometry_bridge_roundtrip():
    ax = pytest.importorskip("ApplicationServices")

    value = ax.AXValueCreate(ax.kAXValueCGPointType, (12.0, 34.0))
    ok, point = ax.AXValueGetValue(value, ax.kAXValueCGPointType, None)
    assert ok and (point.x, point.y) == (12.0, 34.0)
