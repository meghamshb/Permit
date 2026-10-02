"""Explicit, pinned MLX download. Inference requires the local artifact manifest."""

import hashlib
import json
import sys
from pathlib import Path

MODEL = "mlx-community/Qwen3-ASR-0.6B-8bit"
REVISION = "89e96d92ba34aca20b3e29fb10cc284097d1219f"


def main():
    if sys.platform != "darwin":
        raise SystemExit("Run this setup on an Apple Silicon Mac with the mac-asr extra")
    from huggingface_hub import snapshot_download

    destination = Path("models/qwen3-asr-mlx").resolve()
    snapshot_download(
        MODEL,
        revision=REVISION,
        local_dir=destination,
        allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model", "README.md", "LICENSE*"],
    )
    files = {}
    for path in destination.rglob("*"):
        if path.is_file() and ".cache" not in path.parts and path.name != "permit-model.json":
            with path.open("rb") as stream:
                files[str(path.relative_to(destination))] = hashlib.file_digest(
                    stream, "sha256"
                ).hexdigest()
    (destination / "permit-model.json").write_text(
        json.dumps({"model": MODEL, "revision": REVISION, "files": files}, indent=2),
        encoding="utf-8",
    )
    print(f"Pinned model ready: {destination}. Run the live speech matrix on this Mac.")


if __name__ == "__main__":
    main()
