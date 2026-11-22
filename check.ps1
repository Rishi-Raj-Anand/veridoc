<#
.SYNOPSIS
    Run every VeriDoc AI quality check (CLAUDE.md section 12) and print a PASS/FAIL table.

.DESCRIPTION
    ai/       ruff check, ruff format --check, pytest
    backend/  ruff check, ruff format --check, pytest
    frontend/ lint, typecheck, format:check, test, build

    Every step runs even after a failure, so one run shows everything that needs fixing.
    The script exits 1 if any step failed. Works in Windows PowerShell 5.1 and PowerShell 7,
    from any working directory.

.PARAMETER Only
    Run one service: ai, backend or frontend.

.PARAMETER Live
    Also run the AI tests that need a real Ollama (pytest -m ollama). Ollama must be running
    with qwen3:8b and bge-m3 pulled.

.EXAMPLE
    .\check.ps1
    .\check.ps1 -Only frontend
    .\check.ps1 -Live -Only ai
#>
[CmdletBinding()]
param(
    [ValidateSet("ai", "backend", "frontend")]
    [string]$Only,
    [switch]$Live
)

$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
$results = New-Object System.Collections.Generic.List[object]

# uv warns when another project's virtual environment is active; each service uses its own.
$env:VIRTUAL_ENV = $null

function Invoke-Step {
    param([string]$Service, [string]$Name, [string]$Command, [string[]]$Arguments)

    Write-Host ""
    Write-Host "=== $Service : $Name" -ForegroundColor Cyan
    $started = Get-Date
    Push-Location (Join-Path $root $Service)
    try {
        & $Command @Arguments
        $ok = ($LASTEXITCODE -eq 0)
    } catch {
        Write-Host $_ -ForegroundColor Red
        $ok = $false
    } finally {
        Pop-Location
    }
    $results.Add([pscustomobject]@{
        Service = $Service
        Step    = $Name
        Result  = if ($ok) { "PASS" } else { "FAIL" }
        Seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
    })
}

function Invoke-PythonChecks {
    param([string]$Service)
    Invoke-Step $Service "ruff check" "uv" @("run", "ruff", "check", ".")
    Invoke-Step $Service "ruff format --check" "uv" @("run", "ruff", "format", "--check", ".")
    Invoke-Step $Service "pytest" "uv" @("run", "pytest", "-q")
}

if (-not $Only -or $Only -eq "ai") {
    Invoke-PythonChecks "ai"
    if ($Live) {
        Invoke-Step "ai" "pytest -m ollama (live)" "uv" @("run", "pytest", "-q", "-m", "ollama")
    }
}
if (-not $Only -or $Only -eq "backend") {
    Invoke-PythonChecks "backend"
}
if (-not $Only -or $Only -eq "frontend") {
    # npm.cmd, not npm: the npm.ps1 shim is blocked under the default execution policy.
    foreach ($script in @("lint", "typecheck", "format:check", "test", "build")) {
        Invoke-Step "frontend" $script "npm.cmd" @("run", $script)
    }
}

Write-Host ""
$results | Format-Table -AutoSize | Out-String | Write-Host
$failed = @($results | Where-Object { $_.Result -eq "FAIL" })
if ($failed.Count -gt 0) {
    Write-Host "$($failed.Count) of $($results.Count) checks FAILED" -ForegroundColor Red
    exit 1
}
Write-Host "All $($results.Count) checks passed" -ForegroundColor Green
exit 0
