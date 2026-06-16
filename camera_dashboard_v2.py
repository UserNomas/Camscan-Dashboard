#!/usr/bin/env python3
import argparse
import base64
import csv
import datetime
import html
import json
import os
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    import asyncio
    from aiortc import RTCPeerConnection, RTCSessionDescription
    from aiortc.contrib.media import MediaPlayer
    AIORTC_AVAILABLE = True
except ImportError:
    RTCPeerConnection = None
    RTCSessionDescription = None
    MediaPlayer = None
    asyncio = None
    AIORTC_AVAILABLE = False

DEFAULT_PORT = 8080
HEALTH_INTERVAL = 20
STREAM_HISTORY_FILE = 'camera_history.json'
MAX_HISTORY_ENTRIES = 200

DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>Camera Dashboard v2</title>
<style>
:root { --bg: #f4f6f8; --card: #ffffff; --border: #d1d5db; --text: #111827; --muted: #6b7280; --green: #16a34a; --yellow: #f59e0b; --red: #dc2626; --blue: #2563eb; --purple: #7c3aed; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font-family: Inter, system-ui, sans-serif; }
header { padding: 20px; background: #111827; color: #fff; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 16px; }
header h1 { margin: 0; font-size: 1.4rem; }
header .actions { display: flex; gap: 10px; flex-wrap: wrap; }
header button, header a.button { color: white; background: var(--blue); border: none; border-radius: 8px; padding: 10px 14px; cursor: pointer; text-decoration: none; }
main { padding: 20px; }
.toolbar { display: grid; grid-template-columns: 1fr auto; gap: 14px; margin-bottom: 20px; }
.filters, .sort { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.filters button, .sort select { border: 1px solid var(--border); border-radius: 10px; padding: 10px 14px; background: white; color: var(--text); cursor: pointer; }
.filters button.active { background: var(--blue); color: white; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }
.stat { background: var(--card); padding: 18px; border: 1px solid var(--border); border-radius: 18px; }
.stat h2 { margin: 0 0 8px; font-size: 1rem; color: var(--muted); }
.stat p { margin: 0; font-size: 1.8rem; font-weight: 700; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 18px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 18px; overflow: hidden; display: flex; flex-direction: column; }
.card-header { padding: 16px; display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.card-header h3 { margin: 0; font-size: 1rem; }
.badge { border-radius: 999px; padding: 6px 10px; font-size: 0.75rem; color: white; white-space: nowrap; }
.badge.up { background: var(--green); }
.badge.warning { background: var(--yellow); color: #111827; }
.badge.down { background: var(--red); }
.card-body { padding: 0 16px 16px; flex: 1; }
.card-body p { margin: 6px 0; color: var(--muted); }
.card-body p strong { color: var(--text); }
.card-actions { display: flex; flex-wrap: wrap; gap: 10px; padding: 0 16px 16px; }
.button { display: inline-flex; align-items: center; justify-content: center; padding: 10px 14px; background: var(--blue); color: white; border-radius: 10px; border: none; font-size: 0.9rem; cursor: pointer; text-decoration: none; }
.button.secondary { background: #475569; }
.button.mini { padding: 8px 12px; font-size: 0.8rem; }
.preview { width: 100%; height: 190px; background: #000; display: flex; align-items: center; justify-content: center; color: #fff; font-size: 0.95rem; }
.small { font-size: 0.85rem; }
.code { background: #e2e8f0; padding: 4px 6px; border-radius: 6px; }
.tag-list { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.tag { background: #e2e8f0; color: var(--text); border-radius: 999px; padding: 6px 10px; font-size: 0.78rem; }
</style>
</head>
<body>
<header>
  <div>
    <h1>Camera Dashboard v2</h1>
    <p style="margin:4px 0 0; color:#d1d5db;">Grid view, health status, RTSP readiness, and live preview controls.</p>
  </div>
  <div class="actions">
    <button onclick="refreshData()">Refresh</button>
    <a class="button" href="/grid" target="_blank">WebRTC Grid</a>
    <a class="button secondary" href="/help" target="_blank">Help</a>
  </div>
</header>
<main>
  <div class="toolbar">
    <div class="filters">
      <button id="filter-all" class="active" onclick="setFilter('all')">All</button>
      <button id="filter-up" onclick="setFilter('up')">Healthy</button>
      <button id="filter-rtsp" onclick="setFilter('rtsp')">RTSP</button>
      <button id="filter-http" onclick="setFilter('http')">HTTP</button>
    </div>
    <div class="sort">
      <label for="sort-by" style="font-size:0.9rem; color:#fff;">Sort:</label>
      <select id="sort-by" onchange="setSort(this.value)">
        <option value="ip">IP</option>
        <option value="health">Health</option>
        <option value="rtsp">RTSP</option>
      </select>
    </div>
  </div>
  <div class="stats">
    <div class="stat"><h2>Discovered cameras</h2><p id="count-cameras">0</p></div>
    <div class="stat"><h2>Healthy</h2><p id="count-healthy">0</p></div>
    <div class="stat"><h2>RTSP ready</h2><p id="count-rtsp">0</p></div>
    <div class="stat"><h2>Last refresh</h2><p id="last-refresh">-</p></div>
  </div>
  <div id="grid" class="grid"></div>
</main>
<script>
let currentFilter = 'all';
let currentSort = 'ip';
function q(selector) { return document.querySelector(selector); }
function htmlEscape(value) { return value ? value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') : ''; }
function setFilter(mode) {
  currentFilter = mode;
  document.querySelectorAll('.filters button').forEach(btn => btn.classList.toggle('active', btn.id === `filter-${mode}`));
  refreshData();
}
function setSort(mode) {
  currentSort = mode;
  refreshData();
}
function refreshData() {
  fetch('/api/cameras')
    .then(r => r.json())
    .then(data => renderCameras(data))
    .catch(err => {
      console.error(err);
      q('#grid').innerHTML = '<p style="grid-column:1/-1;color:#b91c1c;">Unable to load camera data.</p>';
    });
}
function renderCameras(data) {
  const grid = q('#grid');
  grid.innerHTML = '';
  let healthy = 0, rtsp = 0;
  const filtered = data.filter(camera => matchesFilter(camera));
  const sorted = filtered.sort(sortByKey(currentSort));
  sorted.forEach(camera => {
    const isUp = camera.health_up === 'True' || camera.health_up === true;
    if (isUp) healthy += 1;
    if (camera.RTSPURL) rtsp += 1;
    const connectionTags = buildConnectionTags(camera);
    const quality = getQualityLabel(camera);
    const videoPreview = camera.RTSPURL ? `<div class="preview">RTSP stream ready</div>` : `<div class="preview">No RTSP URL</div>`;
    const webrtcButton = camera.RTSPURL ? `<a class="button" href="/viewer?rtsp=${encodeURIComponent(btoa(camera.RTSPURL))}" target="_blank">WebRTC</a>` : '';
    const mjpegButton = camera.RTSPURL ? `<a class="button secondary" href="/mjpeg?rtsp=${encodeURIComponent(btoa(camera.RTSPURL))}" target="_blank">MJPEG</a>` : '';
    const copyButton = camera.RTSPURL ? `<button class="button secondary mini" onclick="copyText('${encodeURIComponent(camera.RTSPURL)}')">Copy RTSP</button>` : '';
    const httpButton = camera.OpenPorts && camera.OpenPorts.split(',').includes('80') ? `<a class="button secondary" href="http://${camera.IP}" target="_blank">HTTP</a>` : '';
    const httpsButton = camera.OpenPorts && camera.OpenPorts.split(',').includes('443') ? `<a class="button secondary" href="https://${camera.IP}" target="_blank">HTTPS</a>` : '';
    const hit = camera.health_last_seen || 'never';
    grid.innerHTML += `<div class="card">
      <div class="card-header">
        <div>
          <h3>${htmlEscape(camera.IP)}</h3>
          <p class="small">${htmlEscape(camera.Hostname || camera.NmapName || camera.ReverseDNS || 'unknown')}</p>
        </div>
        <span class="badge ${isUp ? 'up' : 'down'}">${isUp ? 'UP' : 'DOWN'}</span>
      </div>
      ${videoPreview}
      <div class="card-body">
        <div class="tag-list">${connectionTags.map(tag => `<span class="tag">${htmlEscape(tag)}</span>`).join('')}</div>
        <p><strong>Quality:</strong> ${htmlEscape(quality)}</p>
        <p><strong>RTSP:</strong> ${htmlEscape(camera.RTSPURL || 'none')}</p>
        <p><strong>Admin:</strong> ${htmlEscape(camera.AdminHint || '—')}</p>
        <p class="small">Last seen: ${htmlEscape(hit)}</p>
        <p class="small">Source: ${htmlEscape(camera.Source || 'unknown')}</p>
      </div>
      <div class="card-actions">
        ${webrtcButton}${mjpegButton}${httpButton}${httpsButton}${copyButton}
      </div>
    </div>`;
  });
  q('#count-cameras').textContent = data.length;
  q('#count-healthy').textContent = healthy;
  q('#count-rtsp').textContent = rtsp;
  q('#last-refresh').textContent = new Date().toLocaleTimeString();
}
function matchesFilter(camera) {
  const isUp = camera.health_up === 'True' || camera.health_up === true;
  const hasRtsp = Boolean(camera.RTSPURL);
  const ports = camera.OpenPorts ? camera.OpenPorts.split(',') : [];
  if (currentFilter === 'up' && !isUp) return false;
  if (currentFilter === 'rtsp' && !hasRtsp) return false;
  if (currentFilter === 'http' && !ports.includes('80') && !ports.includes('443')) return false;
  return true;
}
function sortByKey(key) {
  return (a, b) => {
    if (key === 'health') {
      return Number(b.health_up === 'True' || b.health_up === true) - Number(a.health_up === 'True' || a.health_up === true);
    }
    if (key === 'rtsp') {
      return Number(Boolean(b.RTSPURL)) - Number(Boolean(a.RTSPURL));
    }
    return a.IP.localeCompare(b.IP, undefined, { numeric: true });
  };
}
function buildConnectionTags(camera) {
  const tags = [];
  const ports = camera.OpenPorts ? camera.OpenPorts.split(',') : [];
  if (ports.includes('80')) tags.push('HTTP');
  if (ports.includes('443')) tags.push('HTTPS');
  if (ports.includes('554')) tags.push('RTSP');
  if (camera.RTSPURL) tags.push('Live Stream');
  if (!tags.length) tags.push('No open service');
  return tags;
}
function getQualityLabel(camera) {
  const url = (camera.RTSPURL || '').toLowerCase();
  if (!url) return 'Unknown';
  if (url.includes('101') || url.includes('ch1') || url.includes('high')) return 'High';
  if (url.includes('102') || url.includes('ch0_1') || url.includes('low')) return 'Low';
  return 'Standard';
}
function copyText(encodedUrl) {
  const value = decodeURIComponent(encodedUrl);
  if (navigator.clipboard) {
    navigator.clipboard.writeText(value).catch(console.error);
  } else {
    const textarea = document.createElement('textarea');
    textarea.value = value;
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand('copy');
    textarea.remove();
  }
}
refreshData();
</script>
</body>
</html>
"""

HELP_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Camera Dashboard Help</title></head>
<body style="font-family:Arial,sans-serif; padding:20px; background:#f8fafc; color:#111;">
<h1>Camera Dashboard v2 Help</h1>
<p>This dashboard provides a grid-based camera inventory with health checks and preview capabilities.</p>
<ul>
  <li><b>WebRTC</b> preview is available if the server has the <code>aiortc</code> package installed.</li>
  <li><b>MJPEG</b> preview is a fallback rendered through <code>ffmpeg</code>.</li>
  <li><b>Health checks</b> happen in the background and measure connectivity to camera ports.</li>
</ul>
<h2>Future upgrade capabilities</h2>
<ul>
  <li><b>Filtered grid view</b> for healthy cameras, RTSP-ready devices, and HTTP/HTTPS-enabled feeds.</li>
  <li><b>Connection quality hints</b> based on detected RTSP path and stream type.</li>
  <li><b>One-click preview</b> with WebRTC and MJPEG fallback for flexible browser support.</li>
  <li><b>Live action buttons</b> for browser access, RTSP copy links, and quick stream probes.</li>
  <li><b>Historical uptime</b> and metrics support to log camera health over time.</li>
</ul>
<h2>Recommended stack for enhancements</h2>
<ul>
  <li><b>FastAPI</b> or <b>aiohttp</b> for an async REST + WebSocket backend.</li>
  <li><b>aiortc</b> for low-latency WebRTC proxying.</li>
  <li><b>Redis</b> / SQLite for inventory, state, and credential storage.</li>
  <li><b>Grafana</b> / Prometheus for camera uptime and health dashboards.</li>
  <li><b>React</b> / <b>Svelte</b> for a responsive frontend and multi-camera layout.</li>
</ul>
</body>
</html>
"""

class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


class CameraDashboardServer(ThreadingHTTPServer):
    def __init__(self, addr, handler_class, csv_path):
        super().__init__(addr, handler_class)
        self.csv_path = csv_path
        self.health_map = {}
        self.health_lock = threading.Lock()
        self.history_path = os.path.join(os.path.dirname(self.csv_path), STREAM_HISTORY_FILE)
        self.history_lock = threading.Lock()
        self.history = self.load_history()
        self.start_health_thread()
        if AIORTC_AVAILABLE:
            self.webrtc_loop = asyncio.new_event_loop()
            self.webrtc_thread = threading.Thread(target=self.webrtc_loop.run_forever, daemon=True)
            self.webrtc_thread.start()

    def load_history(self):
        if not os.path.isfile(self.history_path):
            return []
        try:
            with open(self.history_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []

    def save_history(self):
        try:
            with self.history_lock:
                with open(self.history_path, 'w', encoding='utf-8') as f:
                    json.dump(self.history, f, indent=2)
        except Exception as exc:
            print('Failed to save history:', exc, file=sys.stderr)

    def append_history(self, entry):
        with self.history_lock:
            self.history.append(entry)
            self.history = self.history[-MAX_HISTORY_ENTRIES:]
            self.save_history()

    def get_history(self):
        with self.history_lock:
            return list(self.history)

    def compute_quality_label(self, rtsp_url):
        if not rtsp_url:
            return 'Unknown'
        url = rtsp_url.lower()
        if '101' in url or 'ch1' in url or 'high' in url:
            return 'High'
        if '102' in url or 'ch0_1' in url or 'low' in url:
            return 'Low'
        return 'Standard'

    def load_cameras(self):
        cameras = []
        if not os.path.isfile(self.csv_path):
            return cameras
        with open(self.csv_path, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                row.update(self.get_health(row.get('IP', '')))
                row['HealthQuality'] = self.compute_quality_label(row.get('RTSPURL', ''))
                if row.get('RTSPURL'):
                    encoded = base64.b64encode(row['RTSPURL'].encode('utf-8')).decode('ascii')
                    row['ThumbnailURL'] = f"/thumbnail?rtsp={urllib.parse.quote_plus(encoded)}"
                else:
                    row['ThumbnailURL'] = ''
                cameras.append(row)
        return cameras

    def get_health(self, ip):
        with self.health_lock:
            entry = self.health_map.get(ip, {})
            return {
                'health_up': entry.get('up', False),
                'health_last_seen': entry.get('last_seen', ''),
                'health_checked': entry.get('last_checked', ''),
            }

    def start_health_thread(self):
        thread = threading.Thread(target=self.health_worker, daemon=True)
        thread.start()

    def health_worker(self):
        while True:
            rows = []
            if os.path.isfile(self.csv_path):
                with open(self.csv_path, newline='', encoding='utf-8') as f:
                    rows = [row for row in csv.DictReader(f)]
            now = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'
            for row in rows:
                ip = row.get('IP')
                ports = [int(p) for p in row.get('OpenPorts', '').split(',') if p.strip().isdigit()]
                rtsp_url = row.get('RTSPURL', '')
                up = any(self.check_port(ip, port) for port in ports)
                with self.health_lock:
                    self.health_map[ip] = {
                        'up': up,
                        'last_seen': now if up else '',
                        'last_checked': now,
                    }
                if rtsp_url:
                    self.append_history({
                        'timestamp': now,
                        'IP': ip,
                        'up': up,
                        'RTSPURL': rtsp_url,
                        'Quality': self.compute_quality_label(rtsp_url),
                        'OpenPorts': row.get('OpenPorts', ''),
                    })
            time.sleep(HEALTH_INTERVAL)

    def check_port(self, ip, port, timeout=1.0):
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return True
        except Exception:
            return False

    async def create_webrtc_answer(self, sdp, type_, rtsp_url):
        pc = RTCPeerConnection()
        player = MediaPlayer(rtsp_url, format='rtsp', options={'rtsp_transport': 'tcp'})
        if player.video:
            pc.addTrack(player.video)
        await pc.setRemoteDescription(RTCSessionDescription(sdp, type_))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return {'sdp': pc.localDescription.sdp, 'type': pc.localDescription.type}


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == '/':
            self.respond_html(DASHBOARD_HTML)
        elif path == '/help':
            self.respond_html(HELP_HTML)
        elif path == '/api/cameras':
            self.respond_json(self.server.load_cameras())
        elif path == '/api/history':
            self.respond_json(self.server.get_history())
        elif path == '/viewer':
            self.serve_viewer(parsed.query)
        elif path == '/grid':
            self.respond_html(self.build_grid_page())
        elif path == '/history':
            self.respond_html(self.build_history_page())
        elif path == '/thumbnail':
            self.serve_thumbnail(parsed.query)
        elif path == '/mjpeg':
            self.serve_mjpeg(parsed.query)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, 'Not Found')

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/offer':
            self.handle_offer()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, 'Not Found')

    def respond_html(self, content):
        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(content.encode('utf-8'))

    def respond_json(self, data):
        payload = json.dumps(data)
        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(payload.encode('utf-8'))

    def serve_viewer(self, query):
        params = urllib.parse.parse_qs(query)
        rtsp = self.decode_param(params.get('rtsp', [''])[0])
        if not rtsp:
            self.send_error(HTTPStatus.BAD_REQUEST, 'Missing rtsp parameter')
            return
        if AIORTC_AVAILABLE:
            html_page = self.build_webrtc_page(rtsp)
        else:
            html_page = self.build_mjpeg_page(rtsp)
        self.respond_html(html_page)

    def build_webrtc_page(self, rtsp_url):
        signal_url = '/offer'
        return f"""
<!DOCTYPE html><html><head><meta charset='utf-8'><title>WebRTC Camera</title></head><body>
<h2>WebRTC Preview</h2>
<p>{html.escape(rtsp_url)}</p>
<video id='video' autoplay playsinline controls style='width:100%;max-width:720px;background:#000;'></video>
<script>
const video = document.getElementById('video');
const pc = new RTCPeerConnection();
pc.ontrack = event => {{ video.srcObject = event.streams[0]; }};
async function init() {{
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  const response = await fetch('{signal_url}', {{ method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{ sdp: offer.sdp, type: offer.type, rtsp: '{base64_encode(rtsp_url)}' }}) }});
  const answer = await response.json();
  await pc.setRemoteDescription(new RTCSessionDescription(answer));
}}
init().catch(console.error);
</script>
</body></html>
"""

    def build_grid_page(self):
        if not AIORTC_AVAILABLE:
            return """
<!DOCTYPE html><html><head><meta charset='utf-8'><title>WebRTC Grid Unavailable</title></head><body>
<h2>WebRTC Grid is unavailable</h2>
<p>The server does not have <code>aiortc</code> installed, so multi-camera WebRTC preview cannot be started.</p>
<p>Install <code>aiortc</code> and restart the dashboard to enable the grid view.</p>
</body></html>
"""
        return """
<!DOCTYPE html>
<html lang='en'>
<head>
<meta charset='utf-8'>
<title>WebRTC Camera Grid</title>
<style>
body {{ margin: 0; background: #111827; color: #f8fafc; font-family: Inter, system-ui, sans-serif; }}
header {{ padding: 18px; display: flex; align-items: center; justify-content: space-between; gap: 12px; background: #0f172a; }}
header h1 {{ margin: 0; font-size: 1.3rem; }}
header a {{ color: #fff; text-decoration: none; background: #2563eb; padding: 10px 14px; border-radius: 10px; }}
#grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; padding: 18px; }}
.card {{ border: 1px solid #334155; border-radius: 16px; overflow: hidden; background: #0f172a; display: flex; flex-direction: column; }}
.card-header {{ padding: 14px 16px; display: flex; justify-content: space-between; align-items: center; gap: 10px; }}
.card-header h2 {{ margin: 0; font-size: 1rem; }}
.card-status {{ font-size: 0.8rem; color: #a5b4fc; }}
.video-wrap {{ position: relative; background: #000; min-height: 200px; }}
video {{ width: 100%; height: 100%; object-fit: cover; }}
.card-footer {{ padding: 12px 16px; display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap; }}
.badge {{ border-radius: 999px; padding: 6px 10px; font-size: 0.8rem; color: #fff; }}
.badge.up {{ background: #16a34a; }}
.badge.down {{ background: #dc2626; }}
.badge.rtsp {{ background: #2563eb; }}
#statusBar {{ padding: 12px 18px; font-size: 0.95rem; color: #e2e8f0; }}
</style>
</head>
<body>
<header>
  <h1>Multi-camera WebRTC Grid</h1>
  <a href="/">Back to dashboard</a>
</header>
<div id='statusBar'>Loading cameras...</div>
<div id='grid'></div>
<script>
const signalUrl = '/offer';
const grid = document.getElementById('grid');
const statusBar = document.getElementById('statusBar');
async function loadCameras() {{
  const res = await fetch('/api/cameras');
  const cameras = await res.json();
  const rtspCameras = cameras.filter(cam => cam.RTSPURL);
  if (!rtspCameras.length) {{
    statusBar.textContent = 'No RTSP cameras available for grid preview.';
    return;
  }}
  statusBar.textContent = `Preparing ${{rtspCameras.length}} RTSP camera(s) ...`;
  rtspCameras.forEach((camera, index) => createCard(camera, index));
}}
function createCard(camera, index) {{
  const card = document.createElement('div');
  card.className = 'card';
  const header = document.createElement('div');
  header.className = 'card-header';
  header.innerHTML = `<h2>${{camera.IP}}</h2><span class='card-status'>${{camera.health_up === 'True' || camera.health_up === true ? '<span class="badge up">UP</span>' : '<span class="badge down">DOWN</span>'}}</span>`;
  const videoWrap = document.createElement('div');
  videoWrap.className = 'video-wrap';
  const video = document.createElement('video');
  video.setAttribute('playsinline', '');
  video.setAttribute('autoplay', '');
  video.setAttribute('controls', '');
  videoWrap.appendChild(video);
  const footer = document.createElement('div');
  footer.className = 'card-footer';
  const quality = document.createElement('span');
  quality.className = 'badge rtsp';
  quality.textContent = getQualityLabel(camera.RTSPURL);
  const info = document.createElement('span');
  info.textContent = camera.RTSPURL;
  footer.appendChild(quality);
  footer.appendChild(info);
  card.appendChild(header);
  card.appendChild(videoWrap);
  card.appendChild(footer);
  grid.appendChild(card);
  createPeerConnection(camera.RTSPURL, video, index);
}}
function getQualityLabel(rtsp) {{
  const path = rtsp.toLowerCase();
  if (path.includes('101') || path.includes('ch1') || path.includes('high')) return 'High';
  if (path.includes('102') || path.includes('ch0_1') || path.includes('low')) return 'Low';
  return 'Standard';
}}
async function createPeerConnection(rtspUrl, videoEl, index) {{
  const pc = new RTCPeerConnection();
  pc.ontrack = event => {{ videoEl.srcObject = event.streams[0]; }};
  pc.oniceconnectionstatechange = () => {{
    const state = pc.iceConnectionState;
    videoEl.parentNode.previousElementSibling.querySelector('.card-status').textContent = state;
  }};
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  const response = await fetch(signalUrl, {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ sdp: offer.sdp, type: offer.type, rtsp: btoa(rtspUrl) }}),
  }});
  const answer = await response.json();
  await pc.setRemoteDescription(new RTCSessionDescription(answer));
}}
loadCameras().catch(err => {{
  console.error(err);
  statusBar.textContent = 'Failed to load grid preview: ' + err.message;
}});
</script>
</body>
</html>
"""

    def build_mjpeg_page(self, rtsp_url):
        encoded = base64.b64encode(rtsp_url.encode('utf-8')).decode('ascii')
        return f"""
<!DOCTYPE html><html><head><meta charset='utf-8'><title>MJPEG Camera</title></head><body>
<h2>MJPEG Proxy Preview</h2>
<p>{html.escape(rtsp_url)}</p>
<img src='/mjpeg?rtsp={urllib.parse.quote_plus(encoded)}' style='width:100%;max-width:720px;border:1px solid #333;' />
</body></html>
"""

    def serve_thumbnail(self, query):
        params = urllib.parse.parse_qs(query)
        rtsp = self.decode_param(params.get('rtsp', [''])[0])
        if not rtsp:
            self.send_error(HTTPStatus.BAD_REQUEST, 'Missing rtsp parameter')
            return
        if not shutil.which('ffmpeg'):
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, 'ffmpeg is required for thumbnails')
            return
        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Type', 'image/jpeg')
        self.end_headers()
        args = [
            'ffmpeg',
            '-rtsp_transport', 'tcp',
            '-i', rtsp,
            '-frames:v', '1',
            '-q:v', '2',
            '-f', 'image2',
            'pipe:1',
        ]
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if not proc.stdout:
            return
        try:
            data = proc.stdout.read()
            if data:
                self.wfile.write(data)
        finally:
            proc.kill()
            proc.wait(timeout=5)

    def serve_mjpeg(self, query):
        params = urllib.parse.parse_qs(query)
        rtsp = self.decode_param(params.get('rtsp', [''])[0])
        if not rtsp:
            self.send_error(HTTPStatus.BAD_REQUEST, 'Missing rtsp parameter')
            return
        if not shutil.which('ffmpeg'):
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, 'ffmpeg is required for MJPEG streaming')
            return
        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
        self.end_headers()
        self.stream_mjpeg(rtsp)

    def stream_mjpeg(self, rtsp_url):
        args = [
            'ffmpeg',
            '-rtsp_transport', 'tcp',
            '-i', rtsp_url,
            '-vf', 'scale=640:-1',
            '-q:v', '5',
            '-f', 'mjpeg',
            'pipe:1',
        ]
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if not proc.stdout:
            return
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except BrokenPipeError:
            pass
        finally:
            proc.kill()
            proc.wait(timeout=5)

    def handle_offer(self):
        if not AIORTC_AVAILABLE:
            self.send_error(HTTPStatus.NOT_IMPLEMENTED, 'WebRTC support is unavailable on this server')
            return
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        request = json.loads(body.decode('utf-8'))
        rtsp = self.decode_param(request.get('rtsp', ''))
        if not rtsp:
            self.send_error(HTTPStatus.BAD_REQUEST, 'Missing rtsp')
            return
        result = self.create_webrtc_answer(request['sdp'], request['type'], rtsp)
        if result is None:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, 'Failed to establish WebRTC session')
            return
        self.respond_json(result)

    def create_webrtc_answer(self, sdp, type_, rtsp_url):
        coro = self.server.create_webrtc_answer(sdp, type_, rtsp_url)
        future = asyncio.run_coroutine_threadsafe(coro, self.server.webrtc_loop)
        try:
            return future.result(timeout=20)
        except Exception as exc:
            print('WebRTC answer failed:', exc, file=sys.stderr)
            return No