"""
Google Drive for Linux - FUSE Virtual File System Engine
Provides an on-demand virtual filesystem mount (similar to Google Drive on Windows/macOS)
Compatible with Linux libfuse3 via fusepy or pyfuse3.
"""
import os
import sys
import errno
import stat
import time
from typing import Dict, Any, Optional, List

# Try importing fusepy; provide stub if running in environment without libfuse
try:
    from fuse import FUSE, FuseOSError, Operations
except ImportError:
    # Stub for systems without fusepy installed yet
    class Operations:
        pass
    class FuseOSError(Exception):
        def __init__(self, errno_val):
            self.errno = errno_val


class GoogleDriveFUSE(Operations):
    def __init__(self, db, cache_manager, api, account_id: str):
        self.db = db
        self.cache = cache_manager
        self.api = api
        self.account_id = account_id
        self.open_files: Dict[int, Dict[str, Any]] = {}
        self.fh_counter = 1

    def _resolve_item(self, path: str) -> Optional[Dict[str, Any]]:
        """Resolve a POSIX path (e.g. /Folder/Subfolder/file.pdf) to an item record in SQLite."""
        if path in ("/", ""):
            return {"id": "root", "name": "root", "is_directory": 1, "size": 0, "modified_time": int(time.time())}

        parts = [p for p in path.strip("/").split("/") if p]
        current_parent = "root"
        item = None

        for part in parts:
            # Handle Google Docs extension virtual mapping
            real_name = part
            if part.endswith(".desktop") and not part.startswith("."):
                real_name = part[:-8]

            item = self.db.get_item_by_path(current_parent, real_name)
            if not item:
                # Fallback to direct name lookup
                item = self.db.get_item_by_path(current_parent, part)
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

        if item.get("is_directory"):
            return {
                "st_mode": stat.S_IFDIR | 0o755,
                "st_nlink": 2,
                "st_size": 4096,
                "st_ctime": mtime,
                "st_mtime": mtime,
                "st_atime": mtime
            }
        else:
            # If it's a native Google doc, synthesize desktop launcher size
            mime = item.get("mime_type", "")
            if mime.startswith("application/vnd.google-apps."):
                size = 380
            else:
                size = item.get("size") or 0

            return {
                "st_mode": stat.S_IFREG | 0o644,
                "st_nlink": 1,
                "st_size": size,
                "st_ctime": mtime,
                "st_mtime": mtime,
                "st_atime": mtime
            }

    def readdir(self, path: str, fh) -> List[str]:
        item = self._resolve_item(path)
        if not item or not item.get("is_directory"):
            raise FuseOSError(errno.ENOENT)

        dirents = [".", ".."]
        children = self.db.get_children(item["id"])
        for child in children:
            name = child["name"]
            # Append .desktop to Google Docs so Linux launches them in browser
            mime = child.get("mime_type", "")
            if mime.startswith("application/vnd.google-apps.") and not name.endswith(".desktop"):
                name = f"{name}.desktop"
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

        mime = item.get("mime_type", "")
        # Google Docs synthetic .desktop launcher content (Standalone App Window mode)
        if mime.startswith("application/vnd.google-apps."):
            doc_type = "doc"
            icon_name = "docs.png"
            wm_class = "google-docs"
            category = "WordProcessor"

            if "spreadsheet" in mime:
                doc_type = "sheet"
                icon_name = "sheets.png"
                wm_class = "google-sheets"
                category = "Spreadsheet"
            elif "presentation" in mime:
                doc_type = "slide"
                icon_name = "slides.png"
                wm_class = "google-slides"
                category = "Presentation"

            icon_path = f"/opt/google-drive-linux/assets/icons/{icon_name}"
            desktop_content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name={item['name']}
Comment=Google {doc_type.capitalize()} (Janela de Aplicativo)
Exec=/usr/local/bin/gdrive-doc-launcher {item['id']} {doc_type}
Icon={icon_path}
Terminal=false
StartupNotify=true
StartupWMClass={wm_class}
Categories=Office;{category};
""".encode("utf-8")
            return desktop_content[offset:offset + size]

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


def mount_fuse(mount_point: str, db, cache_manager, api, account_id: str, foreground: bool = False):
    """Mount the Google Drive FUSE filesystem on Ubuntu."""
    os.makedirs(mount_point, exist_ok=True)
    fs = GoogleDriveFUSE(db, cache_manager, api, account_id)
    print(f"[VFS] Montando Google Drive em: {mount_point}")
    try:
        FUSE(fs, mount_point, nothreads=False, foreground=foreground, allow_other=False)
    except Exception as e:
        print(f"[VFS] Erro ao montar FUSE: {e}")
