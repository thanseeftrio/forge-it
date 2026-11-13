# Setup Guide

Instructions for installing dependencies, configuring environment variables, and running FORGE IT locally.

---

## 1. Prerequisites

- **Python**: 3.10 or higher
- **Node.js**: 18.0 or higher
- **Git**

---

## 2. EDA Toolchains Setup

### Icarus Verilog (`iverilog`)

Icarus Verilog is the primary simulation and linting tool used by the IC Integration module.

#### Windows
- **Option A (Official Installer)**:
  1. Download the setup executable from [bleyer.org/icarus](https://bleyer.org/icarus/).
  2. Run the installer and check **"Add executable folder to PATH"**.
  3. Verify in PowerShell: `iverilog -v`.
- **Option B (Package Manager)**:
  ```powershell
  winget install Icarus.Verilog
  # or with Scoop:
  scoop install iverilog
  ```

#### Linux (Ubuntu / Debian)
```bash
sudo apt update
sudo apt install -y iverilog
iverilog -v
```

#### macOS (Homebrew)
```bash
brew install icarus-verilog
iverilog -v
```

---

## 3. Python Environment & Dependencies

From the repository root:

```bash
# Set up Python virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
.\venv\Scripts\Activate.ps1
# Linux/macOS:
source venv/bin/activate

# Install backend dependencies
pip install -r backend/requirements.txt
```

---

## 4. Verifying the IC / RTL Integration Module

You can verify that tool detection, inspection, and linting work properly on your system:

```bash
python -c "from integrations.ic import ICAdapter; adapter = ICAdapter(); print(adapter.get_status())"
```

Expected output:
```json
{
  "primary_tool": "iverilog",
  "tools": {
    "iverilog": {
      "tool": "iverilog",
      "available": true,
      "version": "13.0 (stable) (v13_0)",
      "capabilities": ["lint", "syntax_check", "compile", "simulate"]
    }
  }
}
```
