"""
Google Drive for Linux - Chunked Sparse Cache Manager
Provides on-demand block hydration, MD5 cache coherence, recursive offline folder pinning,
and throttled LRU cache eviction.
"""
import os
import time
import shutil
import threading
from typing import Optional
from core.config import CACHE_DIR, ensure_directories
from core.docs_converter import is_google_workspace_doc, export_document_offline


class CacheManager:
    def __init__(self, db, api, chunk_size_mb: int = 16, max_cache_size_gb: int = 10, cache_dir: Optional[str] = None):
        ensure_directories()
        self.db = db
        self.api = api
        self.chunk_size = chunk_size_mb * 1024 * 1024
        self.max_cache_bytes = max_cache_size_gb * 1024 * 1024 * 1024
        self.cache_dir = cache_dir or CACHE_DIR
        self._last_quota_check = 0.0
        self._quota_lock = threading.Lock()
        os.makedirs(self.cache_dir, exist_ok=True)

    def _get_item_dir(self, item_id: str) -> str:
        d = os.path.join(self.cache_dir, item_id)
        os.makedirs(d, exist_ok=True)
        return d

    def _get_chunk_path(self, item_id: str, chunk_index: int) -> str:
        return os.path.join(self._get_item_dir(item_id), f"chunk_{chunk_index}.bin")

    def _get_full_cache_path(self, item_id: str) -> str:
        return os.path.join(self._get_item_dir(item_id), "full.bin")

    def invalidate_item_cache(self, item_id: str):
        """Purge all cached chunks and full copy for an item when its cloud content changes."""
        item_dir = os.path.join(self.cache_dir, item_id)
        if os.path.isdir(item_dir):
            try:
                shutil.rmtree(item_dir, ignore_errors=True)
            except Exception:
                pass
        try:
            with self.db._get_connection() as conn:
                conn.execute("DELETE FROM cache_blocks WHERE item_id = ?", (item_id,))
                conn.execute("UPDATE items SET local_cache_path = NULL WHERE id = ?", (item_id,))
                conn.commit()
        except Exception:
            pass

    def invalidate_if_changed(self, item_id: str, new_md5: str):
        """Invalidate local cache if the cloud MD5 checksum changed."""
        if not new_md5:
            return
        existing = self.db.get_item(item_id)
        if existing:
            old_md5 = existing.get("md5_checksum") or ""
            if old_md5 and old_md5 != new_md5:
                self.invalidate_item_cache(item_id)

    def read(self, item_id: str, offset: int, length: int, total_size: int) -> bytes:
        """Read requested byte slice from local cache or stream chunk from Google Drive."""
        if total_size <= 0 or offset >= total_size:
            return b""
        if offset + length > total_size:
            length = total_size - offset

        # Serve directly from full local copy if offline-pinned or previously fully cached
        full_path = self._get_full_cache_path(item_id)
        if os.path.exists(full_path):
            with open(full_path, "rb") as f:
                f.seek(offset)
                return f.read(length)

        start_chunk = offset // self.chunk_size
        end_chunk = (offset + length - 1) // self.chunk_size

        result = bytearray()
        cur_offset = offset
        remaining = length

        for c_idx in range(start_chunk, end_chunk + 1):
            chunk_start_in_file = c_idx * self.chunk_size
            chunk_data = self._ensure_chunk(item_id, c_idx, chunk_start_in_file, total_size)
            if not chunk_data:
                break

            offset_in_chunk = cur_offset - chunk_start_in_file
            bytes_from_chunk = min(remaining, len(chunk_data) - offset_in_chunk)

            if bytes_from_chunk > 0:
                result.extend(chunk_data[offset_in_chunk:offset_in_chunk + bytes_from_chunk])
                cur_offset += bytes_from_chunk
                remaining -= bytes_from_chunk

            if remaining <= 0:
                break

        return bytes(result)

    def _ensure_chunk(self, item_id: str, chunk_index: int, chunk_offset: int, total_size: int) -> bytes:
        chunk_path = self._get_chunk_path(item_id, chunk_index)
        now = int(time.time())

        if os.path.exists(chunk_path):
            with open(chunk_path, "rb") as f:
                data = f.read()
            with self.db._get_connection() as conn:
                conn.execute(
                    "UPDATE cache_blocks SET last_access = ? WHERE item_id = ? AND chunk_index = ?",
                    (now, item_id, chunk_index),
                )
                conn.commit()
            return data

        chunk_len = min(self.chunk_size, total_size - chunk_offset)
        if chunk_len <= 0:
            return b""

        data = self.api.read_range(item_id, chunk_offset, chunk_len)
        if len(data) != chunk_len:
            full_path = self._get_full_cache_path(item_id)
            if self.api.download_full_file(item_id, full_path) and os.path.getsize(full_path) == total_size:
                with open(full_path, "rb") as f:
                    f.seek(chunk_offset)
                    data = f.read(chunk_len)
            elif os.path.exists(full_path):
                os.remove(full_path)
            if len(data) != chunk_len:
                raise OSError(f"Incomplete range read: expected {chunk_len} bytes, got {len(data)}")

        with open(chunk_path, "wb") as f:
            f.write(data)

        with self.db._get_connection() as conn:
            conn.execute("""
            INSERT INTO cache_blocks (item_id, chunk_index, offset, length, cached_at, last_access, is_dirty)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(item_id, chunk_index) DO UPDATE SET last_access=excluded.last_access;
            """, (item_id, chunk_index, chunk_offset, len(data), now, now))
            conn.commit()

        self.enforce_quota_if_needed()
        return data

    def pin_file_offline(self, item_id: str, total_size: int = 0, export_mode: str = "OPEN_DOCUMENT") -> bool:
        """Download entire file (or export Google Workspace doc) for full offline availability."""
        item = self.db.get_item(item_id)
        if item and is_google_workspace_doc(item.get("mime_type", "")):
            exported = export_document_offline(self.api, item, self._get_item_dir(item_id), mode=export_mode)
            if exported:
                self.db.set_pinned(item_id, True)
                self.db.update_sync_status(item_id, "pinned", local_cache_path=exported)
                return True
            return False

        full_path = self._get_full_cache_path(item_id)
        success = self.api.download_full_file(item_id, full_path)
        if success:
            self.db.set_pinned(item_id, True)
            self.db.update_sync_status(item_id, "pinned", local_cache_path=full_path)
        return success

    def pin_folder_offline(self, folder_id: str, account_id: str) -> int:
        """Recursively mark a folder and all its descendants as pinned for background offline hydration."""
        pinned_count = 0
        stack = [folder_id]
        visited = set()
        while stack:
            curr = stack.pop()
            if curr in visited:
                continue
            visited.add(curr)
            self.db.set_pinned(curr, True)
            pinned_count += 1
            for child in self.db.get_children(curr, account_id=account_id):
                if child.get("is_directory"):
                    stack.append(child["id"])
                else:
                    self.db.set_pinned(child["id"], True)
                    pinned_count += 1
        return pinned_count

    def unpin_file(self, item_id: str):
        """Unpin file and free full local cached copy."""
        item = self.db.get_item(item_id)
        lp = item.get("local_cache_path") if item else None
        if lp and os.path.exists(lp):
            try:
                os.remove(lp)
            except Exception:
                pass
        full_path = self._get_full_cache_path(item_id)
        if os.path.exists(full_path):
            try:
                os.remove(full_path)
            except Exception:
                pass
        self.db.set_pinned(item_id, False)
        self.db.update_sync_status(item_id, "online_only", local_cache_path=None)

    def write_direct(self, item_id: str, data: bytes) -> str:
        """Write modified file directly to full cache and mark dirty for sync worker."""
        full_path = self._get_full_cache_path(item_id)
        with open(full_path, "wb") as f:
            f.write(data)
        self.db.update_sync_status(item_id, "syncing", local_cache_path=full_path)
        return full_path

    def enforce_quota_if_needed(self, force: bool = False):
        """Evict oldest unpinned chunks if cache exceeds quota (throttled to avoid constant os.walk)."""
        now = time.time()
        if not force and (now - self._last_quota_check < 30.0):
            return
        if not self._quota_lock.acquire(blocking=False):
            return
        try:
            self._last_quota_check = now
            total_cache = 0
            for root, _dirs, files in os.walk(self.cache_dir):
                for file in files:
                    try:
                        total_cache += os.path.getsize(os.path.join(root, file))
                    except OSError:
                        pass

            if total_cache > self.max_cache_bytes:
                with self.db._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                    SELECT b.item_id, b.chunk_index
                    FROM cache_blocks b
                    JOIN items i ON b.item_id = i.id
                    WHERE i.is_pinned = 0
                    ORDER BY b.last_access ASC
                    LIMIT 50;
                    """)
                    rows = cursor.fetchall()

                    for row in rows:
                        item_id, chunk_index = row[0], row[1]
                        p = self._get_chunk_path(item_id, chunk_index)
                        if os.path.exists(p):
                            try:
                                os.remove(p)
                            except Exception:
                                pass
                        cursor.execute("DELETE FROM cache_blocks WHERE item_id = ? AND chunk_index = ?", (item_id, chunk_index))
                    conn.commit()
        finally:
            self._quota_lock.release()
