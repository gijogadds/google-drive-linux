"""
Google Drive for Linux - Conflict Resolution Matrix & Safe Trash Utilities
Implements a 4-case conflict matrix and safe OS Trash deletion (never permanently unlinking user data).
"""
import os
import time
import shutil
import hashlib
import subprocess
from typing import Dict, Any, Optional, Tuple

# Default 4-case Conflict Resolution Matrix
DEFAULT_CONFLICT_POLICIES: Dict[str, str] = {
    "LOCAL_EDIT_CLOUD_EDIT": "KEEP_BOTH",      # KEEP_BOTH | KEEP_LOCAL | KEEP_CLOUD | ALWAYS_ASK
    "LOCAL_EDIT_CLOUD_DELETE": "KEEP_LOCAL",   # KEEP_LOCAL | KEEP_CLOUD | ALWAYS_ASK
    "LOCAL_EDIT_UNSYNC": "KEEP_LOCAL",         # KEEP_LOCAL | KEEP_UNSYNC | ALWAYS_ASK
    "LOCAL_DELETE_CLOUD_EDIT": "KEEP_CLOUD",   # KEEP_CLOUD | KEEP_LOCAL | ALWAYS_ASK
}

# Missing file behavior when offline or external HD disconnected
DEFAULT_MISSING_FILE_BEHAVIOR = "UNSYNC"       # UNSYNC (safe default) | DELETE


def compute_file_md5(file_path: str, chunk_size: int = 1024 * 1024) -> str:
    """Stream-compute MD5 checksum of a local file with bounded memory."""
    if not file_path or not os.path.isfile(file_path):
        return ""
    h = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            while True:
                buf = f.read(chunk_size)
                if not buf:
                    break
                h.update(buf)
        return h.hexdigest()
    except Exception:
        return ""


def make_conflict_copy_name(filename: str, suffix_tag: str = "conflito") -> str:
    """Generate a non-destructive conflict copy filename preserving extension."""
    base, ext = os.path.splitext(filename)
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    return f"{base} ({suffix_tag} {stamp}){ext}"


def move_to_os_trash(path: str) -> bool:
    """
    Safely move a local file or directory to the desktop Trash instead of permanent unlink.
    Tries 'gio trash', then falls back to XDG Trash (~/.local/share/Trash/files).
    """
    if not path or not os.path.exists(path):
        return True

    # 1. Preferred on Ubuntu / GNOME: gio trash
    if shutil.which("gio"):
        try:
            res = subprocess.run(["gio", "trash", path], capture_output=True, timeout=10)
            if res.returncode == 0 and not os.path.exists(path):
                return True
        except Exception:
            pass

    # 2. Fallback: XDG Trash directory
    try:
        trash_files = os.path.expanduser("~/.local/share/Trash/files")
        trash_info = os.path.expanduser("~/.local/share/Trash/info")
        os.makedirs(trash_files, exist_ok=True)
        os.makedirs(trash_info, exist_ok=True)

        base_name = os.path.basename(path.rstrip("/"))
        dst_name = base_name
        counter = 1
        while os.path.exists(os.path.join(trash_files, dst_name)):
            stem, ext = os.path.splitext(base_name)
            dst_name = f"{stem}.{counter}{ext}"
            counter += 1

        dst_path = os.path.join(trash_files, dst_name)
        shutil.move(path, dst_path)

        info_path = os.path.join(trash_info, f"{dst_name}.trashinfo")
        import urllib.parse
        with open(info_path, "w", encoding="utf-8") as f:
            f.write("[Trash Info]\n")
            f.write(f"Path={urllib.parse.quote(os.path.abspath(path))}\n")
            f.write(f"DeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
        return True
    except Exception as e:
        print(f"[Trash] Falha ao mover '{path}' para a Lixeira: {e}")
        return False


class ConflictResolver:
    """Evaluates local vs. cloud state changes and applies configured conflict policies."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        saved = cfg.get("conflict_policies") or {}
        self.policies = DEFAULT_CONFLICT_POLICIES.copy()
        self.policies.update(saved)
        self.missing_file_behavior = cfg.get("missing_file_behavior", DEFAULT_MISSING_FILE_BEHAVIOR)

    def classify_conflict(
        self,
        local_md5: str,
        cloud_md5: str,
        baseline_md5: str,
        local_deleted: bool = False,
        cloud_deleted: bool = False,
        unsyncing: bool = False,
    ) -> Optional[str]:
        """
        Determine whether a conflict exists between local and cloud states relative to the last synced baseline.
        Returns one of the 4 conflict types, or None if no conflict exists.
        """
        local_changed = bool(local_md5 and baseline_md5 and local_md5 != baseline_md5)
        cloud_changed = bool(cloud_md5 and baseline_md5 and cloud_md5 != baseline_md5)

        # Identical content on both sides is never a conflict
        if not local_deleted and not cloud_deleted and local_md5 and cloud_md5 and local_md5 == cloud_md5:
            return None

        if local_changed and unsyncing:
            return "LOCAL_EDIT_UNSYNC"
        if local_changed and cloud_deleted:
            return "LOCAL_EDIT_CLOUD_DELETE"
        if local_deleted and cloud_changed:
            return "LOCAL_DELETE_CLOUD_EDIT"
        if local_changed and cloud_changed:
            return "LOCAL_EDIT_CLOUD_EDIT"
        return None

    def decide(self, conflict_type: str) -> str:
        """Return the configured action for a conflict type."""
        return self.policies.get(conflict_type, "ALWAYS_ASK")

    def resolve_upload_conflict(
        self,
        local_path: str,
        db_item: Dict[str, Any],
        remote_meta: Optional[Dict[str, Any]],
    ) -> Tuple[str, Optional[str]]:
        """
        Check before uploading a modified local file whether the cloud version diverged.
        Returns (action, conflict_type):
          action in ('PROCEED_UPLOAD', 'KEEP_BOTH', 'KEEP_CLOUD', 'DEFER_ASK')
        """
        baseline_md5 = (db_item or {}).get("md5_checksum") or ""
        local_md5 = compute_file_md5(local_path)

        if remote_meta is None or remote_meta.get("trashed"):
            if baseline_md5 and local_md5 and local_md5 != baseline_md5:
                policy = self.decide("LOCAL_EDIT_CLOUD_DELETE")
                if policy == "KEEP_LOCAL":
                    return "PROCEED_UPLOAD", "LOCAL_EDIT_CLOUD_DELETE"
                if policy == "KEEP_CLOUD":
                    return "KEEP_CLOUD", "LOCAL_EDIT_CLOUD_DELETE"
                return "DEFER_ASK", "LOCAL_EDIT_CLOUD_DELETE"
            return "PROCEED_UPLOAD", None

        cloud_md5 = remote_meta.get("md5Checksum") or ""
        conflict_type = self.classify_conflict(
            local_md5=local_md5,
            cloud_md5=cloud_md5,
            baseline_md5=baseline_md5,
        )
        if not conflict_type:
            return "PROCEED_UPLOAD", None

        policy = self.decide(conflict_type)
        if policy == "KEEP_LOCAL":
            return "PROCEED_UPLOAD", conflict_type
        if policy == "KEEP_CLOUD":
            return "KEEP_CLOUD", conflict_type
        if policy == "KEEP_BOTH":
            return "KEEP_BOTH", conflict_type
        return "DEFER_ASK", conflict_type
