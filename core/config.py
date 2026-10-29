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


def unmount_fuse_path(path: str, remove_if_empty: bool = False):
    """Safely unmount a FUSE mountpoint and optionally remove the directory if empty."""
    if not path:
        return
    subprocess.run(["fusermount3", "-uz", path], stderr=subprocess.DEVNULL, check=False)
    subprocess.run(["fusermount", "-uz", path], stderr=subprocess.DEVNULL, check=False)
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
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
        except OSError:
            # Recover if path is a disconnected FUSE mount (ENOTCONN / FileExistsError)
            unmount_fuse_path(path, remove_if_empty=False)
            try:
                Path(path).mkdir(parents=True, exist_ok=True)
            except OSError:
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
