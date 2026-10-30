"""
Google Drive for Linux - FUSE Virtual File System Engine
Provides an on-demand virtual filesystem mount (similar to Google Drive on Windows/macOS)
Compatible with Linux libfuse3 via fusepy or pyfuse3.
"""
import os
import sys
import glob
import json
import errno
import stat
import time
import ctypes.util
from typing import Dict, Any, Optional, List

# Auto-detect libfuse2 or libfuse3 shared library on Ubuntu 20.04 / 22.04 / 24.04 / 26.04+
if "FUSE_LIBRARY_PATH" not in os.environ:
    _detected_fuse = ctypes.util.find_library("fuse") or ctypes.util.find_library("fuse3")
    if not _detected_fuse:
        for _pattern in (
            "/lib/*-linux-gnu/libfuse.so*",
            "/usr/lib/*-linux-gnu/libfuse.so*",
            "/lib/*-linux-gnu/libfuse3.so*",
            "/usr/lib/*-linux-gnu/libfuse3.so*",
            "/lib64/libfuse*.so*",
        ):
            _matches = sorted(glob.glob(_pattern))
            if _matches:
                _detected_fuse = _matches[0]
                break
    if _detected_fuse:
        os.environ["FUSE_LIBRARY_PATH"] = _detected_fuse

# Try importing fusepy; provide stub if running in environment without libfuse
try:
    from fuse import FUSE, FuseOSError, Operations
except (ImportError, OSError, EnvironmentError):
    # Stub for systems without fusepy or libfuse installed yet
    FUSE = None
    class Operations:
        pass
    class FuseOSError(Exception):
        def __init__(self, errno_val):
            self.errno = errno_val


class GoogleDriveFUSE(Operations):
    def __init__(self, db, cache_manager, api, account_id: str, volume_name: str = "GoogleDrive"):
        self.db = db
        self.cache = cache_manager
        self.api = api
        self.account_id = account_id
        self.volume_name = volume_name or "GoogleDrive"
        self.open_files: Dict[int, Dict[str, Any]] = {}
        self.fh_counter = 1
        self._hydrated_dirs = set()
        self._icon_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "assets", "icons", "drive_fs.png"
        )

    def _get_virtual_volume_file(self, path: str) -> Optional[bytes]:
        if path == "/.xdg-volume-info":
            return (
                f"[Volume Info]\n"
                f"Name={self.volume_name}\n"
                f"IconFile=.volume-icon.png\n"
            ).encode("utf-8")
        if path == "/.volume-icon.png" and os.path.exists(self._icon_path):
            try:
                with open(self._icon_path, "rb") as f:
                    return f.read()
            except Exception:
                return None
        return None

    @staticmethod
    def _get_gdoc_extension(mime: str) -> str:
        if "spreadsheet" in mime:
            return ".gsheet"
        if "presentation" in mime:
            return ".gslides"
        if "form" in mime:
            return ".gform"
        return ".gdoc"

    def _build_gdoc_payload(self, item: Dict[str, Any]) -> bytes:
        mime = item.get("mime_type", "")
        doc_type = "doc"
        if "spreadsheet" in mime:
            doc_type = "sheet"
        elif "presentation" in mime:
            doc_type = "slide"
        elif "form" in mime:
            doc_type = "form"

        item_id = item.get("id", "")
        if doc_type == "doc":
            url = f"https://docs.google.com/document/d/{item_id}/edit"
        elif doc_type == "sheet":
            url = f"https://docs.google.com/spreadsheets/d/{item_id}/edit"
        elif doc_type == "slide":
            url = f"https://docs.google.com/presentation/d/{item_id}/edit"
        elif doc_type == "form":
            url = f"https://docs.google.com/forms/d/{item_id}/edit"
        else:
            url = f"https://drive.google.com/open?id={item_id}"

        payload = {
            "gd_type": doc_type,
            "doc_id": item_id,
            "url": url,
            "title": item.get("name", ""),
            "account_id": self.account_id,
        }
        return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")

    def _resolve_item(self, path: str) -> Optional[Dict[str, Any]]:
        """Resolve a POSIX path (e.g. /Folder/Subfolder/file.pdf) to an item record in SQLite."""
        if path in ("/", ""):
            return {"id": "root", "name": "root", "is_directory": 1, "size": 0, "modified_time": int(time.time())}
        vdata = self._get_virtual_volume_file(path)
        if vdata is not None:
            return {
                "id": f"__virtual__{path}",
                "name": os.path.basename(path),
                "is_directory": 0,
                "size": len(vdata),
                "modified_time": int(time.time()),
                "_virtual_bytes": vdata,
            }

        parts = [p for p in path.strip("/").split("/") if p]
        current_parent = "root"
        item = None

        for part in parts:
            # Handle Google Docs/Sheets/Slides virtual extension mapping (.gdoc, .gsheet, .gslides, .gform, .desktop)
            real_name = part
            if not part.startswith("."):
                for ext in (".gdoc", ".gsheet", ".gslides", ".gform", ".desktop"):
                    if part.endswith(ext):
                        real_name = part[:-len(ext)]
                        break

            item = self.db.get_item_by_path(current_parent, real_name, account_id=self.account_id)
            if not item:
                # Fallback to direct name lookup
                item = self.db.get_item_by_path(current_parent, part, account_id=self.account_id)
            if not item:
                return None
            current_parent = item["id"]

        return item

    # --- Filesystem Methods ---

    def getattr(self, path: str, fh=None) -> Dict[str, Any]:
        item = self._resolve_item(path)
        if not item:
            raise FuseOSError(errno.ENOENT)

        now = int(time.time())
        mtime = item.get("modified_time") or now

        uid = os.getuid()
        gid = os.getgid()

        if item.get("is_directory"):
            return {
                "st_mode": stat.S_IFDIR | 0o755,
                "st_nlink": 2,
                "st_size": 4096,
                "st_uid": uid,
                "st_gid": gid,
                "st_ctime": mtime,
                "st_mtime": mtime,
                "st_atime": mtime
            }
        else:
            # If it's a native Google Workspace document, compute exact JSON descriptor size
            mime = item.get("mime_type", "")
            is_gdoc = mime.startswith("application/vnd.google-apps.")
            if is_gdoc:
                size = len(self._build_gdoc_payload(item))
                file_mode = 0o644
            else:
                size = item.get("size") or 0
                file_mode = 0o644

            return {
                "st_mode": stat.S_IFREG | file_mode,
                "st_nlink": 1,
                "st_size": size,
                "st_uid": uid,
                "st_gid": gid,
                "st_ctime": mtime,
                "st_mtime": mtime,
                "st_atime": mtime
            }

    def readdir(self, path: str, fh) -> List[str]:
        item = self._resolve_item(path)
        if not item or not item.get("is_directory"):
            raise FuseOSError(errno.ENOENT)

        dirents = [".", ".."]
        children = self.db.get_children(item["id"], account_id=self.account_id)
        # On-demand folder hydration if directory appears empty and hasn't been fetched yet
        if not children and item["id"] not in self._hydrated_dirs and hasattr(self.api, "list_files"):
            self._hydrated_dirs.add(item["id"])
            try:
                resp = self.api.list_files(q=f"'{item['id']}' in parents and trashed = false", page_size=200)
                files = resp.get("files", [])
                if files:
                    now = int(time.time())
                    batch = []
                    for f in files:
                        is_dir = 1 if f.get("mimeType") == "application/vnd.google-apps.folder" else 0
                        batch.append({
                            "id": f["id"],
                            "account_id": self.account_id,
                            "name": f["name"],
                            "parent_id": item["id"],
                            "mime_type": f.get("mimeType", ""),
                            "is_directory": is_dir,
                            "size": int(f.get("size", 0)),
                            "md5_checksum": f.get("md5Checksum", ""),
                            "modified_time": now,
                            "created_time": now,
                            "is_pinned": 0,
                            "is_trashed": 0,
                            "sync_status": "online_only",
                            "local_cache_path": None
                        })
                    self.db.upsert_items_batch(batch)
                    children = self.db.get_children(item["id"], account_id=self.account_id)
            except Exception:
                pass

        for child in children:
            name = child["name"]
            # Append native Google Workspace extension (.gdoc, .gsheet, .gslides, .gform) so Linux MIME opens the installed app
            mime = child.get("mime_type", "")
            if not child.get("is_directory") and mime.startswith("application/vnd.google-apps."):
                ext = self._get_gdoc_extension(mime)
                if not name.endswith(ext):
                    name = f"{name}{ext}"
            dirents.append(name)

        return dirents

    def open(self, path: str, flags: int) -> int:
        item = self._resolve_item(path)
        if not item:
            raise FuseOSError(errno.ENOENT)

        fh = self.fh_counter
        self.fh_counter += 1
        self.open_files[fh] = {
            "item": item,
            "path": path,
            "flags": flags,
            "write_buffer": bytearray() if (flags & (os.O_WRONLY | os.O_RDWR)) else None
        }
        return fh

    def read(self, path: str, size: int, offset: int, fh: int) -> bytes:
        file_info = self.open_files.get(fh)
        if not file_info:
            item = self._resolve_item(path)
        else:
            item = file_info["item"]

        if not item:
            raise FuseOSError(errno.ENOENT)

        if "_virtual_bytes" in item:
            vdata = item["_virtual_bytes"]
            return vdata[offset:offset + size]

        mime = item.get("mime_type", "")
        # Google Docs/Sheets/Slides native JSON descriptor (associated with installed Google Workspace apps)
        if mime.startswith("application/vnd.google-apps."):
            gdoc_bytes = self._build_gdoc_payload(item)
            return gdoc_bytes[offset:offset + size]

        total_size = item.get("size") or 0
        # Stream slice via Chunked Sparse Cache Manager
        return self.cache.read(item["id"], offset, size, total_size)

    def write(self, path: str, data: bytes, offset: int, fh: int) -> int:
        file_info = self.open_files.get(fh)
        if not file_info or file_info["write_buffer"] is None:
            raise FuseOSError(errno.EBADF)

        buf = file_info["write_buffer"]
        if offset > len(buf):
            buf.extend(b"\0" * (offset - len(buf)))
        buf[offset:offset + len(data)] = data
        return len(data)

    def flush(self, path: str, fh: int):
        file_info = self.open_files.get(fh)
        if file_info and file_info["write_buffer"]:
            # Commit dirty file buffer to cache and queue upload
            item = file_info["item"]
            data = bytes(file_info["write_buffer"])
            local_cache_path = self.cache.write_direct(item["id"], data)
            self.db.queue_operation(
                account_id=self.account_id,
                op_type="upload_modify",
                item_id=item["id"],
                source_path=local_cache_path,
                total_bytes=len(data)
            )

    def release(self, path: str, fh: int):
        self.flush(path, fh)
        if fh in self.open_files:
            del self.open_files[fh]

    def create(self, path: str, mode: int, fi=None) -> int:
        parent_path, name = os.path.split(path)
        parent_item = self._resolve_item(parent_path)
        if not parent_item:
            raise FuseOSError(errno.ENOENT)

        # Temporary client-side synthetic ID
        new_id = f"local_{int(time.time() * 1000)}"
        new_item = {
            "id": new_id,
            "account_id": self.account_id,
            "name": name,
            "parent_id": parent_item["id"],
            "mime_type": "application/octet-stream",
            "is_directory": 0,
            "size": 0,
            "md5_checksum": "",
            "modified_time": int(time.time()),
            "created_time": int(time.time()),
            "is_pinned": 0,
            "is_trashed": 0,
            "sync_status": "syncing",
            "local_cache_path": None
        }
        self.db.upsert_item(new_item)

        fh = self.fh_counter
        self.fh_counter += 1
        self.open_files[fh] = {
            "item": new_item,
            "path": path,
            "flags": os.O_WRONLY | os.O_CREAT,
            "write_buffer": bytearray()
        }
        return fh

    def mkdir(self, path: str, mode: int):
        parent_path, name = os.path.split(path)
        parent_item = self._resolve_item(parent_path)
        if not parent_item:
            raise FuseOSError(errno.ENOENT)

        new_id = f"local_dir_{int(time.time() * 1000)}"
        new_dir = {
            "id": new_id,
            "account_id": self.account_id,
            "name": name,
            "parent_id": parent_item["id"],
            "mime_type": "application/vnd.google-apps.folder",
            "is_directory": 1,
            "size": 0,
            "md5_checksum": "",
            "modified_time": int(time.time()),
            "created_time": int(time.time()),
            "is_pinned": 0,
            "is_trashed": 0,
            "sync_status": "syncing",
            "local_cache_path": None
        }
        self.db.upsert_item(new_dir)
        self.db.queue_operation(
            account_id=self.account_id,
            op_type="mkdir",
            item_id=new_id,
            target_path=name
        )

    def unlink(self, path: str):
        item = self._resolve_item(path)
        if not item:
            raise FuseOSError(errno.ENOENT)
        self.db.queue_operation(
            account_id=self.account_id,
            op_type="delete",
            item_id=item["id"]
        )

    def rmdir(self, path: str):
        self.unlink(path)

    def rename(self, old: str, new: str):
        item = self._resolve_item(old)
        if not item:
            raise FuseOSError(errno.ENOENT)
        new_parent_path, new_name = os.path.split(new)
        new_parent = self._resolve_item(new_parent_path)
        if not new_parent:
            raise FuseOSError(errno.ENOENT)

        self.db.queue_operation(
            account_id=self.account_id,
            op_type="rename",
            item_id=item["id"],
            source_path=item["name"],
            target_path=new_name
        )


def _sync_gtk_bookmark(mount_point: str, enable_bookmark: bool):
    """Ensure Nautilus left sidebar always shows GoogleDrive pointing to mount_point."""
    import urllib.parse
    bookmarks_file = os.path.expanduser("~/.config/gtk-3.0/bookmarks")
    os.makedirs(os.path.dirname(bookmarks_file), exist_ok=True)
    uri = "file://" + urllib.parse.quote(mount_point)
    target_line = f"{uri} GoogleDrive"
    lines = []
    if os.path.exists(bookmarks_file):
        try:
            with open(bookmarks_file, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f.readlines() if ln.strip()]
        except Exception:
            lines = []
    # Remove any old GoogleDrive bookmark entries
    filtered = [ln for ln in lines if not ln.endswith(" GoogleDrive") and "GoogleDrive" not in ln]
    if enable_bookmark:
        filtered.append(target_line)
    try:
        with open(bookmarks_file, "w", encoding="utf-8") as f:
            f.write("\n".join(filtered) + ("\n" if filtered else ""))
    except Exception:
        pass


def mount_fuse(mount_point: str, db, cache_manager, api, account_id: str, foreground: bool = False, volume_name: str = ""):
    """Mount the Google Drive FUSE filesystem on Ubuntu (with automatic fallback for AppArmor /run/media)."""
    import subprocess
    fallback_mount = os.path.expanduser(f"~/.cache/google-drive-linux/fuse_mnt_{account_id}")
    subprocess.run(["fusermount3", "-uz", mount_point], stderr=subprocess.DEVNULL, check=False)
    subprocess.run(["fusermount", "-uz", mount_point], stderr=subprocess.DEVNULL, check=False)
    subprocess.run(["fusermount3", "-uz", fallback_mount], stderr=subprocess.DEVNULL, check=False)
    subprocess.run(["fusermount", "-uz", fallback_mount], stderr=subprocess.DEVNULL, check=False)

    if os.path.islink(mount_point):
        try:
            os.unlink(mount_point)
        except OSError:
            pass

    os.makedirs(mount_point, exist_ok=True)
    vol_label = volume_name or os.path.basename(mount_point.rstrip("/")) or "GoogleDrive"
    fs = GoogleDriveFUSE(db, cache_manager, api, account_id, volume_name=vol_label)
    print(f"[VFS] Montando Google Drive ({vol_label}) em: {mount_point}")
    try:
        if FUSE is not None:
            _sync_gtk_bookmark(mount_point, enable_bookmark=False)
            FUSE(fs, mount_point, nothreads=False, foreground=foreground, allow_other=False)
    except Exception as e:
        print(f"[VFS] Montagem direta em '{mount_point}' bloqueada pelo AppArmor ({e}); ativando ponte FUSE transparente...")
        try:
            os.makedirs(fallback_mount, exist_ok=True)
            if os.path.isdir(mount_point) and not os.path.islink(mount_point):
                if not os.listdir(mount_point):
                    os.rmdir(mount_point)
            if not os.path.exists(mount_point) and not os.path.islink(mount_point):
                os.symlink(fallback_mount, mount_point)
            _sync_gtk_bookmark(mount_point, enable_bookmark=True)
            if FUSE is not None:
                FUSE(fs, fallback_mount, nothreads=False, foreground=foreground, allow_other=False)
        except Exception as e2:
            print(f"[VFS] Erro ao montar FUSE no fallback: {e2}")
