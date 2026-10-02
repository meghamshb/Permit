"""Synthetic granted-file qualification. Touches only a new temporary directory."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter, time

from assistant.platform.base import PermissionDenied, UnsupportedTarget
from assistant.routes.files import FileGrant, FileRoute


def main():
    start = perf_counter()
    with TemporaryDirectory(prefix="permit-files-fixture-") as directory:
        grant = FileGrant(
            "synthetic",
            Path(directory).resolve(),
            frozenset({"list", "read", "write", "move"}),
            time() + 60,
        )
        route = FileRoute(grant)
        try:
            written = route.write("first.txt", b"Permit fixture: 007\n")
            assert route.read("first.txt")[1].sha256 == written.observation.sha256
            route.move("first.txt", "second.txt", expected_sha256=written.observation.sha256)
            assert route.list() == ("second.txt",)
            grant.revoked = True
            try:
                route.read("second.txt")
            except PermissionDenied:
                pass
            else:
                raise AssertionError("Revoked grant was accepted.")
            print(
                json.dumps(
                    {
                        "status": "verified-synthetic",
                        "operations": 5,
                        "elapsed_ms": round((perf_counter() - start) * 1000, 2),
                    }
                )
            )
        except UnsupportedTarget:
            print(json.dumps({"status": "unsupported", "reason": "secure-file-backend"}))
            raise SystemExit(2) from None


if __name__ == "__main__":
    main()
