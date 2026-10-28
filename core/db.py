"""
Google Drive for Linux - SQLite Metadata & Sync State Database
Replicates the architecture found in Google Drive's internal metadata_sqlite_db.
"""
import sqlite3
import time
import os
from typing import Optional, List, Dict, Any
from core.config import METADATA_DB_PATH, ensure_directories


class MetadataDB:
    def __init__(self, db_path: str = METADATA_DB_PATH):
        ensure_directories()
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Accounts Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                account_id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                display_name TEXT,
                photo_url TEXT,
                storage_total INTEGER DEFAULT 0,
                storage_used INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                auth_data TEXT,
                last_sync INTEGER DEFAULT 0
            );
            """)

            # Items (Files and Folders) Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                name TEXT NOT NULL,
                parent_id TEXT,
                mime_type TEXT,
                is_directory INTEGER DEFAULT 0,
                size INTEGER DEFAULT 0,
                md5_checksum TEXT,
                modified_time INTEGER,
                created_time INTEGER,
                is_pinned INTEGER DEFAULT 0,
                is_trashed INTEGER DEFAULT 0,
                sync_status TEXT DEFAULT 'online_only',
                local_cache_path TEXT,
                version INTEGER DEFAULT 1,
                FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
            );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_parent ON items(parent_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_account ON items(account_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_name ON items(parent_id, name);")

            # Operations / Pending Sync Queue
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT,
                account_id TEXT NOT NULL,
                op_type TEXT NOT NULL,
                source_path TEXT,
                target_path TEXT,
                status TEXT DEFAULT 'pending',
                bytes_transferred INTEGER DEFAULT 0,
                total_bytes INTEGER DEFAULT 0,
                retry_count INTEGER DEFAULT 0,
                error_message TEXT,
                created_at INTEGER,
                updated_at INTEGER
            );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_ops_status ON operations(status);")

            # Cache Chunks / Blocks for Sparse Hydration
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS cache_blocks (
                item_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                offset INTEGER NOT NULL,
                length INTEGER NOT NULL,
                cached_at INTEGER NOT NULL,
                last_access INTEGER NOT NULL,
                is_dirty INTEGER DEFAULT 0,
                PRIMARY KEY (item_id, chunk_index),
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
            );
            """)

            conn.commit()

    # --- Item CRUD ---
    def upsert_item(self, item_data: Dict[str, Any]):
        defaults = {
            "account_id": "",
            "mime_type": "",
            "is_directory": 0,
            "size": 0,
            "md5_checksum": "",
            "modified_time": int(time.time()),
            "created_time": int(time.time()),
            "is_pinned": 0,
            "is_trashed": 0,
            "sync_status": "online_only",
            "local_cache_path": None
        }
        params = defaults.copy()
        params.update(item_data)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO items (
                id, account_id, name, parent_id, mime_type, is_directory,
                size, md5_checksum, modified_time, created_time,
                is_pinned, is_trashed, sync_status, local_cache_path
            ) VALUES (
                :id, :account_id, :name, :parent_id, :mime_type, :is_directory,
                :size, :md5_checksum, :modified_time, :created_time,
                :is_pinned, :is_trashed, :sync_status, :local_cache_path
            )
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                parent_id=excluded.parent_id,
                mime_type=excluded.mime_type,
                is_directory=excluded.is_directory,
                size=excluded.size,
                md5_checksum=excluded.md5_checksum,
                modified_time=excluded.modified_time,
                is_trashed=excluded.is_trashed,
                sync_status=CASE WHEN items.is_pinned=1 THEN 'pinned' ELSE excluded.sync_status END,
                local_cache_path=coalesce(items.local_cache_path, excluded.local_cache_path);
            """, params)
            conn.commit()

    def get_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM items WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_item_by_path(self, parent_id: str, name: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM items WHERE parent_id = ? AND name = ? AND is_trashed = 0", (parent_id, name))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_children(self, parent_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM items WHERE parent_id = ? AND is_trashed = 0 ORDER BY is_directory DESC, name ASC", (parent_id,))
            return [dict(row) for row in cursor.fetchall()]

    def update_sync_status(self, item_id: str, status: str, local_cache_path: Optional[str] = None):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if local_cache_path:
                cursor.execute("UPDATE items SET sync_status = ?, local_cache_path = ? WHERE id = ?", (status, local_cache_path, item_id))
            else:
                cursor.execute("UPDATE items SET sync_status = ? WHERE id = ?", (status, item_id))
            conn.commit()

    def set_pinned(self, item_id: str, is_pinned: bool):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE items SET is_pinned = ?, sync_status = ? WHERE id = ?", (1 if is_pinned else 0, 'pinned' if is_pinned else 'online_only', item_id))
            conn.commit()

    # --- Operations Queue ---
    def queue_operation(self, account_id: str, op_type: str, item_id: Optional[str] = None, source_path: Optional[str] = None, target_path: Optional[str] = None, total_bytes: int = 0) -> int:
        with self._get_connection() as conn:
            now = int(time.time())
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO operations (account_id, item_id, op_type, source_path, target_path, status, total_bytes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """, (account_id, item_id, op_type, source_path, target_path, total_bytes, now, now))
            conn.commit()
            return cursor.lastrowid

    def get_pending_operations(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM operations WHERE status = 'pending' ORDER BY id ASC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def update_operation_status(self, op_id: int, status: str, bytes_transferred: int = 0, error_message: Optional[str] = None):
        with self._get_connection() as conn:
            now = int(time.time())
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE operations
            SET status = ?, bytes_transferred = ?, error_message = ?, updated_at = ?
            WHERE id = ?
            """, (status, bytes_transferred, error_message, now, op_id))
            conn.commit()

    # --- Accounts ---
    def get_accounts(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM accounts WHERE is_active = 1")
            return [dict(row) for row in cursor.fetchall()]

    def upsert_account(self, account_data: Dict[str, Any]):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO accounts (account_id, email, display_name, photo_url, storage_total, storage_used, is_active, auth_data, last_sync)
            VALUES (:account_id, :email, :display_name, :photo_url, :storage_total, :storage_used, :is_active, :auth_data, :last_sync)
            ON CONFLICT(account_id) DO UPDATE SET
                email=excluded.email,
                display_name=excluded.display_name,
                photo_url=excluded.photo_url,
                storage_total=excluded.storage_total,
                storage_used=excluded.storage_used,
                is_active=excluded.is_active,
                auth_data=excluded.auth_data,
                last_sync=excluded.last_sync;
            """, account_data)
            conn.commit()
