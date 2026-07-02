#!/usr/bin/env python3
import json
import hashlib
import os
import re
import shutil
import socket
import subprocess
import time
import mimetypes
import secrets
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


HOST = "127.0.0.1"
PORT = int(os.environ.get("LINGYUE_API_PORT", "8088"))
STATIC_DIR = os.environ.get("LINGYUE_STATIC_DIR")
CONFIG_DIR = Path(os.environ.get("LINGYUE_CONFIG_DIR") or (Path(STATIC_DIR) / ".lingyue-state" if STATIC_DIR else "/var/lib/lingyue"))
SETUP_FILE = CONFIG_DIR / "setup.json"


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def run_command(args):
    if not shutil.which(args[0]):
        return ""

    try:
        result = subprocess.run(args, check=False, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return ""

    return result.stdout.strip()


def load_setup():
    try:
        data = json.loads(SETUP_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}

    return {
        "completed": bool(data.get("completed")),
        "device_name": data.get("device_name") or socket.gethostname(),
        "admin_username": data.get("admin_username") or "admin",
        "network_mode": data.get("network_mode") or "dhcp",
        "enable_smb": bool(data.get("enable_smb", True)),
        "completed_at": data.get("completed_at"),
    }


def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 180000)
    return {"salt": salt, "hash": digest.hex(), "algorithm": "pbkdf2_sha256"}


def validate_setup(payload):
    errors = {}
    device_name = str(payload.get("device_name", "")).strip()
    admin_username = str(payload.get("admin_username", "")).strip()
    admin_password = str(payload.get("admin_password", ""))
    network_mode = str(payload.get("network_mode", "dhcp")).strip()

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{1,30}[A-Za-z0-9]", device_name):
        errors["device_name"] = "设备名需为 3-32 位字母、数字或短横线。"
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{2,31}", admin_username):
        errors["admin_username"] = "管理员名需为 3-32 位小写字母、数字、下划线或短横线。"
    if len(admin_password) < 8:
        errors["admin_password"] = "密码至少需要 8 位。"
    if network_mode not in {"dhcp", "static"}:
        errors["network_mode"] = "网络模式无效。"

    return errors


def complete_setup(payload):
    errors = validate_setup(payload)
    if errors:
        return None, errors

    data = {
        "completed": True,
        "completed_at": utc_now(),
        "device_name": str(payload["device_name"]).strip(),
        "admin_username": str(payload["admin_username"]).strip(),
        "network_mode": str(payload.get("network_mode", "dhcp")).strip(),
        "enable_smb": bool(payload.get("enable_smb", True)),
        "password": password_hash(str(payload["admin_password"])),
    }

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SETUP_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(SETUP_FILE, 0o600)
    except OSError:
        pass

    return load_setup(), None


def format_bytes(value):
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1

    if size >= 10 or index == 0:
        return f"{size:.0f} {units[index]}"
    return f"{size:.1f} {units[index]}"


def memory_info():
    values = {}
    for line in read_text("/proc/meminfo").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            values[parts[0].rstrip(":")] = int(parts[1]) * 1024

    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    used = max(total - available, 0)
    percent = round((used / total) * 100) if total else 0

    return {
        "total": total,
        "used": used,
        "available": available,
        "percent": percent,
        "label": f"{format_bytes(used)} / {format_bytes(total)}",
    }


def cpu_model():
    for line in read_text("/proc/cpuinfo").splitlines():
        if line.lower().startswith("model name"):
            return line.split(":", 1)[1].strip()
    return os.uname().machine


def uptime_label():
    raw = read_text("/proc/uptime").split()
    if not raw:
        return "未知"

    seconds = int(float(raw[0]))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60

    if days:
        return f"{days} 天 {hours} 小时"
    if hours:
        return f"{hours} 小时 {minutes} 分钟"
    return f"{minutes} 分钟"


def block_devices():
    output = run_command(["lsblk", "-b", "-J", "-o", "NAME,TYPE,SIZE,MODEL,TRAN,MOUNTPOINT,FSTYPE"])
    if not output:
        return []

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []

    disks = []
    for item in data.get("blockdevices", []):
        if item.get("type") != "disk":
            continue

        disks.append({
            "name": item.get("name", "disk"),
            "size": int(item.get("size") or 0),
            "size_label": format_bytes(item.get("size") or 0),
            "model": item.get("model") or "未知型号",
            "transport": item.get("tran") or "system",
            "status": "online",
        })

    return disks


def storage_info():
    usage = shutil.disk_usage("/")
    disks = block_devices()
    disk_count = len(disks)

    return {
        "root": {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": round((usage.used / usage.total) * 100) if usage.total else 0,
            "total_label": format_bytes(usage.total),
            "used_label": format_bytes(usage.used),
            "free_label": format_bytes(usage.free),
        },
        "disks": disks,
        "disk_count": disk_count,
        "disk_health": f"{disk_count} / {disk_count}" if disk_count else "0 / 0",
    }


def network_info():
    interfaces = []
    base = Path("/sys/class/net")
    for item in sorted(base.iterdir()) if base.exists() else []:
        name = item.name
        if name == "lo":
            continue

        state = read_text(item / "operstate") or "unknown"
        speed = read_text(item / "speed")
        mac = read_text(item / "address")
        speed_label = f"{speed} MbE" if speed.isdigit() and int(speed) > 0 else "未知速率"
        interfaces.append({"name": name, "state": state, "speed": speed_label, "mac": mac})

    ips = []
    hostname_ips = run_command(["hostname", "-I"])
    if hostname_ips:
        ips = [value for value in hostname_ips.split() if not value.startswith("127.")]
    if not ips:
        try:
            ips = [socket.gethostbyname(socket.gethostname())]
        except socket.gaierror:
            ips = []

    return {
        "hostname": socket.gethostname(),
        "primary_ip": ips[0] if ips else "127.0.0.1",
        "interfaces": interfaces,
    }


def service_status():
    services = [
        ("nginx", "Web 控制台"),
        ("smbd", "SMB"),
        ("nfs-server", "NFS"),
        ("docker", "Docker"),
    ]
    result = []
    active_count = 0

    for unit, label in services:
        status = run_command(["systemctl", "is-active", unit]) or "unknown"
        active = status == "active"
        if active:
            active_count += 1
        result.append({"unit": unit, "label": label, "status": status, "active": active})

    return {"items": result, "active_count": active_count, "total": len(result)}


def overview():
    storage = storage_info()
    network = network_info()
    services = service_status()

    return {
        "generated_at": int(time.time()),
        "system": {
            "name": network["hostname"],
            "kernel": os.uname().release,
            "architecture": os.uname().machine,
            "cpu": cpu_model(),
            "uptime": uptime_label(),
            "memory": memory_info(),
        },
        "storage": storage,
        "network": network,
        "services": services,
        "health": {
            "state": "healthy" if services["active_count"] >= 1 else "attention",
            "label": "运行正常" if services["active_count"] >= 1 else "需要检查",
        },
        "setup": load_setup(),
    }


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors_headers()
        self.end_headers()

    def do_HEAD(self):
        parsed = urlparse(self.path)
        if STATIC_DIR and self.static_target(parsed.path):
            target = self.static_target(parsed.path)
            content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(target.stat().st_size))
            self.end_headers()
            return

        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self.send_json({"ok": True})
            return
        if parsed.path == "/api/setup/state":
            self.send_json(load_setup())
            return
        if parsed.path == "/api/system/overview":
            self.send_json(overview())
            return
        if STATIC_DIR:
            self.send_static(parsed.path)
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/setup/complete":
            self.send_response(404)
            self.end_headers()
            return

        try:
            length = min(int(self.headers.get("Content-Length", "0")), 16384)
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, json.JSONDecodeError):
            self.send_json({"ok": False, "error": "请求格式无效。"}, status=400)
            return

        setup, errors = complete_setup(payload)
        if errors:
            self.send_json({"ok": False, "errors": errors}, status=400)
            return

        self.send_json({"ok": True, "setup": setup})

    def send_static(self, request_path):
        target = self.static_target(request_path)
        if not target:
            self.send_response(404)
            self.end_headers()
            return

        content = target.read_bytes()
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def static_target(self, request_path):
        base = Path(STATIC_DIR).resolve()
        relative = request_path.lstrip("/") or "index.html"
        target = (base / relative).resolve()

        try:
            target.relative_to(base)
        except ValueError:
            return None

        return target if target.is_file() else None

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_cors_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Lingyue API listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
