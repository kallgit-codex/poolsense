"""
PoolSense — Leak Detection System
Main application: reads sensors, runs leak algorithm, serves web dashboard.
"""

import time
import json
import threading
import statistics
import os
from datetime import datetime, timedelta
from collections import deque

# Flask for web dashboard
from flask import Flask, jsonify, render_template_string

# Our sensor driver
from ms5837 import MS5837

# ─── Configuration ────────────────────────────────────────────────────
CONFIG = {
    "SAMPLE_INTERVAL_SEC": 1,        # Read sensor every 1 second
    "BASELINE_DURATION_MIN": 15,      # Stabilization period
    "TEST_DURATION_MIN": 120,         # Default 2-hour test
    "EVAP_RATE_MM_HR": 0.15,          # Default evap rate (summer, still air)
    "LEAK_THRESHOLD_MM_HR": 0.5,      # Below this = no leak
    "WEB_PORT": 8080,
    "DATA_DIR": "/home/pi/poolsense/data",
}

# ─── Data Storage ─────────────────────────────────────────────────────
class TestSession:
    """Stores all data for a single leak test."""
    
    def __init__(self):
        self.start_time = None
        self.readings = deque(maxlen=86400)  # Max 24hrs of 1s readings
        self.baseline_pressure = None
        self.baseline_temp = None
        self.status = "idle"  # idle, baseline, testing, complete
        self.result = None
    
    def add_reading(self, timestamp, pressure_mbar, temp_c, depth_mm):
        self.readings.append({
            "t": timestamp,
            "p": round(pressure_mbar, 3),
            "temp": round(temp_c, 2),
            "d": round(depth_mm, 2),
        })
    
    def get_readings_since(self, since_timestamp=0):
        """Return readings after a given timestamp (for live updates)."""
        return [r for r in self.readings if r["t"] > since_timestamp]
    
    def elapsed_minutes(self):
        if not self.start_time:
            return 0
        return (time.time() - self.start_time) / 60.0
    
    def calculate_leak_rate(self):
        """
        Core algorithm:
        1. Get depth at start vs end of test period
        2. Subtract expected evaporation
        3. Return leak rate in mm/hr
        """
        if len(self.readings) < 60:
            return None
        
        # Use rolling averages to smooth noise
        window = 30  # 30-second windows
        
        # First 30 readings average (start depth)
        start_depths = [r["d"] for r in list(self.readings)[:window]]
        start_depth = statistics.mean(start_depths)
        
        # Last 30 readings average (end depth)
        end_depths = [r["d"] for r in list(self.readings)[-window:]]
        end_depth = statistics.mean(end_depths)
        
        # Time elapsed in hours
        time_start = list(self.readings)[0]["t"]
        time_end = list(self.readings)[-1]["t"]
        elapsed_hrs = (time_end - time_start) / 3600.0
        
        if elapsed_hrs < 0.1:  # Need at least 6 minutes
            return None
        
        # Raw water loss (mm)
        raw_loss_mm = start_depth - end_depth  # Positive = water dropped
        
        # Expected evaporation
        # TODO: Pull from weather API for more accuracy
        avg_temp = statistics.mean([r["temp"] for r in self.readings])
        evap_rate = self._estimate_evaporation(avg_temp)
        expected_evap_mm = evap_rate * elapsed_hrs
        
        # Net leak rate
        net_loss_mm = raw_loss_mm - expected_evap_mm
        leak_rate_mm_hr = net_loss_mm / elapsed_hrs
        
        # Convert to gallons per day (for a typical 15,000 gal pool)
        # 1mm depth in a ~60m² pool ≈ 60 liters ≈ 15.85 gallons
        # This is approximate — would need pool surface area input for accuracy
        pool_surface_m2 = 60  # ~650 sq ft, typical residential pool
        liters_per_mm = pool_surface_m2  # 1mm * 1m² = 1 liter
        gal_per_day = (leak_rate_mm_hr * 24 * liters_per_mm) / 3.785
        
        return {
            "raw_loss_mm": round(raw_loss_mm, 2),
            "evap_correction_mm": round(expected_evap_mm, 2),
            "net_loss_mm": round(net_loss_mm, 2),
            "leak_rate_mm_hr": round(leak_rate_mm_hr, 3),
            "leak_rate_gal_day": round(gal_per_day, 1),
            "elapsed_hours": round(elapsed_hrs, 2),
            "avg_temp_c": round(avg_temp, 1),
            "verdict": self._get_verdict(leak_rate_mm_hr),
        }
    
    def _estimate_evaporation(self, water_temp_c):
        """
        Estimate evaporation rate in mm/hr based on water temperature.
        Simplified model — Phase 2 will use weather API for wind + humidity.
        """
        # Base rate increases with temperature
        # ~0.1 mm/hr at 20°C, ~0.2 mm/hr at 30°C, ~0.3 at 38°C
        base_rate = 0.05 + (water_temp_c - 15) * 0.008
        return max(0.05, base_rate)  # Floor at 0.05 mm/hr
    
    def _get_verdict(self, rate_mm_hr):
        if rate_mm_hr < 0.1:
            return {"status": "PASS", "message": "No leak detected. Water loss within normal evaporation range."}
        elif rate_mm_hr < CONFIG["LEAK_THRESHOLD_MM_HR"]:
            return {"status": "BORDERLINE", "message": f"Minor water loss detected ({rate_mm_hr:.2f} mm/hr). Could be slow leak or high evaporation. Recommend extended test."}
        elif rate_mm_hr < 2.0:
            return {"status": "LEAK", "message": f"Leak confirmed at {rate_mm_hr:.2f} mm/hr. Moderate leak — recommend dye testing to locate."}
        else:
            return {"status": "MAJOR LEAK", "message": f"Significant leak at {rate_mm_hr:.2f} mm/hr. Urgent repair needed."}


# ─── Sensor Reading Thread ────────────────────────────────────────────
session = TestSession()
sensor = None

def sensor_thread():
    """Background thread that continuously reads the pressure sensor."""
    global sensor, session
    
    try:
        sensor = MS5837()
    except Exception as e:
        print(f"[ERROR] Could not initialize MS5837: {e}")
        print("[INFO] Running in DEMO mode with simulated data")
        sensor = None
    
    # Calibrate: take first reading as atmospheric baseline
    baseline_offset = None
    
    while True:
        timestamp = time.time()
        
        if sensor and sensor.read():
            pressure = sensor.pressure()
            temp = sensor.temperature()
            
            # First reading calibration
            if baseline_offset is None:
                baseline_offset = pressure - 1013.25  # Local atmospheric offset
            
            # Calculate depth relative to first reading
            depth = sensor.depth_mm()
        else:
            # Demo mode — simulate a small leak
            elapsed = session.elapsed_minutes() if session.start_time else 0
            pressure = 1113.25 - (elapsed * 0.005)  # Slow pressure drop
            temp = 28.5 + (0.1 * (elapsed / 60))    # Slight temp rise
            depth = 1000.0 - (elapsed * 0.02)        # ~1.2 mm/hr loss
        
        if session.status in ("baseline", "testing"):
            session.add_reading(timestamp, pressure, temp, depth)
        
        time.sleep(CONFIG["SAMPLE_INTERVAL_SEC"])


# ─── Web Dashboard ────────────────────────────────────────────────────
app = Flask(__name__)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PoolSense — Leak Detection</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #0a0e1a; color: #e0e6ed;
            min-height: 100vh;
        }
        .container { max-width: 800px; margin: 0 auto; padding: 20px; }
        
        h1 { 
            font-size: 1.8rem; text-align: center; margin-bottom: 8px;
            background: linear-gradient(135deg, #00b4d8, #0077b6);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .subtitle { text-align: center; color: #6b7280; margin-bottom: 24px; }
        
        .card {
            background: #141b2d; border-radius: 12px; padding: 20px;
            margin-bottom: 16px; border: 1px solid #1e293b;
        }
        .card h2 { font-size: 1rem; color: #94a3b8; margin-bottom: 12px; }
        
        .metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
        .metric { text-align: center; }
        .metric .value { font-size: 1.8rem; font-weight: 700; color: #00b4d8; }
        .metric .label { font-size: 0.75rem; color: #64748b; margin-top: 4px; }
        
        .status-badge {
            display: inline-block; padding: 6px 16px; border-radius: 20px;
            font-weight: 600; font-size: 0.9rem;
        }
        .status-idle { background: #1e293b; color: #94a3b8; }
        .status-baseline { background: #1e3a5f; color: #38bdf8; }
        .status-testing { background: #064e3b; color: #34d399; animation: pulse 2s infinite; }
        .status-complete { background: #1e3a1e; color: #4ade80; }
        
        .verdict-PASS { background: #064e3b; border-color: #34d399; }
        .verdict-LEAK, .verdict-MAJOR { background: #3b0f0f; border-color: #ef4444; }
        .verdict-BORDERLINE { background: #3b2f0f; border-color: #f59e0b; }
        
        canvas { width: 100%; height: 300px; }
        
        .btn {
            padding: 12px 24px; border: none; border-radius: 8px;
            font-size: 1rem; font-weight: 600; cursor: pointer;
            transition: all 0.2s;
        }
        .btn-start { background: #00b4d8; color: #0a0e1a; }
        .btn-start:hover { background: #0077b6; }
        .btn-stop { background: #ef4444; color: white; }
        .btn-stop:hover { background: #dc2626; }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        
        .controls { display: flex; gap: 12px; justify-content: center; margin: 16px 0; }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.7; }
        }
        
        #chart { background: #0d1321; border-radius: 8px; padding: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🌊 PoolSense</h1>
        <p class="subtitle">Precision Leak Detection System</p>
        
        <div class="card">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <h2>STATUS</h2>
                <span id="status-badge" class="status-badge status-idle">IDLE</span>
            </div>
            <div class="metrics" style="margin-top: 16px;">
                <div class="metric">
                    <div class="value" id="pressure">--</div>
                    <div class="label">Pressure (mbar)</div>
                </div>
                <div class="metric">
                    <div class="value" id="temp">--</div>
                    <div class="label">Water Temp (°C)</div>
                </div>
                <div class="metric">
                    <div class="value" id="depth">--</div>
                    <div class="label">Depth (mm)</div>
                </div>
            </div>
        </div>
        
        <div class="card" id="chart-card">
            <h2>WATER LEVEL OVER TIME</h2>
            <canvas id="chart"></canvas>
        </div>
        
        <div class="card" id="result-card" style="display: none;">
            <h2>RESULTS</h2>
            <div id="result-content"></div>
        </div>
        
        <div class="controls">
            <button class="btn btn-start" id="btn-start" onclick="startTest()">Start Leak Test</button>
            <button class="btn btn-stop" id="btn-stop" onclick="stopTest()" disabled>Stop Test</button>
        </div>
        
        <div class="card">
            <h2>ELAPSED</h2>
            <div class="metrics">
                <div class="metric">
                    <div class="value" id="elapsed">0:00</div>
                    <div class="label">Test Time</div>
                </div>
                <div class="metric">
                    <div class="value" id="readings-count">0</div>
                    <div class="label">Readings</div>
                </div>
                <div class="metric">
                    <div class="value" id="leak-rate">--</div>
                    <div class="label">Leak Rate (mm/hr)</div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        let chartData = [];
        let canvas, ctx;
        let lastTimestamp = 0;
        
        window.onload = () => {
            canvas = document.getElementById('chart');
            ctx = canvas.getContext('2d');
            canvas.width = canvas.offsetWidth * 2;
            canvas.height = 600;
            setInterval(poll, 1000);
        };
        
        async function poll() {
            try {
                const res = await fetch(`/api/data?since=${lastTimestamp}`);
                const data = await res.json();
                
                // Update status
                const badge = document.getElementById('status-badge');
                badge.textContent = data.status.toUpperCase();
                badge.className = `status-badge status-${data.status}`;
                
                // Update latest reading
                if (data.latest) {
                    document.getElementById('pressure').textContent = data.latest.p.toFixed(2);
                    document.getElementById('temp').textContent = data.latest.temp.toFixed(1);
                    document.getElementById('depth').textContent = data.latest.d.toFixed(1);
                }
                
                // Update elapsed
                document.getElementById('elapsed').textContent = data.elapsed || '0:00';
                document.getElementById('readings-count').textContent = data.total_readings || 0;
                
                // Add new data points
                if (data.new_readings && data.new_readings.length > 0) {
                    chartData.push(...data.new_readings);
                    lastTimestamp = data.new_readings[data.new_readings.length - 1].t;
                    drawChart();
                }
                
                // Update leak rate
                if (data.leak_rate !== null && data.leak_rate !== undefined) {
                    document.getElementById('leak-rate').textContent = data.leak_rate.leak_rate_mm_hr;
                }
                
                // Show result
                if (data.status === 'complete' && data.result) {
                    showResult(data.result);
                }
                
                // Button states
                document.getElementById('btn-start').disabled = data.status !== 'idle';
                document.getElementById('btn-stop').disabled = data.status === 'idle' || data.status === 'complete';
                
            } catch (e) { console.error('Poll error:', e); }
        }
        
        function drawChart() {
            if (chartData.length < 2) return;
            
            const w = canvas.width, h = canvas.height;
            const pad = { top: 20, right: 20, bottom: 40, left: 60 };
            
            ctx.clearRect(0, 0, w, h);
            ctx.fillStyle = '#0d1321';
            ctx.fillRect(0, 0, w, h);
            
            // Data range
            const depths = chartData.map(r => r.d);
            const minD = Math.min(...depths) - 1;
            const maxD = Math.max(...depths) + 1;
            const timeRange = chartData[chartData.length - 1].t - chartData[0].t;
            
            // Draw grid
            ctx.strokeStyle = '#1e293b';
            ctx.lineWidth = 1;
            for (let i = 0; i <= 5; i++) {
                const y = pad.top + (h - pad.top - pad.bottom) * (i / 5);
                ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(w - pad.right, y); ctx.stroke();
                
                const val = maxD - (maxD - minD) * (i / 5);
                ctx.fillStyle = '#64748b'; ctx.font = '20px sans-serif'; ctx.textAlign = 'right';
                ctx.fillText(val.toFixed(1), pad.left - 8, y + 6);
            }
            
            // Draw line
            ctx.strokeStyle = '#00b4d8';
            ctx.lineWidth = 2;
            ctx.beginPath();
            
            chartData.forEach((r, i) => {
                const x = pad.left + ((r.t - chartData[0].t) / timeRange) * (w - pad.left - pad.right);
                const y = pad.top + ((maxD - r.d) / (maxD - minD)) * (h - pad.top - pad.bottom);
                if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
            });
            ctx.stroke();
            
            // Labels
            ctx.fillStyle = '#64748b'; ctx.font = '20px sans-serif'; ctx.textAlign = 'center';
            ctx.fillText('Depth (mm)', pad.left - 30, pad.top - 5);
        }
        
        async function startTest() {
            chartData = []; lastTimestamp = 0;
            document.getElementById('result-card').style.display = 'none';
            await fetch('/api/start', { method: 'POST' });
        }
        
        async function stopTest() {
            await fetch('/api/stop', { method: 'POST' });
        }
        
        function showResult(result) {
            const card = document.getElementById('result-card');
            const cls = result.verdict.status.includes('LEAK') ? 'verdict-LEAK' : 
                        result.verdict.status === 'PASS' ? 'verdict-PASS' : 'verdict-BORDERLINE';
            card.className = `card ${cls}`;
            card.style.display = 'block';
            
            card.querySelector('#result-content').innerHTML = `
                <div style="font-size: 1.5rem; font-weight: 700; margin-bottom: 8px;">
                    ${result.verdict.status}
                </div>
                <p>${result.verdict.message}</p>
                <div class="metrics" style="margin-top: 16px;">
                    <div class="metric">
                        <div class="value">${result.leak_rate_mm_hr}</div>
                        <div class="label">mm/hr loss</div>
                    </div>
                    <div class="metric">
                        <div class="value">${result.leak_rate_gal_day}</div>
                        <div class="label">est. gal/day</div>
                    </div>
                    <div class="metric">
                        <div class="value">${result.elapsed_hours}h</div>
                        <div class="label">test duration</div>
                    </div>
                </div>
            `;
        }
    </script>
</body>
</html>
"""

@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML)

@app.route("/api/data")
def api_data():
    from flask import request
    since = float(request.args.get("since", 0))
    
    new_readings = session.get_readings_since(since)
    latest = new_readings[-1] if new_readings else None
    
    elapsed_min = session.elapsed_minutes()
    elapsed_str = f"{int(elapsed_min)}:{int(elapsed_min % 1 * 60):02d}"
    
    leak_rate = None
    if session.status == "testing" and elapsed_min > 6:
        leak_rate = session.calculate_leak_rate()
    
    return jsonify({
        "status": session.status,
        "elapsed": elapsed_str,
        "total_readings": len(session.readings),
        "new_readings": new_readings[-300:],  # Cap at 300 per update
        "latest": latest,
        "leak_rate": leak_rate,
        "result": session.result,
    })

@app.route("/api/start", methods=["POST"])
def api_start():
    global session
    session = TestSession()
    session.status = "baseline"
    session.start_time = time.time()
    
    # After baseline period, switch to testing
    def switch_to_testing():
        time.sleep(CONFIG["BASELINE_DURATION_MIN"] * 60)
        if session.status == "baseline":
            session.status = "testing"
            print(f"[PoolSense] Baseline complete. Testing started.")
    
    threading.Thread(target=switch_to_testing, daemon=True).start()
    
    return jsonify({"ok": True, "message": "Test started. Baseline phase..."})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    session.status = "complete"
    session.result = session.calculate_leak_rate()
    
    # Save results to file
    os.makedirs(CONFIG["DATA_DIR"], exist_ok=True)
    filename = f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    filepath = os.path.join(CONFIG["DATA_DIR"], filename)
    
    with open(filepath, "w") as f:
        json.dump({
            "result": session.result,
            "readings_count": len(session.readings),
            "start_time": session.start_time,
            "end_time": time.time(),
        }, f, indent=2)
    
    print(f"[PoolSense] Test saved to {filepath}")
    return jsonify({"ok": True, "result": session.result})


# ─── Main ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  PoolSense — Leak Detection System")
    print("  Dashboard: http://poolsense.local:8080")
    print("=" * 50)
    
    # Start sensor reading in background
    t = threading.Thread(target=sensor_thread, daemon=True)
    t.start()
    
    # Start web server
    app.run(host="0.0.0.0", port=CONFIG["WEB_PORT"], debug=False)
