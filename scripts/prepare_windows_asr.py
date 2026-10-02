"""Explicit download of pinned public weights and a Windows llama.cpp runtime."""

import argparse
import hashlib
import json
import platform
import urllib.request
import zipfile
from pathlib import Path

REVISION = "928ab958557df9aa2ef1c93e0e83c7ad0933fae2"
MODEL = "ggml-org/Qwen3-ASR-0.6B-GGUF"
RUNTIME = "b11349"
ASSETS = (
    (
        "asr/Qwen3-ASR-0.6B-Q8_0.gguf",
        f"https://huggingface.co/{MODEL}/resolve/{REVISION}/Qwen3-ASR-0.6B-Q8_0.gguf",
        "bca259818b50ca7c4c05e9bdb35a5dc04fa039653a6d6f3f0f331f96f6aa1971",
    ),
    (
        "asr/mmproj-Qwen3-ASR-0.6B-Q8_0.gguf",
        f"https://huggingface.co/{MODEL}/resolve/{REVISION}/mmproj-Qwen3-ASR-0.6B-Q8_0.gguf",
        "41a342b5e4c514e968cb756de6cd1b7be39eff43c44c57a2ef5fc6522e36603d",
    ),
    (
        f"runtime/llama-{RUNTIME}-bin-win-vulkan-x64.zip",
        f"https://github.com/ggml-org/llama.cpp/releases/download/{RUNTIME}/"
        f"llama-{RUNTIME}-bin-win-vulkan-x64.zip",
        "7b9bcb45c8dfaec09362383de996de8d56d5e415baa46ccf870cc7a464a37d55",
    ),
)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(path: Path, url: str, expected: str):
    if path.is_file() and digest(path) == expected:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    # Network is confined to this explicit setup command; inference never downloads.
    with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as output:
        while data := response.read(1024 * 1024):
            output.write(data)
    if digest(temporary) != expected:
        raise RuntimeError(f"Checksum mismatch for {path.name}; asset was not installed")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, default=Path("models"))
    args = parser.parse_args()
    if platform.system() != "Windows" or platform.machine().lower() not in {"amd64", "x86_64"}:
        parser.error("This pinned runtime requires Windows x64; use the macOS setup on a Mac")
    destination = args.models_dir.resolve()
    records = []
    for relative, url, expected in ASSETS:
        path = destination / relative
        print(f"Preparing {path.name}", flush=True)
        download(path, url, expected)
        records.append({"path": relative, "url": url, "sha256": expected})
    archive = destination / ASSETS[-1][0]
    runtime = destination / "runtime" / f"llama-{RUNTIME}"
    runtime.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            resolved = (runtime / item.filename).resolve()
            if not resolved.is_relative_to(runtime):
                raise RuntimeError("Runtime archive contains an out-of-directory entry")
        package.extractall(runtime)
    (destination / "permit-assets.json").write_text(
        json.dumps(
            {"model": MODEL, "revision": REVISION, "runtime": RUNTIME, "assets": records}, indent=2
        ),
        encoding="utf-8",
    )
    print(f"Ready: {runtime / 'llama-server.exe'}")


if __name__ == "__main__":
    main()
