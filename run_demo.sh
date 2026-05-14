#!/bin/bash
# ==============================================================================
# PathGuard: One-Click Demo Script
# Runs the POX controller, the web dashboard, and the AI detection demo.
# ==============================================================================

# Define cleanup function to run when script exits
cleanup() {
    echo -e "\n[!] Demo interrupted or finished. Cleaning up..."
    if [ -n "$POX_PID" ]; then
        kill $POX_PID 2>/dev/null
    fi
    if [ -n "$DASH_PID" ]; then
        kill $DASH_PID 2>/dev/null
    fi
    # Ensure mininet is cleaned up
    sudo mn -c > /dev/null 2>&1
    echo "Cleanup complete. Goodbye!"
    exit 0
}

# Trap Ctrl+C (SIGINT) to ensure cleanup runs
trap cleanup SIGINT

echo "========================================================="
echo "  PathGuard: Automated Full-System Demo"
echo "========================================================="

# 1. Start POX Controller
echo "➔ [1/4] Starting POX SDN Controller in the background..."
cd ~/pox
./pox.py openflow.discovery openflow.spanning_tree --no-flood --hold-down forwarding.l2_learning > /dev/null 2>&1 &
POX_PID=$!
cd ~/pathgaurd
sleep 2

# 2. Start Dashboard
echo "➔ [2/4] Starting Web Dashboard in the background..."
python3 dashboard/app.py > /dev/null 2>&1 &
DASH_PID=$!
sleep 2

echo -e "\n========================================================="
echo "  🌐 Dashboard is live! Open your browser to:"
echo "     👉  http://localhost:5000"
echo "========================================================="
echo -e "\nStarting network topology in 5 seconds..."
sleep 5

# 3. Run AI Test Script
echo "➔ [3/4] Launching Network Topology & AI Demo..."
echo "       (This will automatically test normal traffic and all link failures)"
echo ""
sudo python3 test_ai_detection.py

# 4. Finish
echo "➔ [4/4] Demo sequence completed."
cleanup
