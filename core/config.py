"""
Google Drive for Linux - Configuration & Environment Manager
"""
import os
import json
import subprocess
from pathlib import Path
from typing import Dict, Any

APP_NAME = "google-drive-linux"
DEFAULT_MOUNT_POINT = os.path.expanduser("~/GoogleDrive")
CONFIG_DIR = os.path.expanduser(f"~/.config/{APP_NAME}")
CACHE_DIR = os.path.expanduser(f"~/.cache/{APP_NAME}")
METADATA_DB_PATH = os.path.join(CONFIG_DIR, "metadata.db")
CONFIG_FILE_PATH = os.path.join(CONFIG_DIR, "config.json")
ACCOUNTS_FILE_PATH = os.path.join(CONFIG_DIR, "accounts.json")
LOG_DIR = os.path.join(CONFIG_DIR, "logs")
IPC_SOCKET_PATH = os.path.join(CONFIG_DIR, "gdrive_ipc.sock")
MCP_SOCKET_PATH = os.path.join(CONFIG_DIR, "dfd_mcp.sock")

# Google Drive Client OAuth Credentials (installed desktop app default)
# Supports custom overrides via environment variables or config.json
DEFAULT_CLIENT_ID = os.environ.get(
    "GDRIVE_CLIENT_ID",
    "202264815644.apps.googleusercontent.com"
)
DEFAULT_CLIENT_SECRET = os.environ.get(
    "GDRIVE_CLIENT_SECRET",
    "X4Z3ca8xfWDb1Voo-F9a7ZxJ"
)

DEFAULT_CONFIG: Dict[str, Any] = {
    "mount_point": DEFAULT_MOUNT_POINT,
    "cache_dir": CACHE_DIR,
    "sync_mode": "stream",  # 'stream' (on-demand) or 'mirror' (offline sync)
    "max_cache_size_gb": 10,
    "chunk_size_mb": 16,
    "upload_bandwidth_kbps": 0,    # 0 = unlimited
    "download_bandwidth_kbps": 0,  # 0 = unlimited
    "auto_update": True,
    "update_check_interval_hours": 12,
    "enable_nautilus_integration": True,
    "enable_mcp_server": True,
    "start_at_login": True,
    "notifications_enabled": True
}


def ensure_apparmor_allows_mount(mount_point: str) -> bool:
    """Ensure Ubuntu 24.04/26.04+ AppArmor fusermount3 profile allows FUSE mounts under /run/media/."""
    if not mount_point or not mount_point.startswith("/run/media/"):
        return True
    apparmor_profile = "/etc/apparmor.d/fusermount3"
    local_override = "/etc/apparmor.d/local/fusermount3"
    if not os.path.exists(apparmor_profile):
        return True
    try:
        if os.path.exists(local_override):
            with open(local_override, "r", encoding="utf-8") as f:
                if "/run/media/**/" in f.read():
                    return True
    except Exception:
        pass

    rule_cmd = (
        "mkdir -p /etc/apparmor.d/local && "
        "printf 'mount fstype=@{fuse_types} options=(nosuid,nodev) options in (ro,rw,noatime,dirsync,nodiratime,noexec,sync) -> /run/media/**/,\n"
        "umount /run/media/**/,\n' > /etc/apparmor.d/local/fusermount3 && "
        "apparmor_parser -r /etc/apparmor.d/fusermount3"
    )
    try:
        if os.geteuid() == 0:
            res = subprocess.run(["sh", "-c", rule_cmd], capture_output=True)
            return res.returncode == 0
        elif os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            res = subprocess.run(["pkexec", "sh", "-c", rule_cmd], capture_output=True)
            return res.returncode == 0
    except Exception:
        pass
    return False


def unmount_fuse_path(path: str, remove_if_empty: bool = False):
    """Safely unmount a FUSE mountpoint (including symlinked AppArmor fallback) and optionally remove it if empty."""
    if not path:
        return
    fallback_mount = os.path.expanduser("~/.cache/google-drive-linux/fuse_mnt")
    subprocess.run(["fusermount3", "-uz", path], stderr=subprocess.DEVNULL, check=False)
    subprocess.run(["fusermount", "-uz", path], stderr=subprocess.DEVNULL, check=False)
    if os.path.islink(path):
        subprocess.run(["fusermount3", "-uz", fallback_mount], stderr=subprocess.DEVNULL, check=False)
        subprocess.run(["fusermount", "-uz", fallback_mount], stderr=subprocess.DEVNULL, check=False)
        if remove_if_empty:
            try:
                os.unlink(path)
            except OSError:
                pass
        return
    if remove_if_empty and os.path.isdir(path) and not os.path.ismount(path):
        try:
            if not os.listdir(path):
                os.rmdir(path)
        except OSError:
            pass


def _read_raw_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_FILE_PATH):
        try:
            with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {}


def ensure_directories():
    """Ensure all required application directories exist, recovering from stale FUSE mounts if needed."""
    raw = _read_raw_config()
    active_mount = raw.get("mount_point") or DEFAULT_MOUNT_POINT
    active_cache = raw.get("cache_dir") or CACHE_DIR

    # If user configured a custom mount point different from DEFAULT_MOUNT_POINT,
    # clean up DEFAULT_MOUNT_POINT if it is a leftover mount or empty directory.
    if active_mount != DEFAULT_MOUNT_POINT and os.path.exists(DEFAULT_MOUNT_POINT):
        unmount_fuse_path(DEFAULT_MOUNT_POINT, remove_if_empty=True)

    for path in [CONFIG_DIR, active_cache, LOG_DIR, active_mount]:
        if os.path.islink(path):
            continue
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
        except OSError:
            # Recover if path is a disconnected FUSE mount (ENOTCONN / FileExistsError)
            unmount_fuse_path(path, remove_if_empty=False)
            try:
                if not os.path.islink(path):
                    Path(path).mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

    # Ensure user-level .desktop and icon exist so GNOME Shell (Wayland/X11) Dock & Lock Screen always show the Drive icon
    try:
        import shutil
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        desktop_src = os.path.join(project_root, "packaging", "desktop", "google-drive.desktop")
        icon_src = os.path.join(project_root, "assets", "icons", "drive_fs.png")
        user_apps_dir = os.path.expanduser("~/.local/share/applications")
        user_icons_dir = os.path.expanduser("~/.local/share/icons/hicolor/256x256/apps")
        if os.path.exists(desktop_src):
            os.makedirs(user_apps_dir, exist_ok=True)
            shutil.copy2(desktop_src, os.path.join(user_apps_dir, "google-drive.desktop"))
        if os.path.exists(icon_src):
            os.makedirs(user_icons_dir, exist_ok=True)
            shutil.copy2(icon_src, os.path.join(user_icons_dir, "google-drive.png"))
    except Exception:
        pass


def load_config() -> Dict[str, Any]:
    """Load configuration from disk or create default."""
    ensure_directories()
    if not os.path.exists(CONFIG_FILE_PATH):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

    try:
        with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
            user_config = json.load(f)
            merged = DEFAULT_CONFIG.copy()
            merged.update(user_config)
            return merged
    except Exception as e:
        print(f"[Config] Error loading configuration: {e}. Using defaults.")
        return DEFAULT_CONFIG.copy()


def save_config(config: Dict[str, Any]):
    """Save configuration to disk."""
    ensure_directories()
    with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def get_mount_point() -> str:
    """Return active mount point (supporting external HDs and custom paths)."""
    cfg = load_config()
    return cfg.get("mount_point", DEFAULT_MOUNT_POINT)


def get_cache_dir() -> str:
    """Return active cache directory (supporting external HDs and custom paths)."""
    cfg = load_config()
    return cfg.get("cache_dir", CACHE_DIR)
