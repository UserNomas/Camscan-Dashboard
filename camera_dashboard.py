#!/usr/bin/env python3
import argparse
import base64
import csv
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
from http import HTTPStatus

DEFAULT_INPUT_PATTERN = "camaras_*.csv"
DEFAULT_PORT = 8080

HTML_PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>Camera Dashboard</title>
<style>
body { font-family: Arial, sans-serif; margin: 0; padding: 0; background: #f4f6f8; color: #111; }
header { background: #1f2937; color: #fff; padding: 16px; display: flex; align-items: center; justify-content: space-between; }
header h1 { margin: 0; font-size: 20px; }
.container { padding: 18px; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; margin-bottom: 20px; }
th, td { padding: 10px 12px; border: 1px solid #d1d5db; text-align: left; }
th { background: #111827; color: #fff; }
tr:nth-child(even) { background: #fff; }
tr:nth-child(odd) { background: #f9fafb; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; color: #fff; }
.badge-green { background: #16a34a; }
.badge-yellow { background: #f59e0b; }
.badge-red { background: #dc2626; }
.button { display: inline-block; margin: 2px 2px 2px 0; padding: 6px 10px; border-radius: 5px; background: #2563eb; color: white; text-decoration: none; font-size: 13px; }
.button.secondary { background: #4b5563; }
.preview { max-width: 100%; border: 1px solid #d1d5db; border-radius: 6px; margin-top: 10px; }
.status { margin-top: 12px; color: #4b5563; }
</style>
</head>
<body>
<header>
  <h1>Camera Dashboard</h1>
  <div>
    <button class="button" onclick="loadCameras()">Refresh</button>
    <a class="button secondary" href="/help" target="_blank">Help</a>
  </div>
</header>
<div class="container">
  <div class="status" id="status">Loading camera inventory...</div>
  <div class="table-wrap"><table id="camera-table"><thead><tr>
    <th>IP</th>
    <th>Ports</th>
    <th>Admin / UI</th>
    <th>RTSP</th>
    <th>FFmpeg</th>
    <th>Actions</th>
  </tr></thead><tbody></tbody></table></div>
  <div id="preview-area"></div>
</div>
<script>
function encodeUrl(value) {
  return encodeURIComponent(btoa(value));
}
function decodeUrl(value) {
  return atob(decodeURIComponent(value));
}
function safeText(text) {
  return text ? text.replace(/</g, '&lt;').replace(/>/g, '&gt;') : '';
}
function loadCameras() {
  document.getElementById('status').textContent = 'Refreshing camera list...';
  fetch('/api/cameras')
    .then(resp => resp.json())
    .then(data => {
      const tbody = document.querySelector('#camera-table tbody');
      tbody.innerHTML = '';
      data.forEach(camera => {
        const tr = document.createElement('tr');
        const rtsp = camera.RTSPURL || '';
        const admin = camera.AdminHint || camera.HTTPTitle || camera.ServerHeader || 'None';
        const ffmpeg = rtsp ? `ffmpeg -rtsp_transport tcp -i "${rtsp}" -f mjpeg -q:v 5 -` : 'n/a';
        tr.innerHTML = `
          <td>${safeText(camera.IP)}</td>
          <td>${safeText(camera.OpenPorts)}</td>
          <td>${safeText(admin)}</td>
          <td>${safeText(rtsp)}</td>
          <td><code>${safeText(ffmpeg)}</code></td>
          <td>
            ${rtsp ? `<a class="button" href="/viewer?rtsp=${encodeUrl(rtsp)}" target="_blank">View</a>` : ''}
            ${camera.OpenPorts.includes('80') ? `<a class="button secondary" href="http://${camera.IP}" target="_blank">HTTP</a>` : ''}
            ${camera.OpenPorts.includes('443') ? `<a class="button secondary" href="https://${camera.IP}" target="_blank">HTTPS</a>` : ''}
          </td>
        `;
        tbody.appendChild(tr);
      });
      document.getElementById('status').textContent = `Loaded ${data.length} cameras. Click View for live mjpeg preview.`;
    })
    .catch(err => {
      console.error(err);
      document.getElementById('status').textContent = 'Failed to load cameras.';
    });
}
window.addEventListener('load', loadCameras);
</script>
</body>
</html>
"""

HELP_PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>Camera Dashboard Help</title>
<style>body{font-family:Arial,sans-serif;line-height:1.5;padding:20px;background:#f8fafc;color:#111}h1{margin-bottom:12px}code{background:#e5e7eb;padding:2px 4px;border-radius:4px;}</style>
</head>
<body>
<h1>Camera Dashboard Help</h1>
<p>This dashboard reads a camera scan CSV and shows discovered devices with RTSP support.</p>
<ul>
  <li><strong>View</strong> opens a live MJPEG preview using <code>ffmpeg</code> on the server.</li>
  <li><strong>HTTP</strong> / <strong>HTTPS</strong> buttons open the camera web interface directly.</li>
  <li>If RTSP is missing, you can use the discovered ports and admin path hints to build a stream URL.</li>
</ul>
<p>Recommended stack for your own viewer:</p>
<ul>
  <li>Python dashboard server for inventory and proxying.</li>
  <li><code>ffmpeg</code> to convert RTSP to browser-compatible MJPEG or HLS.</li>
  <li>Frontend HTML/JS to show camera metadata and preview windows.</li>
</ul>
<p>If you want deeper capabilities, add:</p>
<ul>
  <li>WebSocket or WebRTC proxy for low-latency RTSP playback.</li>
  <li>Credentials management for protected camera streams.</li>
  <li>Health and uptime checks on discovered devices.</li>
</ul>
</body>
</html>
"""


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/':
            self.send_html(HTML_PAGE)
        elif parsed.path == '/help':
            self.send_html(HELP_PAGE)
        elif parsed.path == '/api/cameras':
            self.send_api()
        elif parsed.path == '/viewer':
            self.serve_viewer(parsed.query)
        elif parsed.path == '/stream':
            self.serve_stream(parsed.query)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, 'Not Found')

    def send_html(self, body):
        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(body.encode('utf-8'))

    def send_json(self, data):
        payload = json.dumps(data, indent=2)
        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(payload.encode('utf-8'))

    def send_api(self):
        cameras = self.server.load_cameras()
        self.send_json(cameras)

    def serve_viewer(self, query):
        params = urllib.parse.parse_qs(query)
        rtsp = self.decode_param(params.get('rtsp', [''])[0])
        if not rtsp:
            self.send_error(HTTPStatus.BAD_REQUEST, 'Missing rtsp parameter')
            return
        html = f"""
<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Camera Viewer</title></head><body>
<h2>RTSP Preview</h2>
<p>{rtsp}</p>
<img src="/stream?rtsp={urllib.parse.quote_plus(params.get('rtsp', [''])[0])}" style="max-width:100%;border:1px solid #333;" />
<p>Use the direct RTSP URL in VLC / ffmpeg if playback is not smooth.</p>
</body></html>
"""
        self.send_html(html)

    def serve_stream(self, query):
        params = urllib.parse.parse_qs(query)
        encoded = params.get('rtsp', [''])[0]
        rtsp_url = self.decode_param(encoded)
        if not rtsp_url:
            self.send_error(HTTPStatus.BAD_REQUEST, 'Missing rtsp parameter')
            return
        if not shutil.which('ffmpeg'):
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, 'ffmpeg is required for stream proxying')
            return
        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
        self.end_headers()
        self.stream_rtsp(rtsp_url)

    def stream_rtsp(self, rtsp_url):
        args = [
            'ffmpeg',
            '-rtsp_transport', 'tcp',
            '-i', rtsp_url,
            '-vf', 'scale=640:-1',
            '-q:v', '5',
            '-f', 'mjpeg',
            'pipe:1',
        ]
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if not proc.stdout:
            return
        buffer = b''
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                buffer += chunk
                while True:
                    start = buffer.find(b'\xff\xd8')
                    end = buffer.find(b'\xff\xd9', start + 2)
                    if start != -1 and end != -1:
                        frame = buffer[start:end+2]
                        buffer = buffer[end+2:]
                        self.wfile.write(b'--frame\r\n')
                        self.wfile.write(b'Content-Type: image/jpeg\r\n')
                        self.wfile.write(f'Content-Length: {len(frame)}\r\n\r\n'.encode('ascii'))
                        self.wfile.write(frame)
                        self.wfile.write(b'\r\n')
                        self.wfile.flush()
                    else:
                        break
        except BrokenPipeError:
            pass
        finally:
            proc.kill()
            proc.wait(timeout=5)

    def decode_param(self, value):
        try:
            return base64.b64decode(urllib.parse.unquote_plus(value)).decode('utf-8')
        except Exception:
            return ''

    def log_message(self, format, *args):
        return


class CameraDashboardServer(ThreadingHTTPServer):
    def __init__(self, server_address, handler_class, csv_path):
        super().__init__(server_address, handler_class)
        self.csv_path = csv_path

    def load_cameras(self):
        if not os.path.isfile(self.csv_path):
            return []
        with open(self.csv_path, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            return [row for row in reader]


def find_latest_csv(search_dir):
    files = [f for f in os.listdir(search_dir) if f.startswith('camaras_') and f.endswith('.csv')]
    files.sort(key=lambda f: os.path.getmtime(os.path.join(search_dir, f)), reverse=True)
    return os.path.join(search_dir, files[0]) if files else ''


def parse_args():
    parser = argparse.ArgumentParser(description='Camera dashboard server for RTSP/HTTP camera inventory.')
    parser.add_argument('--csv', default=None, help='Input CSV file generated by scan_cameras.py')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help='Dashboard HTTP port')
    return parser.parse_args()


def main():
    args = parse_args()
    csv_path = args.csv or find_latest_csv(os.getcwd())
    if not csv_path or not os.path.isfile(csv_path):
        print('No camera CSV found. Generate scan output first or pass --csv <file>.')
        sys.exit(1)
    print(f'Loading camera inventory from: {csv_path}')
    print(f'Starting dashboard on http://127.0.0.1:{args.port}/')
    server = CameraDashboardServer(('0.0.0.0', args.port), DashboardHandler, csv_path)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nShutting down.')
        server.shutdown()


if __name__ == '__main__':
    main()
