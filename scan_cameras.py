#!/usr/bin/env python3
import argparse
import csv
import concurrent.futures
import datetime
import ipaddress
import os
import re
import socket
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, **kwargs):
        total = kwargs.get("total")
        desc = kwargs.get("desc", "")
        count = 0
        if total is not None:
            print(f"{desc} 0/{total}", end="", flush=True)
        for item in iterable:
            count += 1
            if total is not None:
                print(f"\r{desc} {count}/{total}", end="", flush=True)
            yield item
        if total is not None:
            print()
        else:
            print(f"{desc} done")

DEFAULT_PORTS = [80, 443, 554, 8000, 8080, 8554, 37777]
DEFAULT_RTSP_PATHS = [
    "/Streaming/Channels/101",
    "/Streaming/Channels/102",
    "/live/ch00_0",
    "/live/ch00_1",
    "/live",
    "/h264",
    "/h264/ch0_0",
    "/ch0.h264",
    "/ch0_0.h264",
    "/video1",
    "/media/video1",
    "/11",
    "/12",
    "/1",
    "/stream",
    "/mjpeg",
    "/video",
    "/axis-cgi/mjpeg",
    "/",
]
ADMIN_HINTS = [
    "camera",
    "hikvision",
    "dahua",
    "axis",
    "foscam",
    "vivotek",
    "admin",
    "login",
    "webcam",
    "nvr",
    "surveillance",
    "encoder",
]
HTTP_ADMIN_PATHS = [
    "/admin",
    "/login",
    "/user/login",
    "/cgi-bin/login",
    "/web",
    "/index.html",
]

CSV_FIELDS = [
    "IP",
    "ReverseDNS",
    "Hostname",
    "OpenPorts",
    "NmapName",
    "OSDetails",
    "HTTPTitle",
    "ServerHeader",
    "AdminHint",
    "RTSPURL",
    "Source",
    "Notes",
]


def is_executable(command):
    return shutil_which(command) is not None


def shutil_which(command):
    path = os.environ.get("PATH", "")
    for directory in path.split(os.pathsep):
        candidate = os.path.join(directory, command)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def parse_port_list(port_string):
    ports = []
    for token in re.split(r"[\s,]+", port_string.strip()):
        if not token:
            continue
        try:
            ports.append(int(token))
        except ValueError:
            raise argparse.ArgumentTypeError(f"Invalid port value: {token}")
    if not ports:
        raise argparse.ArgumentTypeError("Port list may not be empty")
    return sorted(set(ports))


def parse_nmap_grepable(output):
    hosts = {}
    for line in output.splitlines():
        if not line.startswith("Host:"):
            continue
        parts = line.split("\t")
        host_info = parts[0]
        ports_info = ""
        if len(parts) > 1:
            for part in parts[1:]:
                if part.startswith("Ports:"):
                    ports_info = part[len("Ports:"):].strip()
                    break
        match = re.match(r"Host: (?P<ip>\S+)(?: \((?P<hostname>[^)]*)\))?", host_info)
        if not match:
            continue
        ip = match.group("ip")
        hostname = match.group("hostname") or ""
        open_ports = []
        service_map = {}
        for port_entry in [p.strip() for p in ports_info.split(",") if p.strip()]:
            fields = port_entry.split("/")
            if len(fields) < 3:
                continue
            try:
                port_num = int(fields[0])
            except ValueError:
                continue
            state = fields[1]
            service = fields[4] if len(fields) > 4 else ""
            info = fields[6] if len(fields) > 6 else ""
            if state == "open":
                open_ports.append(port_num)
                service_map[port_num] = " ".join([service, info]).strip()
        hosts[ip] = {
            "hostname": hostname,
            "open_ports": sorted(open_ports),
            "service_map": service_map,
            "os": "",
            "nmap_name": hostname,
        }
    return hosts


def parse_nmap_xml(output):
    hosts = {}
    try:
        root = ET.fromstring(output)
    except ET.ParseError:
        return hosts
    for host in root.findall("host"):
        status = host.find("status")
        if status is not None and status.get("state") != "up":
            continue
        ip = ""
        for addr in host.findall("address"):
            if addr.get("addrtype") == "ipv4":
                ip = addr.get("addr")
                break
        if not ip:
            continue
        hostname = ""
        hostnames = host.find("hostnames")
        if hostnames is not None:
            hostname_el = hostnames.find("hostname")
            if hostname_el is not None:
                hostname = hostname_el.get("name", "")
        open_ports = []
        service_map = {}
        ports_el = host.find("ports")
        if ports_el is not None:
            for port in ports_el.findall("port"):
                state_el = port.find("state")
                if state_el is None or state_el.get("state") != "open":
                    continue
                portid = port.get("portid")
                try:
                    port_num = int(portid)
                except (TypeError, ValueError):
                    continue
                service_el = port.find("service")
                service_name = service_el.get("name", "") if service_el is not None else ""
                product = service_el.get("product", "") if service_el is not None else ""
                version = service_el.get("version", "") if service_el is not None else ""
                info = " ".join([service_name, product, version]).strip()
                open_ports.append(port_num)
                service_map[port_num] = info
        os_details = ""
        os_el = host.find("os")
        if os_el is not None:
            match = os_el.find("osmatch")
            if match is not None:
                os_details = match.get("name", "")
        hosts[ip] = {
            "hostname": hostname,
            "open_ports": sorted(open_ports),
            "service_map": service_map,
            "os": os_details,
            "nmap_name": hostname,
        }
    return hosts


def run_nmap(target, ports, timeout=240, service_scan=True, aggressive=False):
    if not is_executable("nmap"):
        return None
    port_arg = ",".join(str(p) for p in ports)
    base_args = [
        "nmap",
        "-Pn",
        "-sS",
        "--open",
        "-T4",
        "--min-rate",
        "500",
        "--max-retries",
        "1",
        "-p",
        port_arg,
    ]
    if aggressive:
        args = base_args + ["-A", "--version-all", "-oX", "-", target]
    else:
        args = base_args + ["-oG", "-", target]
        if service_scan:
            args.insert(4, "-sV")
    scan_type = "aggressive scan" if aggressive else ("service scan" if service_scan else "fast scan")
    print(f"[INFO] Running nmap {scan_type} on {target}...")
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0 and not result.stdout:
            print(f"[WARN] nmap failed: {result.stderr.strip()}", file=sys.stderr)
            return None
        if aggressive:
            hosts = parse_nmap_xml(result.stdout)
        else:
            hosts = parse_nmap_grepable(result.stdout)
        if not hosts and service_scan and not aggressive:
            print("[INFO] No hosts found with service scan, retrying with fast scan...")
            return run_nmap(target, ports, timeout=timeout, service_scan=False, aggressive=False)
        if not hosts and aggressive:
            print("[INFO] Aggressive nmap scan returned no hosts, falling back to service scan...")
            return run_nmap(target, ports, timeout=timeout, service_scan=service_scan, aggressive=False)
        return hosts
    except subprocess.TimeoutExpired:
        print(f"[WARN] nmap execution timed out after {timeout} seconds", file=sys.stderr)
        if aggressive:
            print("[INFO] Retrying nmap without aggressive flags...")
            return run_nmap(target, ports, timeout=timeout, service_scan=service_scan, aggressive=False)
        if service_scan:
            print("[INFO] Retrying nmap without service/version detection...")
            return run_nmap(target, ports, timeout=timeout, service_scan=False, aggressive=False)
        return None
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"[WARN] nmap execution failed: {exc}", file=sys.stderr)
        return None


def check_port(ip, port, timeout=1.5):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (socket.timeout, OSError):
        return False


def scan_host_ports(ip, ports, timeout=1.5):
    open_ports = []
    for port in ports:
        if check_port(ip, port, timeout=timeout):
            open_ports.append(port)
    return sorted(open_ports)


def resolve_dns(ip):
    hostname = ""
    reverse_dns = ""
    try:
        hostname = socket.getfqdn(ip) or ""
    except Exception:
        hostname = ""
    try:
        reverse_dns = socket.gethostbyaddr(ip)[0]
    except Exception:
        reverse_dns = ""
    return reverse_dns, hostname


def fetch_http_info(ip, port, timeout=5):
    scheme = "https" if port == 443 else "http"
    url = f"{scheme}://{ip}:{port}/"
    request = urllib.request.Request(url, headers={"User-Agent": "camera-scanner/1.0"})
    context = None
    if scheme == "https":
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    title = ""
    server = ""
    raw = ""
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            server = response.getheader("Server", "").strip()
            raw = response.read(16384)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="ignore")
            match = re.search(r"<title>(.*?)</title>", raw, re.IGNORECASE | re.DOTALL)
            if match:
                title = match.group(1).strip()
    except Exception as exc:
        return "", "", f"HTTP probe failed: {exc}"
    return title, server, raw


def probe_http_admin_paths(ip, ports, timeout=4):
    if 80 not in ports and 443 not in ports:
        return "", ""
    scheme = "https" if 443 in ports else "http"
    port = 443 if 443 in ports else 80
    for path in HTTP_ADMIN_PATHS:
        url = f"{scheme}://{ip}:{port}{path}"
        request = urllib.request.Request(url, headers={"User-Agent": "camera-scanner/1.0"})
        context = None
        if scheme == "https":
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                status = response.getcode()
                if status == 200:
                    return url, f"admin-path {path}"
        except Exception:
            continue
    return "", ""


def guess_admin_hint(title, server, hostname, open_ports):
    lower_text = " ".join([title or "", server or "", hostname or ""]).lower()
    for hint in ADMIN_HINTS:
        if hint in lower_text:
            return f"Likely admin/camera interface ({hint})"
    if 80 in open_ports or 443 in open_ports:
        return "HTTP interface available"
    return ""


def probe_rtsp_path(ip, path, timeout=8):
    rtsp_url = f"rtsp://{ip}:554{path}"
    if is_executable("ffprobe"):
        args = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            rtsp_url,
        ]
        try:
            result = subprocess.run(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )
            output = (result.stdout + result.stderr).lower()
            if "video" in output and result.returncode == 0:
                return rtsp_url, "ffprobe"
        except (subprocess.SubprocessError, OSError):
            pass
    try:
        with socket.create_connection((ip, 554), timeout=timeout) as s:
            request = (
                f"OPTIONS {rtsp_url} RTSP/1.0\r\n"
                f"CSeq: 1\r\n"
                f"User-Agent: camera-scanner\r\n\r\n"
            )
            s.sendall(request.encode())
            response = s.recv(4096).decode(errors="ignore").lower()
            if "rtsp/1.0 200" in response or "public:" in response:
                return rtsp_url, "rtsp-options"
    except Exception:
        pass
    return None, None


def find_rtsp_stream(ip, timeout=8):
    for path in DEFAULT_RTSP_PATHS:
        rtsp_url, source = probe_rtsp_path(ip, path, timeout=timeout)
        if rtsp_url:
            return rtsp_url, source
    return "", ""


def collect_host_data(ip, entry, ports, rtsp_enabled=True):
    open_ports = entry.get("open_ports", []) if entry else []
    nmap_name = entry.get("nmap_name", "") if entry else ""
    os_details = entry.get("os", "") if entry else ""
    service_map = entry.get("service_map", {}) if entry else {}
    if not open_ports:
        open_ports = scan_host_ports(ip, ports)
    reverse_dns, hostname = resolve_dns(ip)
    http_title = ""
    server_header = ""
    notes = ""
    http_body = ""
    if 80 in open_ports or 443 in open_ports:
        title, server, body = fetch_http_info(ip, 443 if 443 in open_ports else 80)
        http_title = title
        server_header = server
        http_body = body
        if not title and not server:
            notes = "HTTP probe did not return title or server header"
    admin_path_url = ""
    admin_path_source = ""
    if http_title or server_header:
        admin_path_url, admin_path_source = probe_http_admin_paths(ip, open_ports)
        if admin_path_url and notes:
            notes += f"; {admin_path_source}"
        elif admin_path_source:
            notes = admin_path_source
    admin_hint = guess_admin_hint(http_title, server_header, hostname or nmap_name, open_ports)
    if admin_path_source:
        admin_hint = f"Admin path found ({admin_path_source})"
    rtsp_url = ""
    rtsp_source = ""
    if rtsp_enabled and 554 in open_ports:
        rtsp_url, rtsp_source = find_rtsp_stream(ip)
    source = rtsp_source or server_header or nmap_name or hostname or ""
    if not source and service_map:
        source = "; ".join(f"{p}:{v}" for p, v in service_map.items())
    return {
        "IP": ip,
        "ReverseDNS": reverse_dns,
        "Hostname": hostname,
        "OpenPorts": ",".join(str(p) for p in open_ports),
        "NmapName": nmap_name,
        "OSDetails": os_details,
        "HTTPTitle": http_title,
        "ServerHeader": server_header,
        "AdminHint": admin_hint,
        "RTSPURL": rtsp_url,
        "Source": source,
        "Notes": notes,
    }


def write_results(output_file, rows):
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def parse_arguments():
    parser = argparse.ArgumentParser(description="Camera/RTSP recon scanner with nmap and metadata discovery.")
    parser.add_argument("--target", default="192.168.100.0/24", help="Target subnet or host to scan. Example: 192.168.100.0/24")
    parser.add_argument("--ports", default=','.join(str(p) for p in DEFAULT_PORTS), help="Comma-separated list of ports to scan.")
    parser.add_argument("--output", default=None, help="CSV output filename.")
    parser.add_argument("--no-nmap", action="store_true", help="Use socket-only scanning and skip nmap.")
    parser.add_argument("--no-rtsp", action="store_true", help="Skip RTSP probing even if port 554 is open.")
    parser.add_argument("--aggressive", action="store_true", help="Use aggressive nmap scanning and deeper HTTP/RTSP probing.")
    parser.add_argument("--timeout", type=float, default=1.5, help="Socket timeout in seconds for port probes.")
    parser.add_argument("--nmap-timeout", type=float, default=240.0, help="Timeout in seconds for the nmap scan.")
    parser.add_argument("--workers", type=int, default=100, help="Number of parallel worker threads.")
    return parser.parse_args()


def main():
    args = parse_arguments()
    ports = parse_port_list(args.ports)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = args.output or f"camaras_{timestamp}.csv"
    target = args.target
    rtsp_enabled = not args.no_rtsp
    aggressive = args.aggressive
    use_nmap = not args.no_nmap and is_executable("nmap")

    print(f"[+] Target: {target}")
    print(f"[+] Ports: {ports}")
    print(f"[+] RTSP probing: {'enabled' if rtsp_enabled else 'disabled'}")
    print(f"[+] Aggressive mode: {'enabled' if aggressive else 'disabled'}")
    print(f"[+] nmap: {'enabled' if use_nmap else 'disabled or unavailable'}")

    nmap_data = None
    if use_nmap:
        nmap_data = run_nmap(target, ports, timeout=args.nmap_timeout, aggressive=aggressive)
        if nmap_data is None:
            print("[!] nmap scan failed or is unavailable, falling back to socket scan.")

    network = ipaddress.ip_network(target, strict=False)
    hosts = list(network.hosts())
    results = []

    if nmap_data is not None:
        host_ips = sorted(nmap_data.keys(), key=lambda ip: ipaddress.ip_address(ip))
        if not host_ips:
            print("[+] nmap scan completed, no hosts with open ports found.")
        for ip in tqdm(host_ips, desc="Gathering metadata", total=len(host_ips)):
            entry = nmap_data.get(ip, {})
            results.append(collect_host_data(ip, entry, ports, rtsp_enabled=rtsp_enabled))
    else:
        print(f"[+] Scanning {len(hosts)} hosts with socket probes...")
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(10, args.workers))
        futures = {executor.submit(scan_host_ports, str(host), ports, args.timeout): host for host in hosts}
        try:
            for future in tqdm(concurrent.futures.as_completed(futures), total=len(futures), desc="Scanning hosts"):
                host = futures[future]
                try:
                    open_ports = future.result()
                except Exception as exc:
                    print(f"[ERROR] Host {host} scan failed: {exc}", file=sys.stderr)
                    continue
                if not open_ports:
                    continue
                entry = {
                    "hostname": "",
                    "open_ports": open_ports,
                    "service_map": {},
                    "os": "",
                    "nmap_name": "",
                }
                results.append(collect_host_data(str(host), entry, ports, rtsp_enabled=rtsp_enabled))
        except KeyboardInterrupt:
            print("\n[!] Interrupted by user, cancelling pending tasks...", file=sys.stderr)
        finally:
            executor.shutdown(wait=True)

    if results:
        write_results(output_file, results)
        print(f"\n[+] Scan complete. Results saved to: {output_file}")
        print(f"[+] Hosts found: {len(results)}")
        for row in results:
            print(f"[CAMERA?] {row['IP']} ports={row['OpenPorts']} admin={row['AdminHint']} rtsp={row['RTSPURL'] or 'none'}")
    else:
        print("\n[+] Scan complete. No hosts with the selected open ports were found.")


if __name__ == "__main__":
    main()
