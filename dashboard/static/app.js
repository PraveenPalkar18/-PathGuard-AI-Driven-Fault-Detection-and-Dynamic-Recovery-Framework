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

function updateDOM(data) {
    // Top Bar
    document.getElementById('timestamp-val').textContent = data.timestamp;

    // AI Status & Explainable AI
    const aiEl = document.getElementById('ai-status-val');
    aiEl.textContent = data.ai_status;
    aiEl.className = 'kpi-value status-' + data.ai_status;
    
    document.getElementById('confidence-val').textContent = data.confidence + '%';
    document.getElementById('explanation-val').textContent = data.explanation;
    
    // Health Score
    const healthEl = document.getElementById('health-val');
    healthEl.textContent = data.health_score + '/100';
    if (data.health_score >= 80) healthEl.style.color = 'var(--status-normal)';
    else if (data.health_score >= 50) healthEl.style.color = 'var(--status-warning)';
    else healthEl.style.color = 'var(--status-fault)';

    // KPIs
    document.getElementById('loss-val').textContent = data.packet_loss_pct;
    document.getElementById('latency-val').textContent = data.rtt_avg_ms;
    
    const recEl = document.getElementById('recovery-val');
    recEl.textContent = data.recovery_status;
    if (data.ai_status === 'CRITICAL' || data.ai_status === 'FAULT') {
        recEl.style.color = 'var(--status-fault)';
    } else if (data.ai_status === 'WARNING') {
        recEl.style.color = 'var(--status-warning)';
    } else {
        recEl.style.color = 'var(--text-secondary)';
    }

    // Topology Links & Nodes Heatmap
    const nodeStatus = { s1: 'up', s2: 'up', s3: 'up' };
    
    for (const [linkId, status] of Object.entries(data.links)) {
        const line = document.getElementById('link-' + linkId);
        if (line) {
            line.className.baseVal = `link ${status}`;
        }
        
        // Aggregate worst status for connected nodes (e.g., link s1-s2 affects s1 and s2)
        const switches = linkId.split('-');
        switches.forEach(sw => {
            if (nodeStatus[sw]) {
                if (status === 'down' || nodeStatus[sw] === 'down') {
                    nodeStatus[sw] = 'down';
                } else if (status === 'warning' || nodeStatus[sw] === 'warning') {
                    nodeStatus[sw] = 'warning';
                }
            }
        });
    }

    // Apply status to nodes
    for (const [sw, status] of Object.entries(nodeStatus)) {
        const nodeEl = document.getElementById('node-' + sw);
        if (nodeEl) {
            nodeEl.setAttribute('class', `node switch ${status}`);
        }
    }

    // Charts
    if (data.chart_data && data.chart_data.labels.length > 0) {
        latencyChart.data.labels = data.chart_data.labels;
        latencyChart.data.datasets[0].data = data.chart_data.latency;
        latencyChart.update();

        lossChart.data.labels = data.chart_data.labels;
        lossChart.data.datasets[0].data = data.chart_data.loss;
        
        // Dynamic y-axis for loss (if loss is 0, cap at 10 to make it look clean)
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
            let color = p.score > 80 ? 'var(--status-normal)' : (p.score > 40 ? 'var(--status-warning)' : 'var(--status-fault)');
            li.innerHTML = `<span><strong>${p.path}</strong> <span class="subtext">(${p.route})</span></span> <span style="color: ${color}">${p.score}/100</span>`;
            li.style.borderLeftColor = color;
            pathList.appendChild(li);
        });
    }

    // Timeline
    const timeline = document.getElementById('timeline-container');
    if (data.timeline && data.timeline.length > 0) {
        timeline.innerHTML = '';
        // Reverse so newest is on top
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
        // Could show a disconnected state here
    }
}

// Initialization
document.addEventListener('DOMContentLoaded', () => {
    initCharts();
    fetchStatus();
    // Refresh every 2 seconds
    setInterval(fetchStatus, 2000);
});
