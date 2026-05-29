#!/bin/bash
# ==============================================================================
# PathGuard: POX Controller Launcher
# ==============================================================================

# Automatically release conflicting ports to prevent Address Already In Use errors
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PATHGAURD_ROOT="$PROJECT_ROOT"

echo "➔ Checking and releasing ports 8080 and 6633..."
fuser -k 8080/tcp > /dev/null 2>&1
fuser -k 6633/tcp > /dev/null 2>&1
pkill -f pox.py > /dev/null 2>&1

# Locate POX directory dynamically
POX_DIR=""
if [ -n "$POX_HOME" ] && [ -d "$POX_HOME" ]; then
    POX_DIR="$POX_HOME"
fi

if [ -z "$POX_DIR" ]; then
    echo "⚠ POX controller directory not configured. Searching common locations..."
    for candidate in "$HOME/pox" "/usr/local/pox" "/opt/pox" "$PROJECT_ROOT/../pox"; do
        if [ -d "$candidate" ]; then
            POX_DIR="$candidate"
            break
        fi
    done
fi

if [ -z "$POX_DIR" ] || [ ! -d "$POX_DIR" ]; then
    echo "⚠ POX folder not found in configured locations. Trying PATH search..."
    POX_PATH="$(command -v pox.py 2>/dev/null || true)"
    if [ -n "$POX_PATH" ]; then
        POX_DIR="$(cd "$(dirname "$POX_PATH")" && pwd)"
        echo "➔ Found pox.py on PATH at $POX_PATH"
    fi
fi

if [ -z "$POX_DIR" ] || [ ! -d "$POX_DIR" ]; then
    echo "❌ POX folder not found. Please set POX_HOME or install POX in a common location."
    echo "   Checked: $HOME/pox, /usr/local/pox, /opt/pox, $PROJECT_ROOT/../pox, $PROJECT_ROOT/../../pox"
    exit 1
fi

if [ ! -f "$POX_DIR/pox.py" ]; then
    echo "❌ POX installation found at $POX_DIR but pox.py is missing."
    exit 1
fi

echo "➔ Deploying PathGuard Controller module to POX..."
# Copy the controller application to POX's external component directory
cp "$PROJECT_ROOT/controller/pathguard_controller.py" "$POX_DIR/ext/pathguard_controller.py"
if [ $? -ne 0 ]; then
    echo "❌ Failed to copy controller application to POX extensions directory."
    exit 1
fi

echo "➔ Starting Custom PathGuard POX SDN Controller..."
echo "  Enabling REST Server & Deterministic Flow Rules Module..."

cd "$POX_DIR"
# Run Web Server on port 8080, Color logging, and custom PathGuard Controller
./pox.py web --port=8080 samples.pretty_log pathguard_controller
