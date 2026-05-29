#!/bin/bash
# ==============================================================================
# PathGuard: One-Click Demo Script
# ==============================================================================

# Ensure the script is running as root
if [ "$EUID" -ne 0 ]; then
    echo "Error: This script must be run with root privileges. Please use: sudo ./run_demo.sh"
    exit 1
fi

# On Ctrl-C or exit, kill all background processes cleanly
trap "kill 0" EXIT

echo "========================================================="
echo "  PathGuard: Automated Full-System Demo"
echo "========================================================="

# Automatically release Dashboard port 5000 to prevent conflicts
fuser -k 5000/tcp > /dev/null 2>&1

# 1. Start POX Controller
echo "➔ [1/3] Starting POX SDN Controller in the background..."
mkdir -p results
./controller/run_pox.sh > results/pox.log 2>&1 &

# Wait until POX is actually ready by polling port 6633
echo "Waiting for POX to bind port 6633..."
until nc -z 127.0.0.1 6633 2>/dev/null; do
    sleep 0.5
done
echo "POX ready."

# 2. Start Dashboard
echo "➔ [2/3] Starting Web Dashboard in the background..."
python3 dashboard/app.py > results/dashboard.log 2>&1 &

# Wait until Flask binds port 5000 (model load can take 10-15s)
echo "Waiting for Dashboard to bind port 5000..."
for i in $(seq 1 40); do
    if nc -z 127.0.0.1 5000 2>/dev/null; then
        echo "Dashboard ready (http://localhost:5000)."
        break
    fi
    sleep 0.5
done
if ! nc -z 127.0.0.1 5000 2>/dev/null; then
    echo "Warning: Dashboard may not have started. Check results/dashboard.log"
fi
echo ""

# 3. Start Mininet
echo "➔ [3/3] Launching Network Topology in headless monitoring mode..."
echo "---------------------------------------------------------"
echo "  Press [Ctrl+C] at any time to stop the demo and cleanup"
echo "---------------------------------------------------------"
sudo python3 topology/topology.py --monitor
