#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
import time
import webbrowser

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCAN_SCRIPT = os.path.join(SCRIPT_DIR, 'scan_cameras.py')
DASHBOARD_SCRIPT = os.path.join(SCRIPT_DIR, 'camera_dashboard_v2.py')


def parse_args():
    parser = argparse.ArgumentParser(description='Run camera scan pipeline and open the dashboard.')
    parser.add_argument('--target', default='192.168.100.0/24', help='Target subnet or host to scan.')
    parser.add_argument('--ports', default='80,443,554,8000,8080,8554,37777', help='Comma-separated list of ports to scan.')
    parser.add_argument('--output', default=None, help='CSV output filename for scan results.')
    parser.add_argument('--no-nmap', action='store_true', help='Skip nmap and use socket-only scanning.')
    parser.add_argument('--no-rtsp', action='store_true', help='Skip RTSP probing.')
    parser.add_argument('--aggressive', action='store_true', help='Enable aggressive nmap and discovery modes.')
    parser.add_argument('--timeout', type=float, default=1.5, help='Socket probe timeout in seconds.')
    parser.add_argument('--nmap-timeout', type=float, default=240.0, help='Timeout for nmap scan in seconds.')
    parser.add_argument('--workers', type=int, default=100, help='Number of worker threads for socket scanning.')
    parser.add_argument('--dashboard-port', type=int, default=8080, help='Port for the dashboard HTTP server.')
    parser.add_argument('--open-browser', action='store_true', help='Open the dashboard in the default browser after startup.')
    return parser.parse_args()


def run_scan(args):
    output_file = args.output or f'camaras_{time.strftime("%Y%m%d_%H%M%S")}.csv'
    cmd = [sys.executable, SCAN_SCRIPT, '--target', args.target, '--ports', args.ports, '--output', output_file, '--timeout', str(args.timeout), '--nmap-timeout', str(args.nmap_timeout), '--workers', str(args.workers)]
    if args.no_nmap:
        cmd.append('--no-nmap')
    if args.no_rtsp:
        cmd.append('--no-rtsp')
    if args.aggressive:
        cmd.append('--aggressive')

    print(f'[PIPELINE] Running scan: {" ".join(cmd)}')
    result = subprocess.run(cmd, cwd=SCRIPT_DIR)
    if result.returncode != 0:
        raise SystemExit('[PIPELINE] Scan failed, stopping pipeline.')

    if not os.path.isfile(output_file):
        raise SystemExit(f'[PIPELINE] Scan completed but output file was not found: {output_file}')

    return os.path.abspath(output_file)


def start_dashboard(csv_path, port, open_browser):
    cmd = [sys.executable, DASHBOARD_SCRIPT, '--csv', csv_path, '--port', str(port)]
    print(f'[PIPELINE] Starting dashboard: {" ".join(cmd)}')
    proc = subprocess.Popen(cmd, cwd=SCRIPT_DIR)
    time.sleep(2)
    url = f'http://127.0.0.1:{port}/'
    if open_browser:
        print(f'[PIPELINE] Opening browser at {url}')
        try:
            webbrowser.open(url)
        except Exception as exc:
            print(f'[PIPELINE] Unable to open browser: {exc}')
    print(f'[PIPELINE] Dashboard is running at {url}')
    print('[PIPELINE] Press Ctrl+C to stop the dashboard.')
    try:
        proc.wait()
    except KeyboardInterrupt:
        print('\n[PIPELINE] Stopping dashboard...')
        proc.terminate()
        proc.wait()


def main():
    args = parse_args()
    csv_path = run_scan(args)
    start_dashboard(csv_path, args.dashboard_port, args.open_browser)


if __name__ == '__main__':
    main()
