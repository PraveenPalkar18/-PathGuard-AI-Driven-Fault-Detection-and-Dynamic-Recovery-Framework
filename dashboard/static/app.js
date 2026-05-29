// Chart Configurations
const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 0 },
    scales: {
        x: { 
            grid: { color: 'rgba(255, 255, 255, 0.05)' },
            ticks: { color: '#94a3b8', maxTicksLimit: 5 }
        },
        y: { 
            grid: { color: 'rgba(255, 255, 255, 0.05)' },
            ticks: { color: '#94a3b8' },
            beginAtZero: true
        }
    },
    plugins: {
        legend: { display: false }
    }
};

let latencyChart, lossChart;
let globalTopo = null;

function initCharts() {
    const ctxLatency = document.getElementById('latencyChart').getContext('2d');
    latencyChart = new Chart(ctxLatency, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'Latency (ms)',
                data: [],
                borderColor: '#38bdf8',
                backgroundColor: 'rgba(56, 189, 248, 0.1)',
                borderWidth: 2,
                fill: true,
                tension: 0.4,
                pointRadius: 0
            }]
        },
        options: chartOptions
    });

    const ctxLoss = document.getElementById('lossChart').getContext('2d');
    lossChart = new Chart(ctxLoss, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'Packet Loss (%)',
                data: [],
                borderColor: '#ef4444',
                backgroundColor: 'rgba(239, 68, 68, 0.1)',
                borderWidth: 2,
                fill: true,
                tension: 0.1,
                pointRadius: 0
            }]
        },
        options: chartOptions
    });
}

// D3 Topology Rendering
let simulation;
let svgNodes;
let svgLinks;

async function fetchTopology() {
    try {
        const response = await fetch('/api/topology');
        if (!response.ok) throw new Error('Failed to fetch topology');
        const topo = await response.json();
        globalTopo = topo;
        
        if (!topo.nodes || topo.nodes.length === 0) {
            console.warn("Empty topology received, rendering default");
            showTopologyPlaceholder();
            return;
        }
        
        renderTopology(topo);
    } catch (e) {
        console.error("Error fetching topology:", e);
        showTopologyPlaceholder();
    }
}

function showTopologyPlaceholder() {
    // Show a message if topology can't be rendered
    const svg = d3.select("#topology-svg");
    svg.selectAll("*").remove();
    
    svg.append("text")
        .attr("x", 400)
        .attr("y", 300)
        .attr("text-anchor", "middle")
        .attr("fill", "#94a3b8")
        .attr("font-size", "18px")
        .text("Topology loading... (will update in 5s)");
}

// D3 Node Dragging Event Handlers
function drag(sim) {
    function dragstarted(event, d) {
        if (!event.active) sim.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
    }
    
    function dragged(event, d) {
        d.fx = event.x;
        d.fy = event.y;
    }
    
    function dragended(event, d) {
        if (!event.active) sim.alphaTarget(0);
        d.fx = null;
        d.fy = null;
    }
    
    return d3.drag()
        .on("start", dragstarted)
        .on("drag", dragged)
        .on("end", dragended);
}

function renderTopology(topo) {
    if (!topo.nodes || topo.nodes.length === 0) {
        showTopologyPlaceholder();
        return;
    }
    
    const width = 800;
    const height = 800; // Increased height to match expanded HTML viewBox
    
    // Setup D3 simulation centered on the expanded coordinate space
    simulation = d3.forceSimulation(topo.nodes)
        .force("link", d3.forceLink(topo.links || []).id(d => d.id).distance(95))
        .force("charge", d3.forceManyBody().strength(-350))
        .force("center", d3.forceCenter(width / 2, height / 2))
        .force("collide", d3.forceCollide().radius(32));
        
    const svg = d3.select("#topology-svg");
    svg.selectAll("*").remove();
    
    // Draw links
    svgLinks = svg.append("g").attr("id", "links-layer")
        .selectAll("line")
        .data(topo.links || [])
        .join("line")
        .attr("id", d => "link-" + d.id)
        .attr("class", "link up")
        .attr("stroke-width", 2)
        .attr("stroke", "#475569");
        
    // Draw nodes with drag interactivity enabled
    svgNodes = svg.append("g").attr("id", "nodes-layer")
        .selectAll("g")
        .data(topo.nodes)
        .join("g")
        .attr("id", d => "node-" + d.id)
        .attr("class", d => `node ${d.type} ${d.layer}`)
        .call(drag(simulation)); // Drag nodes dynamically with cursor!
        
    svgNodes.append("circle")
        .attr("r", d => d.type === "switch" ? 22 : 14) // Increased node sizing to stand out prominently
        .attr("class", d => d.type === "switch" ? "switch-circle" : "host-circle");
        
    svgNodes.append("text")
        .attr("y", 0) // Centered inside the circles (coordinates with dominant-baseline in CSS)
        .attr("text-anchor", "middle")
        .text(d => d.label);
        
    simulation.on("tick", () => {
        // Enforce strict canvas boundaries with a comfortable 50px buffer to prevent any edge clipping
        const margin = 50;
        topo.nodes.forEach(d => {
            d.x = Math.max(margin, Math.min(width - margin, d.x));
            d.y = Math.max(margin, Math.min(height - margin, d.y));
        });

        svgLinks
            .attr("x1", d => d.source.x || 0)
            .attr("y1", d => d.source.y || 0)
            .attr("x2", d => d.target.x || 0)
            .attr("y2", d => d.target.y || 0);
            
        svgNodes
            .attr("transform", d => `translate(${d.x || 0},${d.y || 0})`);
    });
}

function updateDOM(data) {
    // Top Bar
    document.getElementById('timestamp-val').textContent = data.timestamp;

    // AI Status & Explainable AI
    const aiEl = document.getElementById('ai-status-val');
    aiEl.textContent = data.ai_status;
    aiEl.className = 'kpi-value status-' + data.ai_status;
    
    document.getElementById('confidence-val').textContent = data.confidence + '%';
    document.getElementById('explanation-val').textContent = data.explanation;
    
    // Health Score — aligned with severity bands (85/60)
    const healthEl = document.getElementById('health-val');
    const healthGauge = document.querySelector('.health-gauge');
    healthEl.textContent = data.health_score + '/100';
    healthGauge.classList.remove('warning', 'critical');
    if (data.health_score >= 85) {
        healthEl.style.color = 'var(--status-normal)';
    } else if (data.health_score >= 60) {
        healthEl.style.color = 'var(--status-warning)';
        healthGauge.classList.add('warning');
    } else {
        healthEl.style.color = 'var(--status-fault)';
        healthGauge.classList.add('critical');
    }

    // KPIs
    document.getElementById('loss-val').textContent = data.packet_loss_pct;
    document.getElementById('latency-val').textContent = data.rtt_avg_ms;
    
    const recEl = document.getElementById('recovery-val');
    recEl.textContent = data.recovery_status;
    recEl.className = 'kpi-value subtext';
    if (data.recovery_status.startsWith('RECOVERING')) {
        recEl.classList.add('status-RECOVERING');
    } else if (data.recovery_status.startsWith('RECOVERED')) {
        recEl.classList.add('status-RECOVERED');
    } else if (data.ai_status === 'CRITICAL') {
        recEl.style.color = 'var(--status-fault)';
    } else if (data.ai_status === 'WARNING') {
        recEl.style.color = 'var(--status-warning)';
    } else {
        recEl.style.color = 'var(--text-secondary)';
    }

    // Topology Links & Nodes Heatmap
    const nodeStatus = {};
    if (globalTopo) {
        globalTopo.nodes.forEach(n => nodeStatus[n.id] = 'up');
    }

    // Mark recovery path links blue
    const recoveryLinks = new Set(data.recovery_path_links || []);
    
    for (const [linkId, status] of Object.entries(data.links || {})) {
        let linkStatus = status;
        if (status === 'recovery' || (recoveryLinks.has(linkId) && status === 'up')) {
            linkStatus = 'recovery';
        }
        if ((data.ai_status === 'NORMAL' || data.ai_status === 'CRITICAL') && linkStatus === 'warning') {
            linkStatus = 'up';
        }

        // Try both orderings of the link ID to handle normalization differences
        let line = document.getElementById('link-' + linkId);
        if (!line) {
            const parts = linkId.split('-');
            if (parts.length === 2) {
                line = document.getElementById('link-' + parts[1] + '-' + parts[0]);
            }
        }
        if (!line) {
            continue;
        }
        line.setAttribute('class', `link ${linkStatus}`);
        
        const switches = linkId.split('-');
        switches.forEach(sw => {
            if (nodeStatus[sw] !== undefined) {
                if (linkStatus === 'down' || nodeStatus[sw] === 'down') {
                    nodeStatus[sw] = 'down';
                } else if (linkStatus === 'warning' || nodeStatus[sw] === 'warning') {
                    nodeStatus[sw] = 'warning';
                } else if (linkStatus === 'recovery' && nodeStatus[sw] === 'up') {
                    nodeStatus[sw] = 'recovery';
                }
            }
        });
    }

    for (const [sw, status] of Object.entries(nodeStatus)) {
        const nodeEl = document.getElementById('node-' + sw);
        if (nodeEl) {
            const baseClass = nodeEl.getAttribute('class').replace(/\b(up|warning|down|recovery)\b/g, '').trim();
            nodeEl.setAttribute('class', `${baseClass} ${status}`.trim());
        }
    }

    // Fault Analysis Panel
    updateFaultAnalysis(data);

    // Active Network Issues Panel
    updateActiveIssues(data);

    // Charts
    if (data.chart_data && data.chart_data.labels.length > 0) {
        latencyChart.data.labels = data.chart_data.labels;
        latencyChart.data.datasets[0].data = data.chart_data.latency;
        latencyChart.update();

        lossChart.data.labels = data.chart_data.labels;
        lossChart.data.datasets[0].data = data.chart_data.loss;
        
        // Dynamic y-axis for loss
        let maxLoss = Math.max(...data.chart_data.loss);
        lossChart.options.scales.y.max = maxLoss > 10 ? 100 : 10;
        lossChart.update();
    }
    
    // Path Rankings
    const pathList = document.getElementById('path-list');
    if (data.path_rankings && data.path_rankings.length > 0) {
        pathList.innerHTML = '';
        data.path_rankings.forEach(p => {
            const li = document.createElement('li');
            
            let statusColor = 'var(--status-normal)';
            if (p.status === 'FAILED') statusColor = 'var(--status-fault)';
            else if (p.status === 'ACTIVE RECOVERY PATH') statusColor = '#3b82f6';
            else if (p.status === 'STANDBY') statusColor = 'var(--text-secondary)';
            
            li.style.borderLeft = `4px solid ${statusColor}`;
            li.style.background = 'rgba(255, 255, 255, 0.02)';
            li.style.padding = '0.8rem 1rem';
            li.style.borderRadius = '6px';
            li.style.marginBottom = '0.75rem';
            li.style.display = 'flex';
            li.style.flexDirection = 'column';
            li.style.gap = '0.25rem';
            li.style.justifyContent = 'flex-start';
            
            li.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
                    <strong>${p.path}</strong>
                    <span style="font-weight: bold; color: ${p.score > 80 ? 'var(--status-normal)' : (p.score > 40 ? 'var(--status-warning)' : 'var(--status-fault)')}">${p.score}/100</span>
                </div>
                <div style="font-size: 0.8rem; color: var(--text-secondary);">${p.route}</div>
                <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.75rem; margin-top: 0.25rem; width: 100%;">
                    <span style="background: ${statusColor}15; color: ${statusColor}; border: 1px solid ${statusColor}30; padding: 2px 6px; border-radius: 4px; font-weight: bold; font-size: 0.7rem;">${p.status}</span>
                    <span style="font-style: italic; color: var(--text-secondary); text-align: right;">${p.reason}</span>
                </div>
            `;
            pathList.appendChild(li);
        });
    }

    // Live Recovery Debug
    const debugPanel = document.getElementById('debug-panel-content');
    if (data.debug_info && debugPanel) {
        let openflowColor = 'var(--status-normal)';
        if (data.debug_info.openflow_status.includes('UPDATING')) openflowColor = 'var(--status-warning)';
        else if (data.debug_info.openflow_status.includes('STABLE')) openflowColor = 'var(--text-secondary)';
        
        let actionColor = 'var(--text-primary)';
        if (data.debug_info.controller_action.includes('Rerouting')) actionColor = '#3b82f6';
        
        debugPanel.innerHTML = `
            <div style="display: flex; flex-direction: column; gap: 0.75rem;">
                <div style="display: flex; justify-content: space-between; font-size: 0.85rem; border-bottom: 1px solid rgba(255,255,255,0.03); padding-bottom: 0.5rem;">
                    <span style="color: var(--text-secondary);">Failed Link:</span>
                    <strong class="${data.debug_info.failed_link !== 'None' ? 'status-CRITICAL' : ''}">${data.debug_info.failed_link}</strong>
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 0.85rem; border-bottom: 1px solid rgba(255,255,255,0.03); padding-bottom: 0.5rem;">
                    <span style="color: var(--text-secondary);">Active Recovery:</span>
                    <strong style="color: #3b82f6;">${data.debug_info.active_recovery_path}</strong>
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 0.85rem; border-bottom: 1px solid rgba(255,255,255,0.03); padding-bottom: 0.5rem;">
                    <span style="color: var(--text-secondary);">Trigger Reason:</span>
                    <span style="max-width: 60%; text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 0.8rem;" title="${data.debug_info.recovery_trigger_reason}">${data.debug_info.recovery_trigger_reason}</span>
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 0.85rem; border-bottom: 1px solid rgba(255,255,255,0.03); padding-bottom: 0.5rem;">
                    <span style="color: var(--text-secondary);">Best Path Score:</span>
                    <strong>${data.debug_info.selected_path_score}</strong>
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 0.85rem; border-bottom: 1px solid rgba(255,255,255,0.03); padding-bottom: 0.5rem;">
                    <span style="color: var(--text-secondary);">Controller Action:</span>
                    <span style="color: ${actionColor}; font-weight: 500;">${data.debug_info.controller_action}</span>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.85rem; border-bottom: 1px solid rgba(255,255,255,0.03); padding-bottom: 0.5rem;">
                    <span style="color: var(--text-secondary);">OpenFlow Status:</span>
                    <span class="badge" style="background: ${openflowColor}15; color: ${openflowColor}; border: 1px solid ${openflowColor}30; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.75rem;">${data.debug_info.openflow_status}</span>
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 0.85rem; border-bottom: 1px solid rgba(255,255,255,0.03); padding-bottom: 0.5rem;">
                    <span style="color: var(--text-secondary);">Telemetry Time:</span>
                    <strong>${data.debug_info.telemetry_ts || 'N/A'}</strong>
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 0.85rem; border-bottom: 1px solid rgba(255,255,255,0.03); padding-bottom: 0.5rem;">
                    <span style="color: var(--text-secondary);">Dashboard Render:</span>
                    <strong>${new Date().toLocaleTimeString()}</strong>
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 0.85rem;">
                    <span style="color: var(--text-secondary);">Last Recovery / Reroute:</span>
                    <strong>${data.debug_info.last_recovery_ts || 'None'}</strong>
                </div>
            </div>
        `;
    }

    // Timeline
    const timeline = document.getElementById('timeline-container');
    if (data.timeline && data.timeline.length > 0) {
        timeline.innerHTML = '';
        [...data.timeline].reverse().forEach(event => {
            const div = document.createElement('div');
            let type = 'normal';
            if (event.includes('WARNING')) type = 'warning';
            if (event.includes('CRITICAL') || event.includes('FAULT')) type = 'critical';
            
            div.className = `timeline-event ${type}`;
            div.textContent = event;
            timeline.appendChild(div);
        });
    }
}

async function fetchStatus() {
    try {
        const response = await fetch('/api/status');
        if (!response.ok) throw new Error('Network response was not ok');
        const data = await response.json();
        updateDOM(data);
    } catch (error) {
        console.error('Error fetching status:', error);
    }
}

function updateFaultAnalysis(data) {
    const panel = document.getElementById('fault-analysis-panel');
    const fa = data.fault_analysis || {};
    const items = [];

    if (data.ai_status === 'NORMAL' && !(fa.failed_links || []).length && !(fa.degraded_links || []).length) {
        panel.innerHTML = `<div class="fault-item normal">
            <div class="fault-title">Network Healthy</div>
            <div class="fault-detail">${data.explanation}</div>
        </div>`;
        return;
    }

    (fa.failed_links || []).forEach(link => {
        items.push(`<div class="fault-item critical">
            <div class="fault-title">Link ${link.link} DOWN</div>
            <div class="fault-detail">${link.message} (${link.layer} layer)</div>
        </div>`);
    });

    (fa.degraded_links || []).forEach(link => {
        items.push(`<div class="fault-item warning">
            <div class="fault-title">${link.link} Degraded</div>
            <div class="fault-detail">${link.message}</div>
        </div>`);
    });

    (fa.root_causes || []).forEach(cause => {
        if (!items.some(html => html.includes(cause))) {
            items.push(`<div class="fault-item ${data.ai_status === 'CRITICAL' ? 'critical' : 'warning'}">
                <div class="fault-title">Root Cause</div>
                <div class="fault-detail">${cause}</div>
            </div>`);
        }
    });

    if ((data.recovery_status || '').startsWith('RECOVERING')) {
        items.push(`<div class="fault-item recovery">
            <div class="fault-title">Recovery Active</div>
            <div class="fault-detail">Dynamic reroute in progress — alternate paths being installed</div>
        </div>`);
    }

    panel.innerHTML = items.length ? items.join('') : `<div class="fault-item normal"><div class="fault-detail">${data.explanation}</div></div>`;
}

function updateActiveIssues(data) {
    const list = document.getElementById('active-issues-list');
    const fa = data.fault_analysis || {};
    const issues = fa.active_issues || [];
    const entries = [];

    issues.forEach(issue => {
        const cls = issue.severity === 'down' ? 'issue-down' : 'issue-warning';
        entries.push(`<li class="${cls}"><span>${issue.message}</span></li>`);
    });

    (fa.degraded_links || []).forEach(link => {
        if (!entries.some(e => e.includes(link.link))) {
            entries.push(`<li class="issue-warning"><span>${link.message}</span></li>`);
        }
    });

    (fa.failed_links || []).forEach(link => {
        entries.push(`<li class="issue-down"><span>${link.message}</span></li>`);
    });

    (fa.unstable_switches || []).slice(0, 3).forEach(sw => {
        entries.push(`<li class="issue-warning"><span>Congestion zone near ${sw.switch}</span></li>`);
    });

    if (data.recovery_path_links && data.recovery_path_links.length) {
        entries.push(`<li class="issue-recovery"><span>Recovery path: ${data.recovery_path_links.join(', ')}</span></li>`);
    }

    if (entries.length === 0) {
        list.innerHTML = '<li class="issue-normal subtext">No active issues — all links healthy</li>';
    } else {
        list.innerHTML = entries.join('');
    }
}

// Initialization
document.addEventListener("DOMContentLoaded", () => {
    initCharts();

    fetchTopology().finally(() => {
        // BUG 10 fix: Retry topology fetch if first attempt failed
        if (!globalTopo || !globalTopo.nodes || globalTopo.nodes.length === 0) {
            setTimeout(() => {
                fetchTopology().finally(() => {
                    fetchStatus();
                });
            }, 3000);
        }
        fetchStatus();
        setInterval(fetchStatus, 300);
    });
});

