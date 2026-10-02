# Run in a terminal; the server stays attached and stops with Ctrl+C.
param(
    [ValidateRange(1024, 65535)][int]$Port = 8767,
    [ValidateSet("all", "0")][string]$GpuLayers = "all"
)
$ErrorActionPreference = "Stop"
$permitRoot = Split-Path -Parent $PSScriptRoot
$permitModels = Join-Path $permitRoot "models"
$permitExecutable = Join-Path $permitModels "runtime\llama-b11349\llama-server.exe"
$permitWeights = Join-Path $permitModels "asr\Qwen3-ASR-0.6B-Q8_0.gguf"
$permitProjector = Join-Path $permitModels "asr\mmproj-Qwen3-ASR-0.6B-Q8_0.gguf"
$permitExpected = @{
    $permitWeights = "bca259818b50ca7c4c05e9bdb35a5dc04fa039653a6d6f3f0f331f96f6aa1971"
    $permitProjector = "41a342b5e4c514e968cb756de6cd1b7be39eff43c44c57a2ef5fc6522e36603d"
    (Join-Path $permitModels "runtime\llama-b11349-bin-win-vulkan-x64.zip") = "7b9bcb45c8dfaec09362383de996de8d56d5e415baa46ccf870cc7a464a37d55"
}
foreach ($permitAsset in $permitExpected.Keys) {
    if (!(Test-Path -LiteralPath $permitAsset -PathType Leaf)) {
        throw "Run uv run python scripts/prepare_windows_asr.py first."
    }
    if ((Get-FileHash -LiteralPath $permitAsset -Algorithm SHA256).Hash.ToLowerInvariant() -ne $permitExpected[$permitAsset]) {
        throw "Pinned asset checksum mismatch: $permitAsset"
    }
}
if (!(Test-Path -LiteralPath $permitExecutable -PathType Leaf)) {
    throw "Pinned llama-server executable is missing; rerun the preparation script."
}
Write-Host "ASR listens only at http://127.0.0.1:$Port; offline mode and content logging disabled."
& $permitExecutable --model $permitWeights --mmproj $permitProjector `
    --alias "ggml-org/Qwen3-ASR-0.6B-GGUF" --host 127.0.0.1 --port $Port `
    --offline --log-disable --no-webui --gpu-layers $GpuLayers --ctx-size 2048 --parallel 1
exit $LASTEXITCODE
