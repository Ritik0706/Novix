$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$RuntimeRoot = Join-Path $Root "resources\llama"

# Keep this synchronized with the CPU runtime revision currently bundled with Novix.
$Version = "b10818"

$Temp = Join-Path $env:TEMP "novix-llama-runtime-$Version"

if (Test-Path $Temp) {
    Remove-Item $Temp -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $Temp | Out-Null
New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null

function Install-And-Extract {
    param(
        [string]$Url,
        [string]$Destination,
        [string]$ExpectedSha256
    )

    $Archive = Join-Path $Temp ([IO.Path]::GetFileName($Url))

    Write-Host "Downloading:"
    Write-Host "  $Url"

    Invoke-WebRequest `
        -Uri $Url `
        -OutFile $Archive

    $ActualSha256 = (
        Get-FileHash `
            -Algorithm SHA256 `
            -Path $Archive
    ).Hash.ToLowerInvariant()

    if ($ActualSha256 -ne $ExpectedSha256.ToLowerInvariant()) {
        throw (
            "SHA-256 mismatch for $Archive`n" +
            "Expected: $ExpectedSha256`n" +
            "Actual:   $ActualSha256"
        )
    }

    $Extract = Join-Path $Temp ([IO.Path]::GetFileNameWithoutExtension($Archive))

    if (Test-Path $Extract) {
        Remove-Item $Extract -Recurse -Force
    }

    Expand-Archive `
        -Path $Archive `
        -DestinationPath $Extract

    $Server = Get-ChildItem `
        -Path $Extract `
        -Filter "llama-server.exe" `
        -Recurse `
        -File |
        Select-Object -First 1

    if ($null -eq $Server) {
        throw "llama-server.exe was not found in $Archive"
    }

    if (Test-Path $Destination) {
        Remove-Item $Destination -Recurse -Force
    }

    New-Item `
        -ItemType Directory `
        -Force `
        -Path $Destination | Out-Null

    Copy-Item `
        -Path (Join-Path $Server.Directory.FullName "*") `
        -Destination $Destination `
        -Recurse `
        -Force
}

Write-Host ""
Write-Host "IMPORTANT:"
Write-Host "The SHA-256 values below must correspond to the exact"
Write-Host "b10818 release assets selected for this project."
Write-Host ""

# Official llama.cpp b10818 Windows x64 assets.
Install-And-Extract `
    -Url "https://github.com/ggml-org/llama.cpp/releases/download/b10818/llama-b10818-bin-win-cuda-12.4-x64.zip" `
    -Destination (Join-Path $RuntimeRoot "cuda") `
    -ExpectedSha256 "4077274c0eaeb14f4c8f47a4b4a4b2bfd79b130a830da3a6fe5e9defbffe32fa"

Install-And-Extract `
    -Url "https://github.com/ggml-org/llama.cpp/releases/download/b10818/llama-b10818-bin-win-vulkan-x64.zip" `
    -Destination (Join-Path $RuntimeRoot "vulkan") `
    -ExpectedSha256 "c51f9bed613385922d3bf4c8273500308f04034a76f842ec55fccd517c00f5cc"
