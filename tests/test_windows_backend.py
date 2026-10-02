import threading
from types import SimpleNamespace

import pytest

from assistant.platform.base import AccessError, FocusChanged, Target, UnsupportedTarget
from assistant.platform.windows.backend import PATTERNS, UIABackend, _send_unicode


class FakeComError(Exception):
    def __init__(self, hresult):
        self.hresult = hresult


def backend_with_patterns(mapping):
    backend = UIABackend.__new__(UIABackend)
    backend.owner = threading.get_ident()
    backend.com_error = FakeComError
    backend.uia = SimpleNamespace(
        **{name: name for _, name, _ in PATTERNS.values()},
        IUIAutomationTextPattern="IUIAutomationTextPattern",
    )

    class Element:
        CurrentIsPassword = False
        CurrentIsEnabled = True
        CurrentIsKeyboardFocusable = True
        CurrentBoundingRectangle = SimpleNamespace(left=2, top=4, right=12, bottom=24)
        CurrentName = "0001 香港"
        CurrentControlType = 50004

        def GetCurrentPattern(self, pattern_id):
            if pattern_id not in mapping:
                raise FakeComError(-2147220988)  # UIA_E_NOTSUPPORTED 0x80040204
            pattern = mapping[pattern_id]
            return SimpleNamespace(QueryInterface=lambda interface: pattern)

        def SetFocus(self):
            self.focused = True

    element = Element()
    backend.target_of = lambda e: Target(7, "100")
    backend.assert_access = lambda target: None
    backend.foreground_target = lambda: Target(7, "100")
    return backend, element


@pytest.mark.parametrize("action", ["invoke", "select", "toggle", "expand", "collapse"])
def test_semantic_pattern_methods_are_dispatched(action):
    pattern_id, _, method = PATTERNS[action]
    called = []
    pattern = SimpleNamespace(**{method: lambda: called.append(method)})
    backend, element = backend_with_patterns({pattern_id: pattern})
    backend.perform(element, action, None)
    assert called == [method]


def test_value_pattern_round_trip_keeps_exact_text():
    pattern = SimpleNamespace(CurrentValue="0001 廣東話", CurrentIsReadOnly=False)
    pattern.SetValue = lambda text: setattr(pattern, "CurrentValue", text)
    backend, element = backend_with_patterns({10002: pattern})
    assert backend.read(element) == "0001 廣東話"
    backend.perform(element, "set_value", "00002 普通話 English")
    assert backend.read(element) == "00002 普通話 English"
    assert "set_value" in backend.describe(element)["actions"]


def test_readonly_value_not_offered_and_dispatch_refused():
    pattern = SimpleNamespace(CurrentValue="constant", CurrentIsReadOnly=True)
    backend, element = backend_with_patterns({10002: pattern})
    assert "set_value" not in backend.describe(element)["actions"]
    with pytest.raises(UnsupportedTarget):
        backend.perform(element, "set_value", "new")


def test_text_pattern_uses_bounded_document_range_and_preserves_content():
    limits = []
    document = SimpleNamespace(GetText=lambda limit: limits.append(limit) or "001\n香港")
    backend, element = backend_with_patterns({10014: SimpleNamespace(DocumentRange=document)})
    assert backend.read(element) == "001\n香港"
    assert limits == [16385]


def test_exact_read_refuses_truncation():
    backend, element = backend_with_patterns({10002: SimpleNamespace(CurrentValue="x" * 16385)})
    with pytest.raises(UnsupportedTarget):
        backend.read(element)


@pytest.mark.parametrize(
    "pattern_id,property_name,state,expected",
    [
        (10015, "CurrentToggleState", 1, "on"),
        (10015, "CurrentToggleState", 2, "indeterminate"),
        (10010, "CurrentIsSelected", True, "selected"),
        (10005, "CurrentExpandCollapseState", 0, "collapsed"),
        (10005, "CurrentExpandCollapseState", 1, "expanded"),
    ],
)
def test_semantic_state_is_available_for_readback(pattern_id, property_name, state, expected):
    backend, element = backend_with_patterns(
        {pattern_id: SimpleNamespace(**{property_name: state})}
    )
    assert backend.read(element) == expected


def test_protected_control_never_queries_pattern_or_text():
    backend, element = backend_with_patterns({})
    element.CurrentIsPassword = True
    element.GetCurrentPattern = lambda pattern_id: pytest.fail("Protected content accessed")
    assert backend.read(element) is None
    observed = backend.describe(element)
    assert observed["name"] == ""
    assert observed["value"] is None
    assert not observed["actions"]


def test_dead_provider_errors_are_not_mistaken_for_unsupported_pattern():
    backend, element = backend_with_patterns({})

    def dead(pattern_id):
        raise FakeComError(-2147220991)  # UIA_E_ELEMENTNOTAVAILABLE

    element.GetCurrentPattern = dead
    with pytest.raises(FakeComError):
        backend.read(element)


def test_backend_requires_constructing_thread():
    backend, element = backend_with_patterns({})
    backend.owner = -1
    with pytest.raises(AccessError):
        backend.read(element)


def test_semantic_dispatch_rechecks_foreground():
    called = []
    backend, element = backend_with_patterns(
        {10000: SimpleNamespace(Invoke=lambda: called.append(1))}
    )
    backend.foreground_target = lambda: Target(8, "200")
    with pytest.raises(FocusChanged):
        backend.perform(element, "invoke", None)
    assert not called


def test_unicode_sendinput_uses_utf16_units_and_keyup_pairs():
    observed = []

    class Send:
        def __call__(self, count, events, size):
            observed.extend((e.type, e.ki.wVk, e.ki.wScan, e.ki.dwFlags) for e in events)
            return count

    _send_unicode(SimpleNamespace(SendInput=Send()), "香港𠮷")
    units = [0x9999, 0x6E2F, 0xD842, 0xDFB7]
    assert observed == [(1, 0, unit, flags) for unit in units for flags in (4, 6)]


def test_partial_unicode_input_is_uncertain_error():
    class Send:
        def __call__(self, count, events, size):
            return count - 1

    with pytest.raises(AccessError, match="outcome unknown"):
        _send_unicode(SimpleNamespace(SendInput=Send()), "香港")


@pytest.mark.parametrize("handle", [None, 0])
def test_handleless_focused_child_resolves_its_native_parent(handle):
    backend = UIABackend.__new__(UIABackend)
    backend.owner = threading.get_ident()
    parent = SimpleNamespace(CurrentNativeWindowHandle=100)
    child = SimpleNamespace(CurrentProcessId=7, CurrentNativeWindowHandle=handle)
    backend.walker = SimpleNamespace(GetParentElement=lambda element: parent)
    backend.user32 = SimpleNamespace(GetAncestor=lambda hwnd, flags: 100)
    assert backend.target_of(child) == Target(7, "100")


def test_handleless_element_without_native_ancestor_is_unsupported():
    backend = UIABackend.__new__(UIABackend)
    backend.owner = threading.get_ident()
    child = SimpleNamespace(CurrentProcessId=7, CurrentNativeWindowHandle=None)
    backend.walker = SimpleNamespace(GetParentElement=lambda element: None)
    backend.user32 = SimpleNamespace(
        GetAncestor=lambda hwnd, flags: pytest.fail("No native handle")
    )
    with pytest.raises(UnsupportedTarget):
        backend.target_of(child)
