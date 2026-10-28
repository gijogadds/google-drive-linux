"""
Google Drive for Linux - Chunked Sparse Cache Manager
Provides on-demand block hydration, local chunk management, and LRU cache eviction.
"""
import os
import time
import shutil
from pathlib import Path
from typing import Optional, Tuple
from core.config import CACHE_DIR, ensure_directories


class CacheManager:
    def __init__(self, db, api, chunk_size_mb: int = 16, max_cache_size_gb: int = 10):
        ensure_directories()
        self.db = db
        self.api = api
        self.chunk_size = chunk_size_mb * 1024 * 1024
        self.max_cache_bytes = max_cache_size_gb * 1024 * 1024 * 1024
        self.cache_dir = CACHE_DIR

    def _get_item_dir(self, item_id: str) -> str:
        d = os.path.join(self.cache_dir, item_id)
        os.makedirs(d, exist_ok=True)
        return d

    def _get_chunk_path(self, item_id: str, chunk_index: int) -> str:
        return os.path.join(self._get_item_dir(item_id), f"chunk_{chunk_index}.bin")

    def _get_full_cache_path(self, item_id: str) -> str:
        return os.path.join(self._get_item_dir(item_id), "full.bin")

    def read(self, item_id: str, offset: int, length: int, total_size: int) -> bytes:
        """Read requested byte slice from local cache or stream chunk from Google Drive."""
        if total_size <= 0:
            return b""

        # Cap length to remaining file size
        if offset >= total_size:
            return b""
        if offset + length > total_size:
            length = total_size - offset

        # Check if full file is already cached (e.g. offline pinned or previously fully downloaded)
        full_path = self._get_full_cache_path(item_id)
        if os.path.exists(full_path):
            with open(full_path, "rb") as f:
                f.seek(offset)
                return f.read(length)

        # Otherwise, calculate which chunks are needed
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

        # If chunk is already locally cached, return it
        if os.path.exists(chunk_path):
            with open(chunk_path, "rb") as f:
                data = f.read()
            # Update access time in DB
            with self.db._get_connection() as conn:
                conn.execute(
                    "UPDATE cache_blocks SET last_access = ? WHERE item_id = ? AND chunk_index = ?",
                    (now, item_id, chunk_index)
                )
                conn.commit()
            return data

        # Download chunk from Google Drive API
        chunk_len = min(self.chunk_size, total_size - chunk_offset)
        if chunk_len <= 0:
            return b""

        data = self.api.read_range(item_id, chunk_offset, chunk_len)
        if not data:
            return b""

        # Save to disk
        with open(chunk_path, "wb") as f:
            f.write(data)

        # Record block in DB
        with self.db._get_connection() as conn:
            conn.execute("""
            INSERT INTO cache_blocks (item_id, chunk_index, offset, length, cached_at, last_access, is_dirty)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(item_id, chunk_index) DO UPDATE SET last_access=excluded.last_access;
            """, (item_id, chunk_index, chunk_offset, len(data), now, now))
            conn.commit()

        # Check for cache pruning if needed
        self.enforce_quota_if_needed()
        return data

    def pin_file_offline(self, item_id: str, total_size: int) -> bool:
        """Download entire file for offline availability (Mirror mode)."""
        full_path = self._get_full_cache_path(item_id)
        success = self.api.download_full_file(item_id, full_path)
        if success:
            self.db.set_pinned(item_id, True)
            self.db.update_sync_status(item_id, "pinned", local_cache_path=full_path)
        return success

    def unpin_file(self, item_id: str):
        """Unpin file and free full local cached copy."""
        full_path = self._get_full_cache_path(item_id)
        if os.path.exists(full_path):
            os.remove(full_path)
        self.db.set_pinned(item_id, False)
        self.db.update_sync_status(item_id, "online_only", local_cache_path=None)

    def write_direct(self, item_id: str, data: bytes) -> str:
        """Write modified file directly to full cache and mark dirty for sync worker."""
        full_path = self._get_full_cache_path(item_id)
        with open(full_path, "wb") as f:
            f.write(data)
        self.db.update_sync_status(item_id, "syncing", local_cache_path=full_path)
        return full_path

    def enforce_quota_if_needed(self):
        """Evict oldest unpinned chunks if cache exceeds quota."""
        total_cache = 0
        for root, dirs, files in os.walk(self.cache_dir):
            for file in files:
                total_cache += os.path.getsize(os.path.join(root, file))

        if total_cache > self.max_cache_bytes:
            # Query oldest unpinned chunks
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
