#!/usr/bin/env python3
import json
import hashlib
import hmac
import os
import grp
import re
import shutil
import socket
import subprocess
import time
import mimetypes
import secrets
from http.cookies import SimpleCookie
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


HOST = "127.0.0.1"
PORT = int(os.environ.get("LINGYUE_API_PORT", "8088"))
STATIC_DIR = os.environ.get("LINGYUE_STATIC_DIR")
PREVIEW_MODE = bool(STATIC_DIR)
CONFIG_DIR = Path(os.environ.get("LINGYUE_CONFIG_DIR") or (Path(STATIC_DIR) / ".lingyue-state" if STATIC_DIR else "/var/lib/lingyue"))
SETUP_FILE = CONFIG_DIR / "setup.json"
STORAGE_FILE = CONFIG_DIR / "storage.json"
INSTALL_FILE = CONFIG_DIR / "install.json"
SESSION_FILE = CONFIG_DIR / "sessions.json"
INSTALL_MANIFEST_DIR = CONFIG_DIR / "install-manifests"
INSTALL_RUN_DIR = CONFIG_DIR / "install-runs"
INSTALL_SCRIPT = Path(os.environ.get("LINGYUE_INSTALL_SCRIPT", "/usr/local/sbin/lingyue-install-disk"))
INSTALL_EXECUTION_ENABLED = os.environ.get("LINGYUE_INSTALL_EXECUTE") == "1"
STORAGE_MANIFEST_DIR = CONFIG_DIR / "storage-manifests"
STORAGE_RUN_DIR = CONFIG_DIR / "storage-runs"
STORAGE_POOL_SCRIPT = Path(os.environ.get("LINGYUE_STORAGE_POOL_SCRIPT", "/usr/local/sbin/lingyue-create-pool"))
STORAGE_EXECUTION_ENABLED = os.environ.get("LINGYUE_STORAGE_EXECUTE") == "1"
INSTALL_MIN_SIZE = 16 * 1024**3
SESSION_COOKIE = "lyos_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
SHARE_ROOT = Path(os.environ.get("LINGYUE_SHARE_ROOT") or (CONFIG_DIR / "shares" if STATIC_DIR else "/srv/lingyue/shares"))
SAMBA_MAIN = Path(os.environ.get("LINGYUE_SAMBA_MAIN") or (CONFIG_DIR / "samba/smb.conf" if STATIC_DIR else "/etc/samba/smb.conf"))
SAMBA_SNIPPET = Path(os.environ.get("LINGYUE_SAMBA_SNIPPET") or (CONFIG_DIR / "samba/lingyue-shares.conf" if STATIC_DIR else "/etc/samba/smb.conf.d/lingyue-shares.conf"))
SAMBA_GROUP = os.environ.get("LINGYUE_SAMBA_GROUP", "lingyue-users")


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


def run_command_result(args, input_text=None):
    if not shutil.which(args[0]):
        return 127, "", f"{args[0]} not found"

    try:
        result = subprocess.run(args, input=input_text, check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as error:
        return 1, "", str(error)

    return result.returncode, result.stdout.strip(), result.stderr.strip()


def load_storage_state():
    try:
        data = json.loads(STORAGE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}

    return {
        "pool_plans": data.get("pool_plans") if isinstance(data.get("pool_plans"), list) else [],
        "shares": data.get("shares") if isinstance(data.get("shares"), list) else [],
        "events": data.get("events") if isinstance(data.get("events"), list) else [],
        "runs": data.get("runs") if isinstance(data.get("runs"), list) else [],
        "manifests": data.get("manifests") if isinstance(data.get("manifests"), list) else [],
    }


def save_storage_state(data):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STORAGE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(STORAGE_FILE, 0o600)
    except OSError:
        pass


def append_storage_event(state, level, message):
    events = state.get("events") if isinstance(state.get("events"), list) else []
    events.append({"at": utc_now(), "level": level, "message": message})
    state["events"] = events[-20:]


def load_install_state():
    try:
        data = json.loads(INSTALL_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}

    return {
        "plans": data.get("plans") if isinstance(data.get("plans"), list) else [],
        "events": data.get("events") if isinstance(data.get("events"), list) else [],
        "runs": data.get("runs") if isinstance(data.get("runs"), list) else [],
        "manifests": data.get("manifests") if isinstance(data.get("manifests"), list) else [],
    }


def save_install_state(data):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    INSTALL_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(INSTALL_FILE, 0o600)
    except OSError:
        pass


def append_install_event(state, level, message):
    events = state.get("events") if isinstance(state.get("events"), list) else []
    events.append({"at": utc_now(), "level": level, "message": message})
    state["events"] = events[-20:]


def write_json_file(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def load_session_state():
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}

    sessions = data.get("sessions") if isinstance(data.get("sessions"), dict) else {}
    now = time.time()
    active = {
        token: session for token, session in sessions.items()
        if isinstance(session, dict) and float(session.get("expires_at", 0) or 0) > now
    }
    if active != sessions:
        save_session_state({"sessions": active})
    return {"sessions": active}


def save_session_state(data):
    write_json_file(SESSION_FILE, data)


def load_setup_private():
    try:
        data = json.loads(SETUP_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return data


def share_name_to_slug(name):
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip()).strip("-").lower()
    return slug or "share"


def default_share():
    return {
        "id": "share-public",
        "name": "Public",
        "path": str(SHARE_ROOT / "public"),
        "protocol": "SMB",
        "access": "authenticated_rw",
        "access_label": "认证用户读写",
        "status": "planned",
        "status_label": "待创建",
        "created_at": None,
    }


def normalized_shares():
    state = load_storage_state()
    shares = state["shares"]
    return shares if shares else [default_share()]


def samba_status():
    status = run_command(["systemctl", "is-active", "smbd"]) or "unknown"
    return {
        "installed": bool(shutil.which("smbd") or shutil.which("samba")),
        "active": status == "active",
        "status": status,
        "preview": PREVIEW_MODE,
        "config_path": str(SAMBA_SNIPPET),
    }


def account_status():
    setup = load_setup()
    username = setup["admin_username"]
    linux_user = user_exists(username) if setup["completed"] else False
    samba_user = samba_user_exists(username) if setup["completed"] else False
    group_ready = group_exists(SAMBA_GROUP) if setup["completed"] else False
    configured = bool(setup["completed"] and (PREVIEW_MODE or (linux_user and samba_user and group_ready)))
    users = []
    if setup["completed"]:
        users.append({
            "username": username,
            "display_name": "系统管理员",
            "role": "admin",
            "role_label": "管理员",
            "smb_enabled": bool(PREVIEW_MODE or samba_user),
            "status": "active" if configured else "pending",
            "status_label": "可用" if configured else "等待系统账号同步",
        })

    return {
        "admin_username": username,
        "group": SAMBA_GROUP,
        "completed": setup["completed"],
        "linux_user": linux_user,
        "samba_user": samba_user,
        "group_ready": group_ready,
        "configured": configured,
        "preview": PREVIEW_MODE,
        "users": users,
        "roles": [
            {"id": "admin", "label": "管理员", "description": "管理系统、存储池、共享和安装器。"},
            {"id": "member", "label": "成员", "description": "访问被授权的共享文件夹。"},
            {"id": "guest", "label": "访客", "description": "用于临时或只读共享访问。"},
        ],
    }


def shares_overview():
    return {
        "generated_at": int(time.time()),
        "root": str(SHARE_ROOT),
        "shares": normalized_shares(),
        "samba": samba_status(),
        "accounts": account_status(),
    }


def ensure_samba_include():
    SAMBA_SNIPPET.parent.mkdir(parents=True, exist_ok=True)
    if not SAMBA_MAIN.exists():
        SAMBA_MAIN.parent.mkdir(parents=True, exist_ok=True)
        SAMBA_MAIN.write_text("[global]\n   server role = standalone server\n   map to guest = Bad User\n", encoding="utf-8")

    content = SAMBA_MAIN.read_text(encoding="utf-8", errors="ignore")
    include_line = f"include = {SAMBA_SNIPPET}"
    if include_line not in content:
        with SAMBA_MAIN.open("a", encoding="utf-8") as handle:
            if content and not content.endswith("\n"):
                handle.write("\n")
            handle.write(f"\n# Lingyue OS managed shares\n{include_line}\n")


def write_samba_shares(shares):
    ensure_samba_include()
    blocks = ["# Generated by Lingyue OS. Do not edit this block by hand."]
    for share in shares:
        if share.get("status") not in {"active", "configured"}:
            continue

        access = share.get("access") or "authenticated_rw"
        access_lines = [
            "   guest ok = no",
            f"   valid users = @{SAMBA_GROUP}",
            f"   write list = @{SAMBA_GROUP}",
            "   create mask = 0660",
            "   directory mask = 0770",
            f"   force group = {SAMBA_GROUP}",
        ]
        if access == "guest_rw":
            access_lines = [
                "   guest ok = yes",
                "   create mask = 0664",
                "   directory mask = 0775",
                "   force user = nobody",
                "   force group = nogroup",
            ]

        blocks.append(
            "\n".join([
                f"[{share['name']}]",
                f"   path = {share['path']}",
                "   browseable = yes",
                "   read only = no",
                *access_lines,
            ])
        )

    SAMBA_SNIPPET.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def reload_samba():
    if PREVIEW_MODE:
        return False, "本地预览已写入模拟配置，ISO 环境会重启 SMB 服务。"

    code, _, error = run_command_result(["testparm", "-s"])
    if code not in {0, 127}:
        return False, error or "Samba 配置校验失败。"

    code, _, error = run_command_result(["systemctl", "restart", "smbd"])
    if code not in {0, 127}:
        return False, error or "SMB 服务重启失败。"
    return True, None


def user_exists(username):
    code, _, _ = run_command_result(["id", "-u", username])
    return code == 0


def group_exists(group_name):
    code, _, _ = run_command_result(["getent", "group", group_name])
    return code == 0


def samba_user_exists(username):
    code, output, _ = run_command_result(["pdbedit", "-L", "-u", username])
    if code != 0:
        return False
    return any(line.split(":", 1)[0] == username for line in output.splitlines())


def ensure_samba_account(username, password):
    if PREVIEW_MODE:
        return False, "本地预览已记录管理员账号，ISO 环境会创建系统用户和 SMB 密码。"

    if not group_exists(SAMBA_GROUP):
        code, _, error = run_command_result(["groupadd", "--system", SAMBA_GROUP])
        if code != 0:
            return False, error or "共享用户组创建失败。"

    if user_exists(username):
        code, _, error = run_command_result(["usermod", "-aG", SAMBA_GROUP, username])
    else:
        code, _, error = run_command_result(["useradd", "-m", "-s", "/bin/bash", "-G", SAMBA_GROUP, username])
    if code != 0:
        return False, error or "管理员系统账号创建失败。"

    code, _, error = run_command_result(["chpasswd"], input_text=f"{username}:{password}\n")
    if code != 0:
        return False, error or "管理员系统密码写入失败。"

    smb_password = f"{password}\n{password}\n"
    code, _, error = run_command_result(["smbpasswd", "-s", "-a", username], input_text=smb_password)
    if code != 0:
        return False, error or "SMB 密码写入失败。"

    code, _, error = run_command_result(["smbpasswd", "-e", username])
    if code not in {0, 127}:
        return False, error or "SMB 账号启用失败。"

    return True, None


def set_share_permissions(share_path, access):
    mode = 0o775 if access == "guest_rw" else 0o770
    try:
        os.chmod(share_path, mode)
    except OSError:
        pass

    if PREVIEW_MODE or access == "guest_rw":
        return

    try:
        gid = grp.getgrnam(SAMBA_GROUP).gr_gid
        os.chown(share_path, -1, gid)
    except (KeyError, OSError):
        pass


def validate_share_payload(payload):
    errors = {}
    name = str(payload.get("name") or "Public").strip()
    access = str(payload.get("access") or "authenticated_rw").strip()

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,30}[A-Za-z0-9]", name):
        errors["name"] = "共享名称需为 3-32 位字母、数字、下划线或短横线。"
    if access not in {"authenticated_rw", "guest_rw"}:
        errors["access"] = "当前 Alpha 版本支持认证读写或访客读写共享。"
    if access == "authenticated_rw" and not load_setup()["completed"]:
        errors["access"] = "请先完成初始化管理员设置，再创建认证共享。"
    if access == "authenticated_rw" and not PREVIEW_MODE:
        accounts = account_status()
        if accounts["completed"] and not accounts["configured"]:
            errors["access"] = "管理员 SMB 账号尚未配置成功，请重新完成初始化或检查 Samba 服务。"

    return errors


def create_share(payload):
    errors = validate_share_payload(payload)
    if errors:
        return None, errors

    requested_name = str(payload.get("name") or "Public").strip()
    access = str(payload.get("access") or "authenticated_rw").strip()
    folder_name = share_name_to_slug(requested_name)
    share_path = (SHARE_ROOT / folder_name).resolve()
    root = SHARE_ROOT.resolve()
    try:
        share_path.relative_to(root)
    except ValueError:
        return None, {"path": "共享路径无效。"}

    state = load_storage_state()
    shares = [share for share in state["shares"] if share.get("name") != requested_name]
    share = {
        "id": f"share-{folder_name}",
        "name": requested_name,
        "path": str(share_path),
        "protocol": "SMB",
        "access": access,
        "access_label": "访客读写" if access == "guest_rw" else "认证用户读写",
        "status": "active",
        "status_label": "已共享",
        "created_at": utc_now(),
    }

    SHARE_ROOT.mkdir(parents=True, exist_ok=True)
    share_path.mkdir(parents=True, exist_ok=True)
    set_share_permissions(share_path, access)

    shares.append(share)
    state["shares"] = shares
    write_samba_shares(shares)
    ok, error = reload_samba()
    if not ok:
        share["status"] = "configured"
        share["status_label"] = "配置已写入"

    save_storage_state(state)
    return {**share, "service_warning": error}, None


def load_setup():
    data = load_setup_private()

    return {
        "completed": bool(data.get("completed")),
        "device_name": data.get("device_name") or socket.gethostname(),
        "admin_username": data.get("admin_username") or "admin",
        "network_mode": data.get("network_mode") or "dhcp",
        "enable_smb": bool(data.get("enable_smb", True)),
        "completed_at": data.get("completed_at"),
        "account_configured": bool(data.get("account_configured")),
        "account_warning": data.get("account_warning"),
    }


def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 180000)
    return {"salt": salt, "hash": digest.hex(), "algorithm": "pbkdf2_sha256"}


def verify_password(password, stored):
    if not isinstance(stored, dict) or stored.get("algorithm") != "pbkdf2_sha256":
        return False
    salt = str(stored.get("salt") or "")
    expected = str(stored.get("hash") or "")
    if not salt or not expected:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 180000).hex()
    return hmac.compare_digest(digest, expected)


def create_session(username):
    token = secrets.token_urlsafe(32)
    now = time.time()
    state = load_session_state()
    state["sessions"][token] = {
        "username": username,
        "created_at": now,
        "expires_at": now + SESSION_TTL_SECONDS,
    }
    save_session_state(state)
    return token, state["sessions"][token]


def destroy_session(token):
    if not token:
        return
    state = load_session_state()
    if token in state["sessions"]:
        del state["sessions"][token]
        save_session_state(state)


def session_from_token(token):
    if not token:
        return None
    session = load_session_state()["sessions"].get(token)
    if not session:
        return None
    return {"authenticated": True, "username": session.get("username"), "expires_at": session.get("expires_at")}


def login(payload):
    setup = load_setup_private()
    if not setup.get("completed"):
        return None, {"setup": "请先完成初始化。"}

    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    if username != setup.get("admin_username") or not verify_password(password, setup.get("password")):
        return None, {"login": "管理员账号或密码不正确。"}

    token, session = create_session(username)
    return {"token": token, "session": {"authenticated": True, "username": username, "expires_at": session["expires_at"]}}, None


def public_session_payload(session=None):
    setup = load_setup()
    return {
        "authenticated": bool(session),
        "username": session.get("username") if session else None,
        "expires_at": session.get("expires_at") if session else None,
        "setup_completed": setup["completed"],
        "admin_username": setup["admin_username"],
    }


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

    admin_username = str(payload["admin_username"]).strip()
    admin_password = str(payload["admin_password"])
    account_ok, account_warning = ensure_samba_account(admin_username, admin_password)

    data = {
        "completed": True,
        "completed_at": utc_now(),
        "device_name": str(payload["device_name"]).strip(),
        "admin_username": admin_username,
        "network_mode": str(payload.get("network_mode", "dhcp")).strip(),
        "enable_smb": bool(payload.get("enable_smb", True)),
        "password": password_hash(admin_password),
        "account_configured": account_ok,
        "account_warning": account_warning,
    }

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SETUP_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(SETUP_FILE, 0o600)
    except OSError:
        pass

    setup = load_setup()
    return {**setup, "account_configured": account_ok, "account_warning": account_warning}, None


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


def flatten_mountpoints(item):
    values = []
    for key in ("mountpoint", "mountpoints"):
        value = item.get(key)
        if isinstance(value, list):
            values.extend([entry for entry in value if entry])
        elif value:
            values.append(value)

    for child in item.get("children") or []:
        values.extend(flatten_mountpoints(child))
    return sorted(set(values))


def flatten_fstypes(item):
    values = []
    value = item.get("fstype")
    if value:
        values.append(value)
    for child in item.get("children") or []:
        values.extend(flatten_fstypes(child))
    return sorted(set(values))


def disk_role(mountpoints):
    system_mounts = {"/", "/boot", "/boot/efi", "/usr", "/var", "/home"}
    if any(point in system_mounts for point in mountpoints):
        return "system"
    if mountpoints:
        return "mounted"
    return "available"


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

        mountpoints = flatten_mountpoints(item)
        fstypes = flatten_fstypes(item)
        role = disk_role(mountpoints)

        disks.append({
            "name": item.get("name", "disk"),
            "path": f"/dev/{item.get('name', 'disk')}",
            "size": int(item.get("size") or 0),
            "size_label": format_bytes(item.get("size") or 0),
            "model": item.get("model") or "未知型号",
            "transport": item.get("tran") or "system",
            "mountpoints": mountpoints,
            "fstypes": fstypes,
            "role": role,
            "pool_candidate": role == "available" and int(item.get("size") or 0) > 0,
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


def storage_overview():
    storage = storage_info()
    state = load_storage_state()
    disks = storage["disks"]
    candidates = [disk for disk in disks if disk.get("pool_candidate")]
    system_disks = [disk for disk in disks if disk.get("role") == "system"]
    mounted_disks = [disk for disk in disks if disk.get("role") == "mounted"]

    return {
        "generated_at": int(time.time()),
        "root": storage["root"],
        "disks": disks,
        "summary": {
            "total": len(disks),
            "available": len(candidates),
            "system": len(system_disks),
            "mounted": len(mounted_disks),
            "total_capacity": sum(disk.get("size", 0) for disk in disks),
            "total_capacity_label": format_bytes(sum(disk.get("size", 0) for disk in disks)),
        },
        "pools": state["pool_plans"],
        "shares": state["shares"],
        "latest_run": state["runs"][-1] if state["runs"] else None,
        "events": state["events"],
        "runs": state["runs"],
        "manifests": state["manifests"],
        "execution_enabled": STORAGE_EXECUTION_ENABLED,
        "pool_script_available": STORAGE_POOL_SCRIPT.exists(),
        "recommendation": pool_recommendation(candidates),
        "warnings": [
            "默认安全模式不会格式化、挂载或写入磁盘。",
            "只有显式启用存储池执行开关后，脚本才会进入真实执行路径。",
        ],
    }


def pool_recommendation(candidates):
    if len(candidates) >= 2:
        return {
            "mode": "mirror",
            "label": "镜像池",
            "disk_names": [disk["name"] for disk in candidates[:2]],
            "capacity_label": format_bytes(min(disk.get("size", 0) for disk in candidates[:2])),
            "message": "已找到可用于规划的空闲磁盘。",
        }
    if len(candidates) == 1:
        return {
            "mode": "single",
            "label": "单盘池",
            "disk_names": [candidates[0]["name"]],
            "capacity_label": candidates[0].get("size_label", "0 B"),
            "message": "可先规划单盘池，后续再扩展冗余。",
        }
    return {
        "mode": "none",
        "label": "等待磁盘",
        "disk_names": [],
        "capacity_label": "0 B",
        "message": "未发现可安全规划的空闲磁盘。",
    }


def pool_stage_template(status="pending"):
    return [
        {"id": "validate-disks", "label": "校验目标磁盘", "status": status},
        {"id": "prepare-mountpoint", "label": "准备挂载目录", "status": status},
        {"id": "create-filesystem", "label": "创建 Btrfs 存储池", "status": status},
        {"id": "mount-pool", "label": "挂载数据池", "status": status},
        {"id": "register-pool", "label": "写入系统记录", "status": status},
    ]


def storage_manifest_path(plan_id):
    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "-", str(plan_id)).strip("-") or "storage-plan"
    return STORAGE_MANIFEST_DIR / f"{safe_id}.json"


def storage_run_path(run_id):
    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "-", str(run_id)).strip("-") or "storage-run"
    return STORAGE_RUN_DIR / f"{safe_id}.json"


def pool_disks_still_candidates(plan):
    names = set(plan.get("disk_names") or [])
    candidates = [disk for disk in storage_overview()["disks"] if disk.get("pool_candidate")]
    selected = [disk for disk in candidates if disk.get("name") in names or disk.get("path") in names]
    return selected if len(selected) == len(names) else []


def create_pool_plan(payload):
    name = str(payload.get("name") or "MainPool").strip()
    mode = str(payload.get("mode") or "mirror").strip()
    disk_names = payload.get("disk_names") or []
    available_names = {disk["name"] for disk in storage_overview()["disks"] if disk.get("pool_candidate")}

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,31}", name):
        return None, {"name": "存储池名称需为 3-32 位字母、数字、下划线或短横线。"}
    if mode not in {"single", "mirror"}:
        return None, {"mode": "当前版本仅支持单盘池或镜像池规划。"}
    if not isinstance(disk_names, list) or not disk_names:
        return None, {"disk_names": "请选择可用磁盘。"}
    if any(name not in available_names for name in disk_names):
        return None, {"disk_names": "包含不可用于存储池规划的磁盘。"}
    if mode == "mirror" and len(disk_names) < 2:
        return None, {"mode": "镜像池至少需要 2 块可用磁盘。"}

    state = load_storage_state()
    plans = [plan for plan in state["pool_plans"] if plan.get("name") != name]
    selected_disks = [disk for disk in storage_overview()["disks"] if disk.get("name") in disk_names]
    raw_capacity = min((disk.get("size", 0) for disk in selected_disks), default=0) if mode == "mirror" else sum(disk.get("size", 0) for disk in selected_disks)
    plan_id = f"pool-{int(time.time())}"
    mountpoint = f"/srv/lingyue/pools/{name}"
    manifest = {
        "schema": 1,
        "product": "Lingyue OS",
        "plan_id": plan_id,
        "created_at": utc_now(),
        "pool": {
            "name": name,
            "mode": mode,
            "filesystem": "btrfs",
            "mountpoint": mountpoint,
            "devices": [
                {
                    "name": disk["name"],
                    "path": disk["path"],
                    "size": disk.get("size", 0),
                    "size_label": disk["size_label"],
                    "model": disk.get("model") or "未知型号",
                    "transport": disk.get("transport") or "system",
                }
                for disk in selected_disks
            ],
        },
        "safety": {
            "requires_confirmation": "CREATE-LINGYUE-POOL",
            "execution_enabled": STORAGE_EXECUTION_ENABLED,
            "destructive_write_default": False,
        },
    }
    manifest_path = storage_manifest_path(plan_id)
    write_json_file(manifest_path, manifest)
    plan = {
        "id": plan_id,
        "name": name,
        "mode": mode,
        "mode_label": "Btrfs Mirror" if mode == "mirror" else "Btrfs Single",
        "disk_names": disk_names,
        "disk_paths": [disk["path"] for disk in selected_disks],
        "mountpoint": mountpoint,
        "manifest_path": str(manifest_path),
        "stages": pool_stage_template(),
        "capacity": raw_capacity,
        "capacity_label": format_bytes(raw_capacity),
        "status": "planned",
        "status_label": "待执行",
        "created_at": manifest["created_at"],
    }
    plans.append(plan)
    state["pool_plans"] = plans
    state["manifests"] = [{"plan_id": plan_id, "path": str(manifest_path), "created_at": manifest["created_at"]}]
    append_storage_event(state, "info", f"已生成存储池规划 {name}。")
    save_storage_state(state)
    return plan, None


def execute_pool_plan(payload):
    confirm = str(payload.get("confirm") or "").strip()
    state = load_storage_state()
    plan = state["pool_plans"][-1] if state["pool_plans"] else None

    if not plan:
        return None, {"plan": "请先创建存储池规划。"}
    if confirm != "CREATE-LINGYUE-POOL":
        return None, {"confirm": "请输入确认短语 CREATE-LINGYUE-POOL。"}
    if not STORAGE_POOL_SCRIPT.exists():
        return None, {"script": "存储池脚本入口不存在。"}
    if not pool_disks_still_candidates(plan):
        append_storage_event(state, "error", "目标磁盘状态已变化，存储池执行已停止。")
        save_storage_state(state)
        return None, {"target": "目标磁盘不再处于空闲可规划状态，请重新扫描并创建规划。"}

    run_id = f"pool-run-{int(time.time())}"
    run = {
        "id": run_id,
        "plan_id": plan["id"],
        "pool_name": plan["name"],
        "mode": plan["mode"],
        "disk_paths": plan.get("disk_paths") or [],
        "mountpoint": plan.get("mountpoint"),
        "manifest_path": plan.get("manifest_path"),
        "run_path": str(storage_run_path(run_id)),
        "started_at": utc_now(),
        "status": "blocked",
        "status_label": "安全模式已阻止",
        "stages": pool_stage_template("blocked"),
    }

    if not STORAGE_EXECUTION_ENABLED:
        append_storage_event(state, "warn", "存储池执行被安全模式阻止，未格式化或挂载磁盘。")
        write_json_file(storage_run_path(run_id), run)
        state["runs"] = [run]
        save_storage_state(state)
        return {**run, "message": "安全模式已阻止执行。设置 LINGYUE_STORAGE_EXECUTE=1 后才允许脚本进入真实执行路径。"}, None

    code, output, error = run_command_result([
        str(STORAGE_POOL_SCRIPT),
        "--pool", plan["name"],
        "--mode", plan["mode"],
        "--devices", ",".join(plan.get("disk_paths") or []),
        "--mountpoint", str(plan.get("mountpoint") or f"/srv/lingyue/pools/{plan['name']}"),
        "--plan", plan["id"],
        "--manifest", str(plan.get("manifest_path") or storage_manifest_path(plan["id"])),
        "--run", str(storage_run_path(run_id)),
    ])
    run["status"] = "completed" if code == 0 else "failed"
    run["status_label"] = "执行完成" if code == 0 else "执行失败"
    run["finished_at"] = utc_now()
    run["output"] = output[-2000:]
    run["error"] = error[-2000:]
    run["stages"] = pool_stage_template("completed" if code == 0 else "failed")
    write_json_file(storage_run_path(run_id), run)
    append_storage_event(state, "info" if code == 0 else "error", run["status_label"])
    state["runs"] = [run]
    save_storage_state(state)
    return run, None


def installer_target_candidates():
    disks = storage_overview()["disks"]
    return [disk for disk in disks if disk.get("role") == "available" and disk.get("size", 0) >= INSTALL_MIN_SIZE]


def install_stage_template(status="pending"):
    return [
        {"id": "validate-target", "label": "校验目标磁盘", "status": status},
        {"id": "partition-layout", "label": "规划 EFI、系统和数据分区", "status": status},
        {"id": "format-filesystems", "label": "准备文件系统", "status": status},
        {"id": "deploy-system", "label": "写入凌岳OS 系统文件", "status": status},
        {"id": "configure-boot", "label": "配置引导和首启服务", "status": status},
        {"id": "finalize", "label": "生成安装报告", "status": status},
    ]


def partition_device_name(target, number):
    separator = "p" if re.search(r"(nvme\d+n\d+|mmcblk\d+|loop\d+)$", target) else ""
    return f"{target}{separator}{number}"


def install_partitions_for(target):
    return [
        {"name": "EFI", "device": partition_device_name(target, 1), "size_label": "512 MB", "filesystem": "FAT32", "mountpoint": "/boot/efi"},
        {"name": "System", "device": partition_device_name(target, 2), "size_label": "12 GB", "filesystem": "Btrfs", "mountpoint": "/"},
        {"name": "Data Reserve", "device": partition_device_name(target, 3), "size_label": "剩余空间", "filesystem": "Btrfs", "mountpoint": "/srv/lingyue"},
    ]


def install_manifest_path(plan_id):
    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "-", str(plan_id)).strip("-") or "install-plan"
    return INSTALL_MANIFEST_DIR / f"{safe_id}.json"


def install_run_path(run_id):
    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "-", str(run_id)).strip("-") or "install-run"
    return INSTALL_RUN_DIR / f"{safe_id}.json"


def target_still_candidate(plan):
    candidates = installer_target_candidates()
    return next((disk for disk in candidates if disk.get("path") == plan.get("target") or disk.get("name") == plan.get("target_name")), None)


def install_overview():
    candidates = installer_target_candidates()
    state = load_install_state()
    plans = state["plans"]
    latest_plan = plans[-1] if plans else None
    ready = bool(candidates)

    return {
        "generated_at": int(time.time()),
        "mode": "plan_only",
        "ready": ready,
        "ready_label": "可生成安装计划" if ready else "等待可安装磁盘",
        "candidate_count": len(candidates),
        "minimum_size_label": "16 GB",
        "targets": candidates,
        "latest_plan": latest_plan,
        "latest_run": state["runs"][-1] if state["runs"] else None,
        "events": state["events"],
        "runs": state["runs"],
        "manifests": state["manifests"],
        "execution_enabled": INSTALL_EXECUTION_ENABLED,
        "installer_available": INSTALL_SCRIPT.exists(),
        "warnings": [
            "默认安全模式不会写入、格式化或分区磁盘。",
            "只有显式启用安装执行开关后，安装脚本才会进入真实执行路径。",
        ],
    }


def create_install_plan(payload):
    target = str(payload.get("target") or "").strip()
    candidates = installer_target_candidates()
    selected = next((disk for disk in candidates if disk.get("name") == target or disk.get("path") == target), None)

    if not selected:
        return None, {"target": "请选择一块未挂载且不低于 16 GB 的空闲磁盘。"}

    plan_id = f"install-{int(time.time())}"
    manifest = {
        "schema": 1,
        "product": "Lingyue OS",
        "plan_id": plan_id,
        "created_at": utc_now(),
        "target": {
            "path": selected["path"],
            "name": selected["name"],
            "size": selected.get("size", 0),
            "size_label": selected["size_label"],
            "model": selected.get("model") or "未知型号",
            "transport": selected.get("transport") or "system",
        },
        "layout": {
            "partition_table": "gpt",
            "partitions": install_partitions_for(selected["path"]),
        },
        "safety": {
            "requires_confirmation": "INSTALL-LINGYUE",
            "execution_enabled": INSTALL_EXECUTION_ENABLED,
            "destructive_write_default": False,
        },
    }
    manifest_path = install_manifest_path(plan_id)
    write_json_file(manifest_path, manifest)

    plan = {
        "id": plan_id,
        "target": selected["path"],
        "target_name": selected["name"],
        "target_size_label": selected["size_label"],
        "manifest_path": str(manifest_path),
        "partitions": manifest["layout"]["partitions"],
        "stages": install_stage_template(),
        "status": "planned",
        "status_label": "待执行",
        "created_at": manifest["created_at"],
        "steps": [
            "校验目标磁盘仍为空闲状态",
            "创建 EFI、系统和数据保留分区",
            "安装 Debian 基础系统与凌岳OS 控制台",
            "写入引导器、网络配置和初始化服务",
            "重启后从硬盘进入凌岳OS",
        ],
    }
    state = load_install_state()
    state["plans"] = [plan]
    state["manifests"] = [{"plan_id": plan_id, "path": str(manifest_path), "created_at": manifest["created_at"]}]
    append_install_event(state, "info", f"已生成安装计划，目标 {selected['path']}。")
    save_install_state(state)
    return plan, None


def execute_install_plan(payload):
    confirm = str(payload.get("confirm") or "").strip()
    state = load_install_state()
    plan = state["plans"][-1] if state["plans"] else None

    if not plan:
        return None, {"plan": "请先生成安装计划。"}
    if confirm != "INSTALL-LINGYUE":
        return None, {"confirm": "请输入确认短语 INSTALL-LINGYUE。"}
    if not INSTALL_SCRIPT.exists():
        return None, {"script": "安装脚本入口不存在。"}
    if not target_still_candidate(plan):
        append_install_event(state, "error", "目标磁盘状态已变化，安装执行已停止。")
        save_install_state(state)
        return None, {"target": "目标磁盘不再处于空闲可安装状态，请重新预检并生成计划。"}

    run_id = f"run-{int(time.time())}"
    run = {
        "id": run_id,
        "plan_id": plan["id"],
        "target": plan["target"],
        "manifest_path": plan.get("manifest_path"),
        "run_path": str(install_run_path(run_id)),
        "started_at": utc_now(),
        "status": "blocked",
        "status_label": "安全模式已阻止",
        "stages": install_stage_template("blocked"),
    }

    if not INSTALL_EXECUTION_ENABLED:
        append_install_event(state, "warn", "安装执行被安全模式阻止，未写入磁盘。")
        write_json_file(install_run_path(run_id), run)
        state["runs"] = [run]
        save_install_state(state)
        return {**run, "message": "安全模式已阻止执行。设置 LINGYUE_INSTALL_EXECUTE=1 后才允许脚本进入真实执行路径。"}, None

    code, output, error = run_command_result([
        str(INSTALL_SCRIPT),
        "--target", plan["target"],
        "--plan", plan["id"],
        "--manifest", str(plan.get("manifest_path") or install_manifest_path(plan["id"])),
        "--run", str(install_run_path(run_id)),
    ])
    run["status"] = "completed" if code == 0 else "failed"
    run["status_label"] = "执行完成" if code == 0 else "执行失败"
    run["finished_at"] = utc_now()
    run["output"] = output[-2000:]
    run["error"] = error[-2000:]
    run["stages"] = install_stage_template("completed" if code == 0 else "failed")
    write_json_file(install_run_path(run_id), run)
    append_install_event(state, "info" if code == 0 else "error", run["status_label"])
    state["runs"] = [run]
    save_install_state(state)
    return run, None


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
    def session_token(self):
        cookie_header = self.headers.get("Cookie") or ""
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header)
        except Exception:
            return ""
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else ""

    def current_session(self):
        return session_from_token(self.session_token())

    def require_auth(self):
        session = self.current_session()
        if session:
            return session
        self.send_json({"ok": False, "error": "请先登录管理员账号。", "auth_required": True}, status=401)
        return None

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
        if parsed.path == "/api/auth/session":
            self.send_json(public_session_payload(self.current_session()))
            return
        if parsed.path == "/api/system/overview":
            self.send_json(overview())
            return
        if parsed.path == "/api/storage/overview":
            self.send_json(storage_overview())
            return
        if parsed.path == "/api/install/overview":
            self.send_json(install_overview())
            return
        if parsed.path == "/api/shares/overview":
            self.send_json(shares_overview())
            return
        if STATIC_DIR:
            self.send_static(parsed.path)
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        allowed_paths = {
            "/api/setup/complete",
            "/api/auth/login",
            "/api/auth/logout",
            "/api/storage/pools/plan",
            "/api/storage/pools/execute",
            "/api/install/plan",
            "/api/install/execute",
            "/api/shares/create",
        }
        if parsed.path not in allowed_paths:
            self.send_response(404)
            self.end_headers()
            return

        try:
            length = min(int(self.headers.get("Content-Length", "0")), 16384)
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, json.JSONDecodeError):
            self.send_json({"ok": False, "error": "请求格式无效。"}, status=400)
            return

        if parsed.path == "/api/auth/login":
            result, errors = login(payload)
            if errors:
                self.send_json({"ok": False, "errors": errors}, status=401)
                return
            self.send_json({"ok": True, "session": result["session"]}, extra_headers=[self.session_cookie_header(result["token"])])
            return

        if parsed.path == "/api/auth/logout":
            destroy_session(self.session_token())
            self.send_json({"ok": True}, extra_headers=[self.session_cookie_header("", expired=True)])
            return

        if parsed.path == "/api/setup/complete":
            if load_setup()["completed"] and not self.require_auth():
                return
            setup, errors = complete_setup(payload)
            if errors:
                self.send_json({"ok": False, "errors": errors}, status=400)
                return
            result, _ = login({"username": payload.get("admin_username"), "password": payload.get("admin_password")})
            headers = [self.session_cookie_header(result["token"])] if result else []
            self.send_json({"ok": True, "setup": setup, "session": result["session"] if result else None}, extra_headers=headers)
            return

        if not self.require_auth():
            return

        if parsed.path == "/api/storage/pools/plan":
            plan, errors = create_pool_plan(payload)
            if errors:
                self.send_json({"ok": False, "errors": errors}, status=400)
                return

            self.send_json({"ok": True, "pool": plan})
            return

        if parsed.path == "/api/storage/pools/execute":
            run, errors = execute_pool_plan(payload)
            if errors:
                self.send_json({"ok": False, "errors": errors}, status=400)
                return

            self.send_json({"ok": True, "run": run})
            return

        if parsed.path == "/api/install/plan":
            plan, errors = create_install_plan(payload)
            if errors:
                self.send_json({"ok": False, "errors": errors}, status=400)
                return

            self.send_json({"ok": True, "plan": plan})
            return

        if parsed.path == "/api/install/execute":
            run, errors = execute_install_plan(payload)
            if errors:
                self.send_json({"ok": False, "errors": errors}, status=400)
                return

            self.send_json({"ok": True, "run": run})
            return

        share, errors = create_share(payload)
        if errors:
            self.send_json({"ok": False, "errors": errors}, status=400)
            return

        self.send_json({"ok": True, "share": share})

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

    def session_cookie_header(self, token, expired=False):
        max_age = 0 if expired else SESSION_TTL_SECONDS
        value = token if token else "deleted"
        return ("Set-Cookie", f"{SESSION_COOKIE}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}")

    def send_json(self, payload, status=200, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_cors_headers()
        for key, value in extra_headers or []:
            self.send_header(key, value)
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
