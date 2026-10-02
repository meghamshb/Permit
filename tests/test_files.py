import hashlib
import os
from time import time

import pytest

from assistant.platform.base import (
    AccessError,
    PermissionDenied,
    UncertainAction,
    UnsupportedTarget,
)
from assistant.routes.files import FileGrant, FileRoute

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor backend")


@pytest.fixture
def route(tmp_path):
    # macOS pytest paths may begin with /var -> /private/var; canonicalize authority root.
    return FileRoute(
        FileGrant(
            "test", tmp_path.resolve(), frozenset({"read", "write", "list", "move"}), time() + 60
        )
    )


def test_write_read_observed_and_move(route):
    receipt = route.write("first.txt", b"007\n\xe4\xb8\xad")
    assert receipt.status == "observed"
    assert route.read("first.txt")[0] == b"007\n\xe4\xb8\xad"
    moved = route.move("first.txt", "second.txt", expected_sha256=receipt.observation.sha256)
    assert moved.observation.sha256 == receipt.observation.sha256
    assert route.list() == ("second.txt",)


def test_overwrite_requires_fresh_hash(route):
    old = route.write("file", b"before")
    with pytest.raises(PermissionDenied):
        route.write("file", b"bad")
    with pytest.raises(PermissionDenied):
        route.write("file", b"bad", expected_sha256="wrong")
    receipt = route.write("file", b"after", expected_sha256=old.observation.sha256)
    assert receipt.observation.sha256 == hashlib.sha256(b"after").hexdigest()


@pytest.mark.parametrize(
    "path", ["../outside", "/etc/passwd", "sub/../../file", "C:\\file", "sub\\file", "file\x00", ""]
)
def test_path_escape_denied(route, path):
    with pytest.raises(PermissionDenied):
        route.read(path)


def test_symlink_directory_and_file_cannot_escape(route, tmp_path):
    outside = tmp_path.parent / "outside-fixture"
    outside.mkdir()
    (outside / "secret").write_bytes(b"private")
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    (tmp_path / "file-link").symlink_to(outside / "secret")
    for action in (
        lambda: route.read("link/secret"),
        lambda: route.write("link/secret", b"changed"),
        lambda: route.read("file-link"),
    ):
        with pytest.raises(AccessError):
            action()
    assert (outside / "secret").read_bytes() == b"private"


def test_hardlink_denied(route, tmp_path):
    outside = tmp_path.parent / "hardlink-fixture"
    outside.write_bytes(b"private")
    os.link(outside, tmp_path / "hardlink")
    with pytest.raises(PermissionDenied):
        route.read("hardlink")


@pytest.mark.parametrize("condition", ["expiry", "revocation", "mode", "authority"])
def test_live_authorization(route, condition):
    if condition == "expiry":
        route.grant.expires_at = time() - 1
    elif condition == "revocation":
        route.grant.revoked = True
    elif condition == "mode":
        route.grant.modes = frozenset({"read"})
    else:
        route.authority = lambda grant, mode: False
    with pytest.raises(PermissionDenied):
        route.write("denied", b"body")
    assert not (route.grant.root / "denied").exists()


def test_bounded_reads_and_listing(route):
    route.max_bytes = 2
    with pytest.raises(AccessError):
        route.write("large", b"abc")
    (route.grant.root / "large").write_bytes(b"abc")
    with pytest.raises(AccessError):
        route.read("large")
    route.max_entries = 1
    (route.grant.root / "other").write_bytes(b"a")
    with pytest.raises(AccessError):
        route.list()


def test_move_never_overwrites(route):
    first = route.write("first", b"first")
    route.write("second", b"second")
    with pytest.raises(AccessError):
        route.move("first", "second", expected_sha256=first.observation.sha256)
    assert route.read("first")[0] == b"first"
    assert route.read("second")[0] == b"second"


def test_revoke_before_atomic_publication(route, monkeypatch):
    actual_fsync = os.fsync

    def revoke(descriptor):
        actual_fsync(descriptor)
        route.grant.revoked = True

    monkeypatch.setattr(os, "fsync", revoke)
    with pytest.raises(PermissionDenied):
        route.write("result", b"body")
    assert not (route.grant.root / "result").exists()
    assert list(route.grant.root.iterdir()) == []


def test_failed_post_commit_observation_is_uncertain(route, monkeypatch):
    actual = route._read_at
    calls = 0

    def fail_after_commit(parent, name):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic observation failure")
        return actual(parent, name)

    monkeypatch.setattr(route, "_read_at", fail_after_commit)
    with pytest.raises(UncertainAction):
        route.write("result", b"body")
    assert (route.grant.root / "result").read_bytes() == b"body"


def test_unsupported_backend_fails_closed(route, monkeypatch):
    monkeypatch.setattr("assistant.routes.files.os.name", "nt")
    with pytest.raises(UnsupportedTarget):
        route.list()
