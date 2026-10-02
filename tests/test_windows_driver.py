from dataclasses import dataclass, field

import pytest

from assistant.platform.base import (
    AccessError,
    AppInfo,
    FocusChanged,
    PermissionDenied,
    StaleReference,
    Target,
    UncertainAction,
    UnsupportedTarget,
)
from assistant.platform.windows import WindowsDriver


@dataclass
class Element:
    name: str
    value: str | None = None
    control_type: int = 50004
    actions: tuple[str, ...] = ("set_value", "focus")
    enabled: bool = True
    password: bool = False
    children: list = field(default_factory=list)


class FakeBackend:
    def __init__(self):
        self.target = Target(7, "100")
        self.edit = Element("Fixture input", "001 香港")
        self.button = Element("Apply", control_type=50000, actions=("invoke",))
        self.root = Element(
            "Permit fixture", control_type=50032, actions=(), children=[self.edit, self.button]
        )
        self.focus = self.edit
        self.foreground = self.target
        self.denied = False
        self.dead = False
        self.dispatched = []
        self.raise_dispatch = False

    def assert_access(self, target):
        if self.denied:
            raise PermissionDenied("Elevated/inaccessible fixture")
        if target != self.target:
            raise UnsupportedTarget("Unknown target")

    def list_apps(self):
        return [AppInfo(7, "Permit fixture")]

    def focused_element(self):
        return self.focus

    def foreground_target(self):
        return self.foreground

    def target_of(self, element):
        if self.dead:
            raise AccessError("Element disappeared")
        return self.target

    def resolve(self, target):
        return self.root

    def children(self, element, limit):
        return element.children[:limit]

    def describe(self, element):
        return {
            "name": element.name,
            "value": None if element.password else element.value,
            "control_type": element.control_type,
            "enabled": element.enabled,
            "password": element.password,
            "actions": element.actions,
        }

    def read(self, element):
        return None if element.password else element.value

    def same(self, first, second):
        return first is second

    def perform(self, element, action, value):
        self.dispatched.append((action, value))
        if self.raise_dispatch:
            raise RuntimeError("Provider timed out after dispatch")
        if action == "set_value":
            element.value = value
        if action == "focus":
            self.focus = element

    def insert_unicode(self, element, target, value):
        self.dispatched.append(("insert_unicode", value))
        element.value = (element.value or "") + value


def edit_ref(driver, backend):
    return driver.snapshot(backend.target).nodes[0].children[0].ref


def test_import_and_construction_do_not_require_windows():
    driver = WindowsDriver()
    assert driver._backend is None


def test_snapshot_exact_read_semantic_dispatch_and_fresh_readback():
    backend = FakeBackend()
    driver = WindowsDriver(backend)
    assert driver.list_apps() == [AppInfo(7, "Permit fixture")]
    assert driver.focused().role == "text field"
    ref = edit_ref(driver, backend)
    assert driver.read(ref) == "001 香港"
    receipt = driver.act(ref, "set_value", "0002 廣東話 Mandarin English")
    assert receipt.status == "submitted"
    assert receipt.method == "UIA:set_value"
    with pytest.raises(StaleReference):
        driver.read(ref)
    assert driver.read(edit_ref(driver, backend)) == "0002 廣東話 Mandarin English"


def test_new_snapshot_invalidates_prior_refs():
    backend = FakeBackend()
    driver = WindowsDriver(backend)
    ref = edit_ref(driver, backend)
    edit_ref(driver, backend)
    with pytest.raises(StaleReference):
        driver.act(ref, "set_value", "new")
    assert not backend.dispatched


def test_expired_snapshot_ref_never_dispatches():
    backend = FakeBackend()
    driver = WindowsDriver(backend, max_age=-1)
    ref = edit_ref(driver, backend)
    with pytest.raises(StaleReference):
        driver.act(ref, "set_value", "new")
    assert not backend.dispatched


def test_wrong_foreground_is_rejected_and_refs_invalidated():
    backend = FakeBackend()
    driver = WindowsDriver(backend)
    ref = edit_ref(driver, backend)
    backend.foreground = Target(8, "200")
    with pytest.raises(FocusChanged):
        driver.act(ref, "set_value", "new")
    with pytest.raises(StaleReference):
        driver.read(ref)
    assert not backend.dispatched


@pytest.mark.parametrize("state", ["disabled", "password", "denied", "dead"])
def test_unsafe_target_never_dispatches(state):
    backend = FakeBackend()
    driver = WindowsDriver(backend)
    ref = edit_ref(driver, backend)
    if state == "disabled":
        backend.edit.enabled = False
    elif state == "password":
        backend.edit.password = True
    elif state == "denied":
        backend.denied = True
    else:
        backend.dead = True
    with pytest.raises(AccessError):
        driver.act(ref, "set_value", "new")
    assert not backend.dispatched


def test_uncertain_dispatch_invalidates_refs_and_never_retries():
    backend = FakeBackend()
    driver = WindowsDriver(backend)
    ref = edit_ref(driver, backend)
    backend.raise_dispatch = True
    with pytest.raises(UncertainAction):
        driver.act(ref, "set_value", "new")
    with pytest.raises(StaleReference):
        driver.act(ref, "set_value", "new")
    assert len(backend.dispatched) == 1


def test_snapshot_node_and_depth_limits_are_explicit():
    backend = FakeBackend()
    driver = WindowsDriver(backend, max_nodes=2)
    tree = driver.snapshot(backend.target)
    assert len(tree.nodes[0].children) == 1
    assert tree.truncated
    driver = WindowsDriver(backend, max_depth=0)
    tree = driver.snapshot(backend.target)
    assert not tree.nodes[0].children
    assert tree.truncated


@pytest.mark.parametrize("action", ["press_enter", "click", "type", "send"])
def test_generic_keys_and_unknown_actions_are_rejected(action):
    backend = FakeBackend()
    driver = WindowsDriver(backend)
    with pytest.raises(UnsupportedTarget):
        driver.act(edit_ref(driver, backend), action)
    assert not backend.dispatched


def test_unicode_insertion_is_opt_in_and_requires_no_value_pattern():
    backend = FakeBackend()
    driver = WindowsDriver(backend)
    backend.edit.actions = ("focus",)
    with pytest.raises(UnsupportedTarget):
        driver.act(edit_ref(driver, backend), "insert_text", "香港")
    driver.allow_synthetic = True
    backend.edit.actions = ("set_value", "focus")
    with pytest.raises(UnsupportedTarget):
        driver.act(edit_ref(driver, backend), "insert_text", "香港")
    backend.edit.actions = ("focus",)
    receipt = driver.act(edit_ref(driver, backend), "insert_text", "香港𠮷")
    assert receipt.method == "SendInput:UNICODE"
    assert backend.dispatched == [("insert_unicode", "香港𠮷")]


def test_unicode_insertion_requires_exact_control_focus():
    backend = FakeBackend()
    backend.edit.actions = ()
    driver = WindowsDriver(backend, allow_synthetic=True)
    ref = edit_ref(driver, backend)
    backend.focus = backend.button
    with pytest.raises(FocusChanged):
        driver.act(ref, "insert_text", "香港")
    assert not backend.dispatched


@pytest.mark.parametrize("text", ["", "hello\n", "hello\r", "\t", "x" * 4097])
def test_unicode_insertion_never_submits_control_keys(text):
    backend = FakeBackend()
    backend.edit.actions = ()
    driver = WindowsDriver(backend, allow_synthetic=True)
    with pytest.raises(ValueError):
        driver.act(edit_ref(driver, backend), "insert_text", text)
    assert not backend.dispatched
