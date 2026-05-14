#!/bin/bash
# ==============================================================================
# PathGuard: POX Controller Launcher
# ==============================================================================

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

echo "➔ Starting PathGuard POX SDN Controller..."
echo "  Using modules: discovery, spanning_tree, l2_learning"

cd "$POX_DIR"
./pox.py openflow.discovery openflow.spanning_tree --no-flood --hold-down forwarding.l2_learning
