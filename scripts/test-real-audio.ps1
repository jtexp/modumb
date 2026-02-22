# test-real-audio.ps1 - End-to-end test of acoustic modem with real audio on Windows
#
# Usage: .\scripts\test-real-audio.ps1
#
# This script performs a complete real audio test:
# 1. Checks Python and dependencies
# 2. Creates a test repository
# 3. Starts the modem server in a background job
# 4. Clones via acoustic modem using real speakers/microphone
# 5. Verifies the clone
# 6. Cleans up
#
# Requires: Python 3.10+, Git, Visual C++ Redistributable

param(
    [string]$TestRepo = "C:\temp\modem-test-repo",
    [string]$CloneDir = "C:\temp\modem-clone-test",
    [switch]$SkipDependencyCheck,
    [switch]$KeepRunning
)

$ErrorActionPreference = "Stop"

Write-Host "========================================"
Write-Host "  Acoustic Modem Real Audio Test"
Write-Host "========================================"
Write-Host ""

# Get script and project directories
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir

# Function to check if a command exists
function Test-Command {
    param([string]$Command)
    $null = Get-Command $Command -ErrorAction SilentlyContinue
    return $?
}

# Function to cleanup background jobs
function Cleanup-Server {
    if ($script:ServerJob) {
        Write-Host ""
        Write-Host "Cleaning up server job..."
        Stop-Job -Job $script:ServerJob -ErrorAction SilentlyContinue
        Remove-Job -Job $script:ServerJob -Force -ErrorAction SilentlyContinue
    }
}

# Register cleanup on script exit
$script:ServerJob = $null
trap {
    Cleanup-Server
    break
}

# Define Python path from venv
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"

# Step 1: Check dependencies
if (-not $SkipDependencyCheck) {
    Write-Host "[1/6] Checking dependencies..."

    # Check Git first (usually in PATH)
    if (-not (Test-Command "git")) {
        Write-Host "ERROR: Git not found in PATH" -ForegroundColor Red
        Write-Host "Install Git from https://git-scm.com or run: winget install Git.Git"
        exit 1
    }

    $gitVersion = git --version 2>&1
    Write-Host "  Git: $gitVersion"

    # Check virtual environment exists
    if (-not (Test-Path $VenvPython)) {
        Write-Host "ERROR: Virtual environment not found at $ProjectDir\.venv" -ForegroundColor Red
        Write-Host "Create it with:"
        Write-Host "  python -m venv .venv"
        Write-Host "  .\.venv\Scripts\activate"
        Write-Host "  pip install -e ."
        exit 1
    }
    Write-Host "  Virtual env: Found"

    # Check Python version using venv Python
    $pythonVersion = & $VenvPython --version 2>&1
    Write-Host "  Python: $pythonVersion"

    # Check modumb is installed
    $modemAudio = Join-Path $ProjectDir "bin\modem-audio"
    if (-not (Test-Path $modemAudio)) {
        Write-Host "ERROR: modem-audio not found in bin/" -ForegroundColor Red
        Write-Host "Run: pip install -e ."
        exit 1
    }
    Write-Host "  Modumb: Installed"

    # Check sounddevice
    try {
        & $VenvPython -c "import sounddevice; print(f'  sounddevice: {sounddevice.__version__}')"
    } catch {
        Write-Host "ERROR: sounddevice not working" -ForegroundColor Red
        Write-Host "Install Visual C++ Redistributable and reinstall: pip install sounddevice"
        exit 1
    }

    Write-Host ""
} else {
    Write-Host "[1/6] Skipping dependency check..."
    Write-Host ""
}

# Add bin to PATH
$env:PATH = "$ProjectDir\bin;$env:PATH"

# Step 2: List audio devices
Write-Host "[2/6] Listing audio devices..."
& $VenvPython -m modumb.cli devices
Write-Host ""

# Step 3: Create test repository
Write-Host "[3/6] Creating test repository at $TestRepo..."

# Remove existing repo if present
if (Test-Path $TestRepo) {
    Remove-Item -Recurse -Force $TestRepo
}

# Create directory and initialize repo
New-Item -ItemType Directory -Force -Path $TestRepo | Out-Null
Push-Location $TestRepo

git init

# Create test content
@"
# Acoustic Modem Test Repository

This repository was transmitted over sound waves using AFSK modulation.

- Frequency: 1200 Hz (mark) / 2200 Hz (space)
- Baud rate: 300 baud
- Transport: Git Smart HTTP over acoustic modem
- Platform: Windows (real audio)
"@ | Set-Content -Path "README.md"

@"
Hello from the acoustic modem!
This file traveled through your speakers and microphone.
Transmitted on Windows using real audio hardware.
"@ | Set-Content -Path "hello.txt"

git add .
git commit -m "Initial commit - test data for acoustic modem"

Pop-Location

Write-Host "  Test repository created"
Write-Host ""

# Step 4: Clean up any previous clone
Write-Host "[4/6] Preparing clone destination..."
if (Test-Path $CloneDir) {
    Remove-Item -Recurse -Force $CloneDir
}
Write-Host "  Clone directory ready: $CloneDir"
Write-Host ""

# Step 5: Start server and run clone
Write-Host "[5/6] Starting modem server and cloning..."
Write-Host ""
Write-Host "  =============================================="
Write-Host "  IMPORTANT: Physical Audio Setup"
Write-Host "  =============================================="
Write-Host "  - Set speaker volume to ~50%"
Write-Host "  - Position microphone near speakers (30cm-1m)"
Write-Host "  - Ensure a quiet environment"
Write-Host "  - You will hear modem tones during transmission"
Write-Host "  =============================================="
Write-Host ""

# Start server in background job
$ServerScript = @"
`$env:PATH = "$ProjectDir\bin;`$env:PATH"
& "$ProjectDir\.venv\Scripts\Activate.ps1"
python -m modumb.cli server "$TestRepo"
"@

$script:ServerJob = Start-Job -ScriptBlock {
    param($testRepo, $projectDir)
    $env:PATH = "$projectDir\bin;$env:PATH"
    $env:PYTHONPATH = "$projectDir\src"
    Set-Location $projectDir
    & "$projectDir\.venv\Scripts\python.exe" -m modumb.http.server $testRepo
} -ArgumentList $TestRepo, $ProjectDir

Write-Host "  Server started (Job ID: $($script:ServerJob.Id))"
Write-Host "  Waiting for server to initialize..."
Start-Sleep -Seconds 3

# Check if server is still running
if ($script:ServerJob.State -ne "Running") {
    Write-Host "ERROR: Server failed to start" -ForegroundColor Red
    Receive-Job -Job $script:ServerJob
    Cleanup-Server
    exit 1
}

Write-Host ""
Write-Host "  Running git clone via acoustic modem..."
Write-Host "  (This may take several minutes at 300 baud)"
Write-Host ""

try {
    git clone modem://audio/repo $CloneDir
    $cloneSuccess = $true
} catch {
    Write-Host "ERROR: Clone failed" -ForegroundColor Red
    Write-Host $_.Exception.Message
    $cloneSuccess = $false
}

# Stop server
if (-not $KeepRunning) {
    Cleanup-Server
}

Write-Host ""

# Step 6: Verify
Write-Host "[6/6] Verifying clone..."
Write-Host ""

if ($cloneSuccess -and (Test-Path "$CloneDir\README.md")) {
    Write-Host "=== Clone successful! ===" -ForegroundColor Green
    Write-Host ""
    Write-Host "Source repository: $TestRepo"
    Write-Host "Cloned to: $CloneDir"
    Write-Host ""
    Write-Host "Contents:"
    Get-ChildItem $CloneDir | Format-Table Name, Length, LastWriteTime
    Write-Host ""
    Write-Host "README.md:"
    Get-Content "$CloneDir\README.md"
    Write-Host ""
    Write-Host "========================================"
    Write-Host "  Test PASSED" -ForegroundColor Green
    Write-Host "========================================"
} else {
    Write-Host "=== Clone FAILED ===" -ForegroundColor Red
    Write-Host "Expected file not found: $CloneDir\README.md"

    if ($script:ServerJob) {
        Write-Host ""
        Write-Host "Server output:"
        Receive-Job -Job $script:ServerJob
    }

    Cleanup-Server
    exit 1
}

Write-Host ""
Write-Host "To clean up test files:"
Write-Host "  Remove-Item -Recurse -Force $TestRepo, $CloneDir"
