# Windows Testing Guide

This guide covers testing Modumb with real audio hardware on native Windows.

Running Python natively on Windows provides direct access to Windows audio devices (WASAPI/DirectSound) with lower latency than WSL2 audio forwarding.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Audio Device Configuration](#audio-device-configuration)
- [Quick Test](#quick-test)
- [Full End-to-End Test](#full-end-to-end-test)
- [Physical Setup Tips](#physical-setup-tips)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required Software

1. **Python 3.10+**
   ```powershell
   # Install via winget
   winget install Python.Python.3.12

   # Or download from https://python.org
   ```

2. **Git for Windows**
   ```powershell
   winget install Git.Git
   ```

3. **Visual C++ Redistributable** (required for sounddevice)
   ```powershell
   # Usually already installed, but if sounddevice fails:
   winget install Microsoft.VCRedist.2015+.x64
   ```

### Verify Prerequisites

```powershell
python --version    # Should show Python 3.10+
git --version       # Should show git version
```

---

## Installation

### Step 1: Clone the Repository

```powershell
cd C:\Users\$env:USERNAME
git clone https://github.com/jtexp/modumb.git
cd modumb
```

### Step 2: Create Virtual Environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### Step 3: Install Modumb

```powershell
pip install -e .
```

### Step 4: Add bin to PATH

```powershell
$env:PATH = "$PWD\bin;$env:PATH"
```

### Step 5: Verify Installation

```powershell
# List audio devices
python -m modumb.cli devices

# Should show your Windows audio devices
```

---

## Audio Device Configuration

### Windows Sound Settings

1. Open **Settings** → **System** → **Sound**
2. Under **Output**, select your speakers
3. Under **Input**, select your microphone
4. Set speaker volume to approximately 50%

### Disable Exclusive Mode (if having issues)

1. Right-click the speaker icon in the taskbar
2. Select **Sound settings** → **More sound settings**
3. Double-click your output/input device
4. Go to **Advanced** tab
5. Uncheck "Allow applications to take exclusive control"

### List Available Devices

```powershell
python -m modumb.cli devices
```

Example output:
```
Audio Devices:
  0: Microsoft Sound Mapper - Input (input)
  1: Microphone (Realtek Audio) (input)
  2: Microsoft Sound Mapper - Output (output)
  3: Speakers (Realtek Audio) (output)

Default input: 1
Default output: 3
```

### Select Specific Devices

If you have multiple audio devices:

```powershell
# Using environment variables
$env:MODEM_INPUT_DEVICE = 1
$env:MODEM_OUTPUT_DEVICE = 3

# Or pass to commands
modem-git-server -i 1 -o 3 C:\path\to\repo
```

---

## Quick Test

### Test Audio Hardware

```powershell
cd C:\Users\$env:USERNAME\modumb
.\.venv\Scripts\Activate.ps1
$env:PATH = "$PWD\bin;$env:PATH"

# Test audio (plays a tone, shows input levels)
python -m modumb.cli test
```

This should:
1. Play a 1kHz test tone through your speakers
2. Display microphone input levels
3. Report any audio issues

---

## Full End-to-End Test

### Automated Test Script

The easiest way to test is using the automated script:

```powershell
cd C:\Users\$env:USERNAME\modumb
.\scripts\test-real-audio.ps1
```

This script will:
1. Check Python and dependencies
2. Create a test repository
3. Start the modem server in the background
4. Clone via acoustic modem using real audio
5. Verify the clone succeeded
6. Clean up

### Manual Test (Two Terminals)

**Terminal 1 - Start the Server:**

```powershell
cd C:\Users\$env:USERNAME\modumb
.\.venv\Scripts\Activate.ps1
$env:PATH = "$PWD\bin;$env:PATH"

# Create test repository
mkdir C:\temp\test-repo -Force
cd C:\temp\test-repo
git init
"Hello from acoustic modem!" | Set-Content README.md
git add .
git commit -m "Initial commit"

# Start server
cd C:\Users\$env:USERNAME\modumb
modem-git-server C:\temp\test-repo
```

You should see:
```
Acoustic Modem Git Server
Repository: C:\temp\test-repo
Listening for connections...
```

**Terminal 2 - Clone via Modem:**

```powershell
cd C:\Users\$env:USERNAME\modumb
.\.venv\Scripts\Activate.ps1
$env:PATH = "$PWD\bin;$env:PATH"

# Clone via acoustic modem
git clone modem://audio/repo C:\temp\cloned-repo
```

**Verify:**

```powershell
Get-Content C:\temp\cloned-repo\README.md
# Should show: Hello from acoustic modem!
```

---

## Physical Setup Tips

### Speaker and Microphone Positioning

```
                    ┌─────────────┐
                    │   Speakers  │
                    │  (50% vol)  │
                    └──────┬──────┘
                           │
                      30cm - 1m
                           │
                    ┌──────┴──────┐
                    │ Microphone  │
                    │  (facing    │
                    │  speakers)  │
                    └─────────────┘
```

### Best Practices

| Setting | Recommendation |
|---------|----------------|
| Speaker Volume | 50% (avoid distortion) |
| Mic Distance | 30cm - 1m from speakers |
| Environment | Quiet room |
| System Sounds | Disabled during test |
| Notifications | Disabled during test |

### Disable System Sounds

To prevent Windows sounds from interfering:

1. Right-click speaker icon → **Sound settings**
2. Click **More sound settings**
3. Go to **Sounds** tab
4. Set **Sound Scheme** to "No Sounds"

---

## Troubleshooting

### No Audio Devices Found

```
Error: No audio devices found
```

**Solutions:**

1. Install Visual C++ Redistributable:
   ```powershell
   winget install Microsoft.VCRedist.2015+.x64
   ```

2. Reinstall sounddevice:
   ```powershell
   pip uninstall sounddevice
   pip install sounddevice
   ```

3. Check Windows audio service:
   ```powershell
   Get-Service AudioSrv | Select-Object Status
   # Should show: Running
   ```

### Echo/Feedback Loop

**Symptoms:** Continuous high-pitched sound, audio distortion

**Solutions:**

1. Reduce speaker volume to 30-40%
2. Increase distance between microphone and speakers
3. Use headphones for speaker output
4. Mute microphone monitoring in Windows sound settings

### CRC Errors / Data Corruption

**Symptoms:** Clone fails with CRC errors, retransmissions

**Solutions:**

1. Reduce ambient noise (close windows, turn off fans)
2. Check speaker/mic volume levels
3. Move microphone closer to speakers
4. Disable audio enhancements:
   - Sound settings → Device properties → Additional device properties
   - Go to **Enhancements** tab
   - Check "Disable all enhancements"

### Timeout Waiting for ACK

**Symptoms:** Clone hangs, "Timeout waiting for ACK" errors

**Solutions:**

1. Verify microphone is picking up speaker audio
2. Check microphone is not muted
3. Increase microphone sensitivity in Windows settings
4. Run audio test: `python -m modumb.cli test`

### "modem-git-server" Not Found

**Symptoms:** Command not recognized

**Solutions:**

1. Add bin to PATH:
   ```powershell
   $env:PATH = "C:\Users\$env:USERNAME\modumb\bin;$env:PATH"
   ```

2. Or use Python module directly:
   ```powershell
   python -m modumb.cli server C:\path\to\repo
   ```

### Server Starts but Clone Fails

**Symptoms:** Server shows "Listening...", but git clone fails immediately

**Solutions:**

1. Ensure git-remote-modem is in PATH:
   ```powershell
   where.exe git-remote-modem
   # Should show path to bin\git-remote-modem
   ```

2. Check server terminal for error messages

3. Try loopback mode first to verify protocol works:
   ```powershell
   # Terminal 1
   modem-git-server --loopback C:\temp\test-repo

   # Terminal 2
   $env:MODEM_LOOPBACK = 1
   git clone modem://audio/repo C:\temp\clone-test
   ```

### Python Script Errors

**Symptoms:** Import errors, module not found

**Solutions:**

1. Ensure virtual environment is activated:
   ```powershell
   .\.venv\Scripts\Activate.ps1
   # Prompt should show (.venv)
   ```

2. Reinstall in development mode:
   ```powershell
   pip install -e .
   ```

---

## Verification Checklist

Use this checklist to verify your setup:

- [ ] `python --version` shows Python 3.10+
- [ ] `git --version` shows Git installed
- [ ] `python -m modumb.cli devices` lists audio devices
- [ ] `python -m modumb.cli test` plays tone and shows input levels
- [ ] Server starts without errors
- [ ] Modem tones are audible during transmission
- [ ] Clone completes successfully
- [ ] Cloned files match source repository

---

## Environment Variables Reference

| Variable | Description | Default |
|----------|-------------|---------|
| `MODEM_INPUT_DEVICE` | Audio input device index | System default |
| `MODEM_OUTPUT_DEVICE` | Audio output device index | System default |
| `MODEM_LOOPBACK` | Enable software loopback (bypass audio) | 0 |
| `MODEM_DEBUG` | Enable debug output | 0 |

---

## See Also

- [README.md](../README.md) - Main documentation
- [WSL2_SETUP.md](WSL2_SETUP.md) - WSL2 audio setup guide
- [DEBUGGING_JOURNEY.md](../DEBUGGING_JOURNEY.md) - Technical debugging notes
