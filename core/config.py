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


def ensure_nautilus_mount_icons(config: Dict[str, Any]) -> bool:
    """Install the Google Drive logo icon for Nautilus sidebar mounts and configure x-gvfs mount icon options."""
    import shutil
    import urllib.parse

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    logo_svg = os.path.join(project_root, "assets", "icons", "drive_logo_24px.svg")
    logo_png = os.path.join(project_root, "assets", "icons", "drive_fs.png")

    # 1. Install google-drive and google-drive-symbolic into user icon themes so GTK4/Nautilus renders the colorful Drive logo
    for theme_name in ("hicolor", "Yaru", "Yaru-dark", "Adwaita"):
        for sub in ("scalable/apps", "scalable/devices", "symbolic/apps", "symbolic/devices"):
            dest_dir = os.path.expanduser(f"~/.local/share/icons/{theme_name}/{sub}")
            try:
                os.makedirs(dest_dir, exist_ok=True)
                if os.path.exists(logo_svg):
                    shutil.copy2(logo_svg, os.path.join(dest_dir, "google-drive.svg"))
                    shutil.copy2(logo_svg, os.path.join(dest_dir, "google-drive-symbolic.svg"))
            except Exception:
                pass
        if os.path.exists(logo_png):
            png_dir = os.path.expanduser(f"~/.local/share/icons/{theme_name}/256x256/apps")
            try:
                os.makedirs(png_dir, exist_ok=True)
                shutil.copy2(logo_png, os.path.join(png_dir, "google-drive.png"))
                shutil.copy2(logo_png, os.path.join(png_dir, "google-drive-symbolic.png"))
            except Exception:
                pass

    # 2. Ensure /etc/fstab has noauto,user,x-gvfs-show,x-gvfs-icon=google-drive,x-gvfs-symbolic-icon=google-drive-symbolic
    #    for each configured account mount point so gvfs-udisks2-volume-monitor gives Nautilus the Google Drive logo!
    mounts = dict(config.get("account_mounts") or {})
    primary_mp = config.get("mount_point") or DEFAULT_MOUNT_POINT
    if primary_mp and primary_mp not in mounts.values():
        mounts["primary"] = primary_mp

    names = dict(config.get("account_names") or {})
    desired_lines = []
    for acc_id, mp in mounts.items():
        if not mp:
            continue
        mp_clean = mp.rstrip("/")
        escaped_mp = mp_clean.replace(" ", "\\040")
        display_name = names.get(acc_id) or os.path.basename(mp_clean) or "Google Drive"
        encoded_name = urllib.parse.quote(display_name, safe="")
        opts = (
            f"noauto,user,x-gvfs-show,x-gvfs-name={encoded_name},"
            f"x-gvfs-icon=google-drive,x-gvfs-symbolic-icon=google-drive-symbolic"
        )
        desired_lines.append(f"GoogleDriveFUSE {escaped_mp} fuse.GoogleDriveFUSE {opts} 0 0 # google-drive-linux")

    fstab_path = "/etc/fstab"
    if not os.path.exists(fstab_path):
        return True

    try:
        with open(fstab_path, "r", encoding="utf-8") as f:
            existing_content = f.read()
        existing_lines = [ln for ln in existing_content.splitlines() if "# google-drive-linux" not in ln and "fuse.GoogleDriveFUSE" not in ln]
        current_gdf_lines = [ln.strip() for ln in existing_content.splitlines() if "# google-drive-linux" in ln]
        if sorted(current_gdf_lines) == sorted(desired_lines):
            return True

        new_content = "\n".join(existing_lines).rstrip() + "\n"
        if desired_lines:
            new_content += "\n".join(desired_lines) + "\n"

        tmp_fstab = os.path.join(CACHE_DIR, "fstab.gdrive.tmp")
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(tmp_fstab, "w", encoding="utf-8") as f:
            f.write(new_content)

        if os.geteuid() == 0:
            shutil.copy2(tmp_fstab, fstab_path)
            return True
        elif os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            res = subprocess.run(["pkexec", "cp", tmp_fstab, fstab_path], capture_output=True)
            return res.returncode == 0
    except Exception as e:
        print(f"[Config] Aviso ao atualizar ícones do Nautilus em /etc/fstab: {e}")
    return False


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


def suggest_account_mount_point(config: Dict[str, Any], email: str = "", is_primary: bool = False, custom_name: str = "") -> str:
    """Return a suggested mount point for an account (e.g., '<parent>/GoogleDrive - <prefix>' for secondary accounts)."""
    primary_mount = config.get("mount_point", DEFAULT_MOUNT_POINT).rstrip("/")
    parent_dir = os.path.dirname(primary_mount) or os.path.expanduser("~")
    if custom_name and custom_name.strip():
        safe_name = custom_name.strip().replace("/", "-")
        return os.path.join(parent_dir, safe_name)
    if is_primary or not email:
        return primary_mount
    prefix = email.split("@")[0].strip()
    return os.path.join(parent_dir, f"GoogleDrive - {prefix}")


def get_account_display_name(config: Dict[str, Any], account_id: str, email: str = "", is_primary: bool = False) -> str:
    """Return the custom sidebar display name for an account, or derive it from its mount folder."""
    names = config.get("account_names") or {}
    if account_id and account_id in names and names[account_id]:
        return names[account_id]
    mp = get_account_mount_point(config, account_id, email=email, is_primary=is_primary)
    return os.path.basename(mp.rstrip("/")) or "GoogleDrive"


def get_account_mount_point(config: Dict[str, Any], account_id: str, email: str = "", is_primary: bool = False) -> str:
    """Return the configured mount point for a specific account_id, or suggest a default."""
    mounts = config.get("account_mounts") or {}
    if account_id and account_id in mounts and mounts[account_id]:
        return mounts[account_id]
    return suggest_account_mount_point(config, email=email, is_primary=is_primary)


def get_mount_point() -> str:
    """Return active mount point (supporting external HDs and custom paths)."""
    cfg = load_config()
    return cfg.get("mount_point", DEFAULT_MOUNT_POINT)


def get_cache_dir() -> str:
    """Return active cache directory (supporting external HDs and custom paths)."""
    cfg = load_config()
    return cfg.get("cache_dir", CACHE_DIR)
