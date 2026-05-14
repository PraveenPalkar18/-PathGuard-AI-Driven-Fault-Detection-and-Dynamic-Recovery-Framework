#!/bin/bash
# ==============================================================================
# PathGuard: POX Controller Launcher
# ==============================================================================

# Automatically release conflicting ports to prevent Address Already In Use errors
echo "➔ Checking and releasing ports 8080 and 6633..."
fuser -k 8080/tcp > /dev/null 2>&1
fuser -k 6633/tcp > /dev/null 2>&1
pkill -f pox.py > /dev/null 2>&1

# Locate pox folder (typically assumed at ~/pox)
POX_DIR=~/pox

if [ ! -d "$POX_DIR" ]; then
    echo "⚠ POX controller directory not found at ~/pox. Searching in default locations..."
    if [ -d "/home/wifi/pox" ]; then
        POX_DIR="/home/wifi/pox"
    else
        echo "❌ POX folder not found. Please ensure it is installed."
        exit 1
    fi
fi

echo "➔ Deploying PathGuard Controller module to POX..."
# Copy the controller application to POX's external component directory
cp "/home/wifi/pathgaurd/controller/pathguard_controller.py" "$POX_DIR/ext/pathguard_controller.py"
if [ $? -ne 0 ]; then
    echo "❌ Failed to copy controller application to POX extensions directory."
    exit 1
fi

echo "➔ Starting Custom PathGuard POX SDN Controller..."
echo "  Enabling REST Server & Deterministic Flow Rules Module..."

cd "$POX_DIR"
# Run Web Server on port 8080, Color logging, and custom PathGuard Controller
./pox.py web --port=8080 samples.pretty_log pathguard_controller
