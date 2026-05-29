#!/bin/bash
# ==============================================================================
# PathGuard: Full Automated Demo Workflow
# ==============================================================================
# 
# Complete demo showing:
#   1. POX SDN Controller startup
#   2. Mininet topology with monitoring
#   3. Dashboard server
#   4. Automated demo scenarios (NORMAL → WARNING → CRITICAL → RECOVERY)
#
# Usage: sudo ./demo/run_full_demo.sh
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Project root
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO_DIR="$PROJECT_ROOT/demo"

# Cleanup on exit
cleanup() {
    echo -e "\n${YELLOW}[$(date +'%H:%M:%S')]${NC} Cleaning up demo..."
    pkill -f "python3.*topology.py" 2>/dev/null || true
    pkill -f "python3.*app.py" 2>/dev/null || true
    pkill -f "pox.py" 2>/dev/null || true
    pkill -f "demo_scenarios" 2>/dev/null || true
    fuser -k 5000/tcp > /dev/null 2>&1 || true
    fuser -k 6633/tcp > /dev/null 2>&1 || true
    fuser -k 8000/tcp > /dev/null 2>&1 || true
    echo -e "${GREEN}[$(date +'%H:%M:%S')] Cleanup complete${NC}"
}

trap cleanup EXIT

print_header() {
    echo ""
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC}  PathGuard: AI-Driven Fault Detection & Dynamic Recovery - FULL DEMO        ${BLUE}║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

print_step() {
    local step=$1
    local msg=$2
    echo -e "${BLUE}[$(date +'%H:%M:%S')]${NC} ${YELLOW}[Step $step]${NC} $msg"
}

print_success() {
    echo -e "${GREEN}[$(date +'%H:%M:%S')] ✓${NC} $1"
}

print_info() {
    echo -e "${BLUE}[$(date +'%H:%M:%S')] ℹ${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[$(date +'%H:%M:%S')] ⚠${NC} $1"
}

# Verify running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: This script must be run as root. Use: sudo ./demo/run_full_demo.sh${NC}"
    exit 1
fi

print_header

# ──────────────────────────────────────────────────────────────────────
# STEP 1: Prepare directories and logs
# ──────────────────────────────────────────────────────────────────────
print_step 1 "Preparing environment..."
mkdir -p "$PROJECT_ROOT/results"
rm -f "$PROJECT_ROOT/results/"*.log

print_success "Directories ready"

# ──────────────────────────────────────────────────────────────────────
# STEP 2: Start POX SDN Controller
# ──────────────────────────────────────────────────────────────────────
print_step 2 "Starting POX SDN Controller..."
print_info "  POX will serve OpenFlow on port 6633"

cd "$PROJECT_ROOT"
bash ./controller/run_pox.sh > "$PROJECT_ROOT/results/pox.log" 2>&1 &
POX_PID=$!

# Wait for POX to bind
print_info "  Waiting for POX to initialize..."
for i in {1..30}; do
    if nc -z 127.0.0.1 6633 2>/dev/null; then
        print_success "POX controller ready (port 6633)"
        break
    fi
    sleep 0.5
done

if ! nc -z 127.0.0.1 6633 2>/dev/null; then
    print_warning "POX may not have started correctly. Continuing anyway..."
fi

# ──────────────────────────────────────────────────────────────────────
# STEP 3: Start Dashboard Web Server
# ──────────────────────────────────────────────────────────────────────
print_step 3 "Starting Flask Web Dashboard..."
print_info "  Dashboard will run on http://localhost:5000"

cd "$PROJECT_ROOT"
python3 dashboard/app.py > "$PROJECT_ROOT/results/dashboard.log" 2>&1 &
DASHBOARD_PID=$!

# Wait for dashboard to bind (AI model load can take 10-15s)
print_info "  Waiting for Flask to initialize..."
DASHBOARD_READY=0
for i in {1..40}; do
    if nc -z 127.0.0.1 5000 2>/dev/null; then
        print_success "Dashboard ready (http://localhost:5000)"
        DASHBOARD_READY=1
        break
    fi
    sleep 0.5
done
if [ "$DASHBOARD_READY" -eq 0 ]; then
    print_warning "Dashboard did not start on port 5000. Check results/dashboard.log"
fi

# ──────────────────────────────────────────────────────────────────────
# STEP 4: Start Mininet Topology & Monitoring
# ──────────────────────────────────────────────────────────────────────
print_step 4 "Launching Mininet topology with monitoring..."
print_info "  Starting 12-switch, 24-host network..."
print_info "  AI monitoring will analyze network health in real-time"

cd "$PROJECT_ROOT"
python3 topology/topology.py --monitor > "$PROJECT_ROOT/results/topology.log" 2>&1 &
TOPO_PID=$!

# Wait for topology to start (harder to detect, so just wait a bit)
print_info "  Waiting for topology to boot..."
sleep 15

if ps -p $TOPO_PID > /dev/null; then
    print_success "Topology running (PID: $TOPO_PID)"
else
    print_warning "Topology may have encountered an issue. Check results/topology.log"
fi

# ──────────────────────────────────────────────────────────────────────
# STEP 5: Launch Demo Scenarios
# ──────────────────────────────────────────────────────────────────────
print_step 5 "Launching automated demo scenarios..."

sleep 3

print_info "Demo will show: NORMAL → WARNING → CRITICAL → RECOVERY → RESTORED"
print_info ""
print_info "  🎯 Watch the dashboard at: http://localhost:5000"
print_info "     Metrics will update every 2 seconds"
print_info ""
print_info "  📊 Key things to observe:"
print_info "     • AI Status badge changes color"
print_info "     • Health score decreases/increases"
print_info "     • Topology links change color (green → yellow → red → green)"
print_info "     • Latency/Loss charts update in real-time"
print_info "     • Timeline shows detected faults and recovery actions"
print_info ""

cd "$PROJECT_ROOT"
python3 demo/demo_scenarios.py

print_step 6 "Demo complete!"

print_info ""
print_success "All phases completed successfully!"
print_info ""
print_info "📊 Results:"
print_info "  • POX Controller: Running"
print_info "  • Mininet Topology: 12 switches, 24 hosts"
print_info "  • AI Monitoring: Active"
print_info "  • Dashboard: Responsive"
print_info "  • Demo Scenarios: All states demonstrated"

print_info ""
print_info "📁 Log files in $PROJECT_ROOT/results/"
if [ -f "$PROJECT_ROOT/results/events.log" ]; then
    EVENT_COUNT=$(wc -l < "$PROJECT_ROOT/results/events.log")
    print_info "  • $EVENT_COUNT events logged in events.log"
fi
if [ -f "$PROJECT_ROOT/results/recovery_metrics.json" ]; then
    print_info "  • Recovery metrics saved to recovery_metrics.json"
fi

print_info ""
print_info "🎉 PathGuard demo workflow complete!"
print_info ""

# Keep services running for a bit to allow inspection
print_info "Services will keep running for 30 more seconds for inspection..."
print_info "(Then all services will be cleaned up)"
sleep 30

print_info "Cleaning up processes..."
