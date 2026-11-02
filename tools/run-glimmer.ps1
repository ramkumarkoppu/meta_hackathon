# Start llama.cpp's llama-server with Muse Glimmer for Locked Computer Assist.
# Local only: listens on 127.0.0.1, needs the key in .run\llama_api_key, no web UI.
# Everything stays inside E:\meta_hackathon (models\, tools\llama.cpp\, .run\, .cache\).
#
# Usage (PowerShell, from anywhere):
#   E:\meta_hackathon\tools\run-glimmer.ps1                 # Unsloth UD-Q4_K_XL + BF16 projector + DFlash drafter
#   E:\meta_hackathon\tools\run-glimmer.ps1 -Model meta     # Meta's own Q4KM files (for the A/B test)
#   E:\meta_hackathon\tools\run-glimmer.ps1 -Projector q4km # Meta's smaller vision projector (same file as Unsloth's kquant)
#   E:\meta_hackathon\tools\run-glimmer.ps1 -NoDFlash       # without the DFlash speculative drafter
#   E:\meta_hackathon\tools\run-glimmer.ps1 -DraftMax 8     # draft up to 8 tokens per step (default 15)
#   E:\meta_hackathon\tools\run-glimmer.ps1 -Ctx 65536      # larger context (default 32768)
# Stop it with Ctrl+C.
param(
    [ValidateSet("unsloth", "meta")] [string]$Model = "unsloth",
    [ValidateSet("", "bf16", "q4km")] [string]$Projector = "",   # default: bf16 with unsloth, q4km with meta
    # DFlash is on by default: measured 24 Oct with the GPU held near 55 W, a full-screen read took 23-26 s with it (generation
    # 46 tok/s at draft length 15) against 32-34 s without, with the same 27/27 lines. -DFlash is kept
    # for older scripts and changes nothing.
    [switch]$NoDFlash,
    [switch]$DFlash,
    # Meta's drafter has dflash.block_size 16, so llama.cpp drafts at most 15 tokens per step.
    [ValidateRange(1, 15)] [int]$DraftMax = 15,
    [int]$Port = 8001,
    [int]$Ctx = 32768
)
$ErrorActionPreference = "Stop"
if ($NoDFlash -and $DFlash) { throw "-NoDFlash and -DFlash contradict each other" }
$useDFlash = -not $NoDFlash

# llama.cpp binds its port with SO_REUSEADDR, so on Windows a second server can listen on the same port and
# requests go to either one. Start only when no llama-server is running and nothing listens on the port.
$running = @(Get-Process llama-server -ErrorAction SilentlyContinue | ForEach-Object Id)
$listening = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | ForEach-Object OwningProcess)
if ($running.Count -or $listening.Count) {
    $ids = ($running + $listening | Sort-Object -Unique) -join ", "
    throw "refusing to start: a llama-server is already running or port $Port is in use (process $ids); stop it first"
}

$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root "tools\llama.cpp\llama-server.exe"
$run = Join-Path $root ".run"
New-Item -ItemType Directory -Force -Path $run | Out-Null

if ($Model -eq "unsloth") {
    $weights = Join-Path $root "models\unsloth\Muse-Glimmer-30B-UD-Q4_K_XL.gguf"
} else {
    $weights = Join-Path $root "models\Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf"
}
if (-not $Projector) { $Projector = $(if ($Model -eq "unsloth") { "bf16" } else { "q4km" }) }
# (PowerShell names are case-insensitive, so the file path must not be called $projector.)
$mmproj = $(if ($Projector -eq "bf16") { Join-Path $root "models\unsloth\mmproj-Muse-Glimmer-30B-BF16.gguf" }
            else { Join-Path $root "models\mmproj-Muse-Glimmer-30B-Q4_K_M.gguf" })
$drafter = Join-Path $root "models\dflash-Muse-Glimmer-30B-Q4_K_M.gguf"
$required = @($exe, $weights, $mmproj)
if ($useDFlash) { $required += $drafter }
foreach ($f in $required) {
    if (-not (Test-Path -LiteralPath $f)) { throw "missing: $f" }
}

# A random key so that other programs on this laptop (or web pages) can't use the model server.
$keyFile = Join-Path $run "llama_api_key"
if (-not (Test-Path $keyFile)) {
    $bytes = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $key = [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_').TrimEnd('=')
    Set-Content -Path $keyFile -Value $key -NoNewline -Encoding ascii
}
# llama-server silently runs without any authentication if the key file has no usable line.
if ((Get-Content -LiteralPath $keyFile -Raw) -notmatch '^[A-Za-z0-9_-]{32}$') {
    throw "refusing to start: $keyFile is empty or malformed, so llama-server would run without a key; delete it to generate a new one"
}

# Keep NVIDIA's compiled-kernel cache inside the project instead of the user profile.
$env:CUDA_CACHE_PATH = Join-Path $root ".cache\nv"
New-Item -ItemType Directory -Force -Path $env:CUDA_CACHE_PATH | Out-Null

# llama.cpp applies these config files and LLAMA_* variables before the command line, so they could
# silently turn on prompt logging, slot saving or a download. It also reads AIP_* (AIP_MODE=PREDICTION
# replaces --port) and MTMD_BACKEND_DEVICE. Only the flags below may configure it.
foreach ($base in $env:ProgramData, $env:APPDATA) {
    if (-not $base) { continue }
    $ini = Join-Path $base "llama.cpp\config.ini"
    if (Test-Path -LiteralPath $ini) { throw "refusing to start: $ini would change llama-server's settings; remove it first" }
}
foreach ($v in @(Get-ChildItem Env: | Where-Object { $_.Name -like "LLAMA_*" -or $_.Name -like "AIP_*" -or $_.Name -eq "MTMD_BACKEND_DEVICE" })) {
    Write-Warning "ignoring environment variable $($v.Name)"
    Remove-Item "Env:$($v.Name)"
}

# Battery, USB-C (PD) power and a quiet power mode all held this laptop's GPU to about 55 W under load
# (measured 24 Oct: 1.3 GHz instead of up to 3 GHz). Full power (120 W) needs its 240 W barrel charger.
# The checks below match this laptop's power plan names; on other laptops they may simply not fire.
$scheme = powercfg /getactivescheme
$battery = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue | Select-Object -First 1
if ($battery -and $battery.BatteryStatus -eq 1) {
    Write-Warning "On battery: the GPU is capped at about 55 W and Glimmer runs much slower. Connect the laptop's full-power charger."
} elseif ($scheme -match "\(PD ") {
    Write-Warning "A USB-C (PD) power plan is active: the GPU is power-limited in every mode. Connect the laptop's full-power charger."
} elseif ($scheme -match "\(Silent\)") {
    Write-Warning "Quiet power mode: the GPU is capped at about 55 W and Glimmer runs much slower. Switch the laptop to a performance mode."
}

$argv = @(
    "-m", $weights, "--mmproj", $mmproj,
    # One request slot and a 32K context keep the whole model on the GPU. The server's default
    # (4 slots x 131K) made room for the context by pushing work to the CPU: about 5 tokens/s.
    "-np", "1", "-c", "$Ctx",
    "-ngl", "999", "--fit", "on", "-fa", "auto",
    # Read the files instead of memory-mapping them: from this exFAT drive a cold start took 72 s
    # instead of 130 s (24 Oct).
    "--load-mode", "none",
    "--image-max-tokens", "4096",
    "--host", "127.0.0.1", "--port", "$Port", "--alias", "muse-glimmer", "--no-webui",
    "--api-key-file", $keyFile,
    # Privacy: no cross-site browser access, no /slots endpoint, no model downloads, and no prompt cache or
    # context checkpoints copied to RAM. The one slot still holds the last request until the next replaces it.
    # --offline does not stop the server fetching an http(s) image URL sent in a request, so clients send
    # images only as data: URIs. There is no --log-file: even at the default level a parse warning can print
    # model output. Never add -v, -lv 5 or --log-prompts-dir: they log prompts and screenshots.
    "--cors-origins", "localhost", "--no-cors-credentials", "--no-slots",
    "--cache-ram", "0", "--ctx-checkpoints", "0", "--offline",
    "--temp", "1.0", "--top-p", "0.95", "--top-k", "64"
)
if ($useDFlash) {
    $argv += @("--spec-type", "draft-dflash", "-md", $drafter, "-ngld", "999", "--spec-draft-n-max", "$DraftMax")
}
Write-Host ("llama-server: $Model model, $Projector projector" + $(if ($useDFlash) { ", DFlash (draft $DraftMax)" } else { "" }) +
            " on http://127.0.0.1:$Port (key in .run\llama_api_key)")
# Windows PowerShell 5.1 turns a native program's stderr lines into error records when the caller redirects
# this script's output; under "Stop" the first one would end the script and kill the server.
$ErrorActionPreference = "Continue"
& $exe @argv
