"""
Google Drive for Linux - Configuration & Environment Manager
"""
import os
import json
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

# Google Drive Client OAuth Credentials (installed app default)
# Uses standard Google desktop OAuth client credentials
DEFAULT_CLIENT_ID = os.environ.get("GDRIVE_CLIENT_ID", "1068432940246-gdrive_linux.apps.googleusercontent.com")
DEFAULT_CLIENT_SECRET = os.environ.get("GDRIVE_CLIENT_SECRET", "")

DEFAULT_CONFIG: Dict[str, Any] = {
    "mount_point": DEFAULT_MOUNT_POINT,
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


def ensure_directories():
    """Ensure all required application directories exist."""
    for path in [CONFIG_DIR, CACHE_DIR, LOG_DIR, DEFAULT_MOUNT_POINT]:
        Path(path).mkdir(parents=True, exist_ok=True)


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
