# Dependency and model credits

This records owner A's speech dependencies on 3 October 2026. Libraries are used
as dependencies; Permit is not a fork of another assistant. The repository has
not declared a license for Permit itself. The team must choose that separately.
License metadata is evidence of the package declaration, not an eligibility decision.

## Runtime, models and OS voices

| Artifact | Version / revision | License and source |
|---|---|---|
| Python | 3.12.14 on the recorded Windows machine | [PSF license](https://docs.python.org/3/license.html) |
| uv | 0.12.22 | [MIT or Apache-2.0](https://github.com/astral-sh/uv) |
| llama.cpp Windows Vulkan build | b11349, build 11349, commit fb4b2737a | [MIT](https://github.com/ggml-org/llama.cpp/blob/b11349/LICENSE); downloaded locally, not committed |
| Qwen3-ASR base | Qwen3-ASR-0.6B | [Apache-2.0 model card](https://huggingface.co/Qwen/Qwen3-ASR-0.6B) |
| GGUF conversion | ggml-org/Qwen3-ASR-0.6B-GGUF @ 928ab958557df9aa2ef1c93e0e83c7ad0933fae2 | [Conversion card](https://huggingface.co/ggml-org/Qwen3-ASR-0.6B-GGUF/tree/928ab958557df9aa2ef1c93e0e83c7ad0933fae2); base model Apache-2.0; no separate license declared in the conversion metadata checked |
| MLX 8-bit conversion | mlx-community/Qwen3-ASR-0.6B-8bit @ 89e96d92ba34aca20b3e29fb10cc284097d1219f | [Conversion card](https://huggingface.co/mlx-community/Qwen3-ASR-0.6B-8bit/tree/89e96d92ba34aca20b3e29fb10cc284097d1219f); declares Apache-2.0; Mac runtime untested |
| Silero VAD code and packaged model | silero-vad 6.2.3 | [MIT](https://github.com/snakers4/silero-vad/blob/master/LICENSE); one detector, loaded from installed package |
| PortAudio bundled by sounddevice wheels | Version supplied by sounddevice 0.5.6 wheel | [PortAudio license](https://www.portaudio.com/license.html), MIT-style; verify the shipped binary when packaging |
| Windows speech engine / installed voices | Windows 11 build 26200; David, Zira, Mark (en-US), Tracy, Danny (zh-HK) enumerated | Microsoft OS components, subject to installed Windows terms; no voice assets redistributed. Synthesis used David and Tracy. Mandarin voice absent. |
| macOS speech engine / installed voices | AVSpeechSynthesizer; actual installed voices not yet enumerated on Mac | Apple OS components, subject to installed macOS terms; no voice assets redistributed. Sinji/Tingting are planned selections, not measured installation claims. |

The language-prefix integration follows [Qwen's ASR inference implementation](https://github.com/QwenLM/Qwen3-ASR/blob/main/qwen_asr/inference/qwen3_asr.py)
and [llama.cpp's assistant prefill protocol](https://github.com/ggml-org/llama.cpp/blob/b11349/tools/server/README.md).
No writer model rewrites reading or dictation.

Pinned weight and runtime SHA-256 values and download URLs are in
`scripts/prepare_windows_asr.py`. Locally downloaded assets carry a manifest in
the ignored `models/` folder. The Mac preparation command records the exact
conversion revision and hashes. Full upstream license texts and notices must
accompany any redistributed binaries/models; this inventory does not replace them.

## Python lockfile inventory

The table includes transitive packages and all platform/optional branches in
`uv.lock`; it does not imply every package is installed or used on Windows.
Entries without a declared license need upstream review before redistribution.
In particular, optional NVIDIA/CUDA packages carry separate vendor terms and
are not the Vulkan ASR runtime. Use pynput unmodified under its LGPL terms.
The source links below are the versioned PyPI metadata used for this inventory.

| Package | Locked version | Declared license |
|---|---|---|
| [annotated-doc](https://pypi.org/pypi/annotated-doc/0.0.5/json) | 0.0.5 | MIT |
| [anyio](https://pypi.org/pypi/anyio/4.15.1/json) | 4.15.1 | MIT |
| [certifi](https://pypi.org/pypi/certifi/2026.7.22/json) | 2026.7.22 | MPL-2.0 |
| [cffi](https://pypi.org/pypi/cffi/2.1.1/json) | 2.1.1 | MIT-0 |
| [click](https://pypi.org/pypi/click/8.5.0/json) | 8.5.0 | BSD-3-Clause |
| [colorama](https://pypi.org/pypi/colorama/0.4.6/json) | 0.4.6 | BSD License |
| [cuda-bindings](https://pypi.org/pypi/cuda-bindings/13.4.3/json) | 13.4.3 | Apache-2.0 |
| [cuda-pathfinder](https://pypi.org/pypi/cuda-pathfinder/1.8.3/json) | 1.8.3 | Apache-2.0 |
| [cuda-toolkit](https://pypi.org/pypi/cuda-toolkit/13.0.3.0/json) | 13.0.3.0 | Not declared; review upstream |
| [evdev](https://pypi.org/pypi/evdev/2.0.0/json) | 2.0.0 | BSD-3-Clause |
| [filelock](https://pypi.org/pypi/filelock/4.0.9/json) | 4.0.9 | MIT |
| [fsspec](https://pypi.org/pypi/fsspec/2026.9.0/json) | 2026.9.0 | BSD-3-Clause |
| [h11](https://pypi.org/pypi/h11/0.16.0/json) | 0.16.0 | MIT |
| [hf-xet](https://pypi.org/pypi/hf-xet/1.6.0/json) | 1.6.0 | Apache-2.0 |
| [httpcore](https://pypi.org/pypi/httpcore/1.0.9/json) | 1.0.9 | BSD-3-Clause |
| [httpx](https://pypi.org/pypi/httpx/0.28.1/json) | 0.28.1 | BSD-3-Clause |
| [huggingface-hub](https://pypi.org/pypi/huggingface-hub/1.33.0/json) | 1.33.0 | Apache-2.0 |
| [idna](https://pypi.org/pypi/idna/3.20/json) | 3.20 | BSD-3-Clause |
| [iniconfig](https://pypi.org/pypi/iniconfig/2.3.0/json) | 2.3.0 | MIT |
| [jinja2](https://pypi.org/pypi/jinja2/3.1.6/json) | 3.1.6 | BSD License |
| [markdown-it-py](https://pypi.org/pypi/markdown-it-py/4.2.0/json) | 4.2.0 | MIT License |
| [markupsafe](https://pypi.org/pypi/markupsafe/3.0.3/json) | 3.0.3 | BSD-3-Clause |
| [mdurl](https://pypi.org/pypi/mdurl/0.1.2/json) | 0.1.2 | MIT License |
| [miniaudio](https://pypi.org/pypi/miniaudio/1.71/json) | 1.71 | MIT |
| [mlx](https://pypi.org/pypi/mlx/0.32.3/json) | 0.32.3 | MIT |
| [mlx-audio](https://pypi.org/pypi/mlx-audio/0.5.7/json) | 0.5.7 | MIT |
| [mlx-metal](https://pypi.org/pypi/mlx-metal/0.32.3/json) | 0.32.3 | MIT |
| [mpmath](https://pypi.org/pypi/mpmath/1.3.0/json) | 1.3.0 | BSD |
| [networkx](https://pypi.org/pypi/networkx/3.7/json) | 3.7 | BSD-3-Clause |
| [numpy](https://pypi.org/pypi/numpy/2.5.3/json) | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| [nvidia-cublas](https://pypi.org/pypi/nvidia-cublas/13.1.1.3/json) | 13.1.1.3 | LicenseRef-NVIDIA-Proprietary |
| [nvidia-cuda-cupti](https://pypi.org/pypi/nvidia-cuda-cupti/13.0.85/json) | 13.0.85 | LicenseRef-NVIDIA-Proprietary |
| [nvidia-cuda-nvrtc](https://pypi.org/pypi/nvidia-cuda-nvrtc/13.0.88/json) | 13.0.88 | LicenseRef-NVIDIA-Proprietary |
| [nvidia-cuda-runtime](https://pypi.org/pypi/nvidia-cuda-runtime/13.0.96/json) | 13.0.96 | Not declared; review upstream |
| [nvidia-cudnn-cu13](https://pypi.org/pypi/nvidia-cudnn-cu13/9.24.0.43/json) | 9.24.0.43 | Not declared; review upstream |
| [nvidia-cufft](https://pypi.org/pypi/nvidia-cufft/12.0.0.61/json) | 12.0.0.61 | LicenseRef-NVIDIA-Proprietary |
| [nvidia-cufile](https://pypi.org/pypi/nvidia-cufile/1.15.1.6/json) | 1.15.1.6 | LicenseRef-NVIDIA-Proprietary |
| [nvidia-curand](https://pypi.org/pypi/nvidia-curand/10.4.0.35/json) | 10.4.0.35 | LicenseRef-NVIDIA-Proprietary |
| [nvidia-cusolver](https://pypi.org/pypi/nvidia-cusolver/12.0.4.66/json) | 12.0.4.66 | LicenseRef-NVIDIA-Proprietary |
| [nvidia-cusparse](https://pypi.org/pypi/nvidia-cusparse/12.6.3.3/json) | 12.6.3.3 | LicenseRef-NVIDIA-Proprietary |
| [nvidia-cusparselt-cu13](https://pypi.org/pypi/nvidia-cusparselt-cu13/0.8.1/json) | 0.8.1 | NVIDIA Proprietary Software |
| [nvidia-nccl-cu13](https://pypi.org/pypi/nvidia-nccl-cu13/2.30.7/json) | 2.30.7 | Not declared; review upstream |
| [nvidia-nvjitlink](https://pypi.org/pypi/nvidia-nvjitlink/13.4.92/json) | 13.4.92 | LicenseRef-NVIDIA-Proprietary |
| [nvidia-nvshmem-cu13](https://pypi.org/pypi/nvidia-nvshmem-cu13/3.4.5/json) | 3.4.5 | Not declared; review upstream |
| [nvidia-nvtx](https://pypi.org/pypi/nvidia-nvtx/13.0.85/json) | 13.0.85 | Apache 2.0 |
| [packaging](https://pypi.org/pypi/packaging/26.3/json) | 26.3 | Apache-2.0 OR BSD-2-Clause |
| permit-assistant | 0.1.0 | Not declared by repository |
| [pluggy](https://pypi.org/pypi/pluggy/1.6.0/json) | 1.6.0 | MIT |
| [pycparser](https://pypi.org/pypi/pycparser/3.0/json) | 3.0 | BSD-3-Clause |
| [pygments](https://pypi.org/pypi/pygments/2.21.0/json) | 2.21.0 | BSD-2-Clause |
| [pynput](https://pypi.org/pypi/pynput/1.8.1/json) | 1.8.1 | LGPLv3 |
| [pyobjc-core](https://pypi.org/pypi/pyobjc-core/12.2.2/json) | 12.2.2 | MIT |
| [pyobjc-framework-applicationservices](https://pypi.org/pypi/pyobjc-framework-applicationservices/12.2.2/json) | 12.2.2 | MIT |
| [pyobjc-framework-avfoundation](https://pypi.org/pypi/pyobjc-framework-avfoundation/12.2.2/json) | 12.2.2 | MIT |
| [pyobjc-framework-cocoa](https://pypi.org/pypi/pyobjc-framework-cocoa/12.2.2/json) | 12.2.2 | MIT |
| [pyobjc-framework-coreaudio](https://pypi.org/pypi/pyobjc-framework-coreaudio/12.2.2/json) | 12.2.2 | MIT |
| [pyobjc-framework-coremedia](https://pypi.org/pypi/pyobjc-framework-coremedia/12.2.2/json) | 12.2.2 | MIT |
| [pyobjc-framework-coretext](https://pypi.org/pypi/pyobjc-framework-coretext/12.2.2/json) | 12.2.2 | MIT |
| [pyobjc-framework-quartz](https://pypi.org/pypi/pyobjc-framework-quartz/12.2.2/json) | 12.2.2 | MIT |
| [pytest](https://pypi.org/pypi/pytest/9.1.1/json) | 9.1.1 | MIT |
| [pytest-asyncio](https://pypi.org/pypi/pytest-asyncio/1.4.0/json) | 1.4.0 | Apache-2.0 |
| [python-xlib](https://pypi.org/pypi/python-xlib/0.33/json) | 0.33 | LGPLv2+ |
| [pyyaml](https://pypi.org/pypi/pyyaml/6.0.3/json) | 6.0.3 | MIT |
| [regex](https://pypi.org/pypi/regex/2026.9.29/json) | 2026.9.29 | Apache-2.0 AND CNRI-Python |
| [rich](https://pypi.org/pypi/rich/15.0.0/json) | 15.0.0 | MIT |
| [ruff](https://pypi.org/pypi/ruff/0.16.10/json) | 0.16.10 | MIT |
| [safetensors](https://pypi.org/pypi/safetensors/0.8.0/json) | 0.8.0 | Apache Software License |
| [scipy](https://pypi.org/pypi/scipy/1.18.1/json) | 1.18.1 | BSD License |
| [setuptools](https://pypi.org/pypi/setuptools/84.0.0/json) | 84.0.0 | MIT |
| [shellingham](https://pypi.org/pypi/shellingham/1.5.4/json) | 1.5.4 | ISC License |
| [silero-vad](https://pypi.org/pypi/silero-vad/6.2.3/json) | 6.2.3 | MIT License |
| [six](https://pypi.org/pypi/six/1.17.0/json) | 1.17.0 | MIT |
| [sounddevice](https://pypi.org/pypi/sounddevice/0.5.6/json) | 0.5.6 | MIT |
| [sympy](https://pypi.org/pypi/sympy/1.14.0/json) | 1.14.0 | BSD |
| [tokenizers](https://pypi.org/pypi/tokenizers/0.23.2/json) | 0.23.2 | Apache Software License |
| [torch](https://pypi.org/pypi/torch/2.14.1/json) | 2.14.1 | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT |
| [tqdm](https://pypi.org/pypi/tqdm/4.70.1/json) | 4.70.1 | MPL-2.0 AND MIT |
| [transformers](https://pypi.org/pypi/transformers/5.18.0/json) | 5.18.0 | Apache 2.0 License |
| [triton](https://pypi.org/pypi/triton/3.8.0/json) | 3.8.0 | MIT |
| [typer](https://pypi.org/pypi/typer/0.27.2/json) | 0.27.2 | MIT |
| [typing-extensions](https://pypi.org/pypi/typing-extensions/4.16.0/json) | 4.16.0 | PSF-2.0 |
| [winrt-runtime](https://pypi.org/pypi/winrt-runtime/3.2.1/json) | 3.2.1 | MIT |
| [winrt-windows-foundation](https://pypi.org/pypi/winrt-windows-foundation/3.2.1/json) | 3.2.1 | MIT |
| [winrt-windows-foundation-collections](https://pypi.org/pypi/winrt-windows-foundation-collections/3.2.1/json) | 3.2.1 | MIT |
| [winrt-windows-media-speechsynthesis](https://pypi.org/pypi/winrt-windows-media-speechsynthesis/3.2.1/json) | 3.2.1 | MIT |
| [winrt-windows-storage-streams](https://pypi.org/pypi/winrt-windows-storage-streams/3.2.1/json) | 3.2.1 | MIT |

Build tooling is separate from the runtime lockfile: Hatchling uses MIT licensing
and is selected by the build-system requirement in `pyproject.toml`. GitHub CI uses
`actions/checkout@v4` and `astral-sh/setup-uv@v6` (MIT); CI has been added but has not
been executed on GitHub from this checkout. Update this inventory when the lockfile,
runtime, model or packaged voice assets change.
