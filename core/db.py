"""
Google Drive for Linux - SQLite Metadata & Sync State Database
Replicates the architecture found in Google Drive's internal metadata_sqlite_db.
"""
import sqlite3
import contextlib
import time
import os
from typing import Optional, List, Dict, Any
from core.config import METADATA_DB_PATH, ensure_directories


class MetadataDB:
    def __init__(self, db_path: str = METADATA_DB_PATH):
        ensure_directories()
        self.db_path = db_path
        self._init_db()

    @contextlib.contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            yield conn
        finally:
            conn.close()

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

            # Incremental Delta Sync Tokens (Drive API v3 changes.list)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS sync_tokens (
                account_id TEXT PRIMARY KEY,
                changes_page_token TEXT,
                initial_scan_complete INTEGER DEFAULT 0,
                updated_at INTEGER DEFAULT 0
            );
            """)

            # Unresolved / Historical Sync Conflicts
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL,
                item_id TEXT,
                conflict_type TEXT NOT NULL,
                local_path TEXT,
                cloud_name TEXT,
                local_md5 TEXT,
                cloud_md5 TEXT,
                resolution TEXT DEFAULT 'pending',
                created_at INTEGER,
                resolved_at INTEGER
            );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_conflicts_status ON conflicts(account_id, resolution);")

            # Structured Activity Feed Log
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                item_id TEXT,
                item_name TEXT,
                details TEXT,
                created_at INTEGER NOT NULL
            );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log(created_at DESC);")

            # Hybrid Local Selective Sync Rules (2-way / 1-way upload / 1-way download)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS local_sync_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL,
                local_path TEXT NOT NULL UNIQUE,
                cloud_folder_id TEXT NOT NULL DEFAULT 'root',
                sync_mode TEXT NOT NULL DEFAULT 'TWO_WAY',
                export_mode TEXT NOT NULL DEFAULT 'LINK',
                enabled INTEGER DEFAULT 1,
                last_item_count INTEGER DEFAULT 0,
                created_at INTEGER,
                updated_at INTEGER
            );
            """)

            # Safe non-destructive schema migrations for existing DBs
            existing_cols = {row[1] for row in cursor.execute("PRAGMA table_info(items)").fetchall()}
            for col_name, col_def in (
                ("is_shared", "INTEGER DEFAULT 0"),
                ("drive_id", "TEXT DEFAULT ''"),
                ("is_starred", "INTEGER DEFAULT 0"),
            ):
                if col_name not in existing_cols:
                    cursor.execute(f"ALTER TABLE items ADD COLUMN {col_name} {col_def};")

            conn.commit()

    # --- Item CRUD ---
    def upsert_item(self, item_data: Dict[str, Any]):
        self.upsert_items_batch([item_data])

    def upsert_items_batch(self, items_list: List[Dict[str, Any]]):
        if not items_list:
            return
        now = int(time.time())
        defaults = {
            "account_id": "",
            "mime_type": "",
            "is_directory": 0,
            "size": 0,
            "md5_checksum": "",
            "modified_time": now,
            "created_time": now,
            "is_pinned": 0,
            "is_trashed": 0,
            "sync_status": "online_only",
            "local_cache_path": None,
            "is_shared": 0,
            "drive_id": "",
            "is_starred": 0,
        }
        batch = []
        for item_data in items_list:
            params = defaults.copy()
            params.update(item_data)
            batch.append(params)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany("""
            INSERT INTO items (
                id, account_id, name, parent_id, mime_type, is_directory,
                size, md5_checksum, modified_time, created_time,
                is_pinned, is_trashed, sync_status, local_cache_path,
                is_shared, drive_id, is_starred
            ) VALUES (
                :id, :account_id, :name, :parent_id, :mime_type, :is_directory,
                :size, :md5_checksum, :modified_time, :created_time,
                :is_pinned, :is_trashed, :sync_status, :local_cache_path,
                :is_shared, :drive_id, :is_starred
            )
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                parent_id=excluded.parent_id,
                mime_type=excluded.mime_type,
                is_directory=excluded.is_directory,
                size=excluded.size,
                md5_checksum=excluded.md5_checksum,
                modified_time=excluded.modified_time,
                is_pinned=MAX(items.is_pinned, excluded.is_pinned),
                is_trashed=excluded.is_trashed,
                is_shared=MAX(items.is_shared, excluded.is_shared),
                drive_id=CASE WHEN excluded.drive_id != '' THEN excluded.drive_id ELSE items.drive_id END,
                is_starred=excluded.is_starred,
                sync_status=CASE WHEN items.is_pinned=1 THEN 'pinned' ELSE excluded.sync_status END,
                local_cache_path=coalesce(items.local_cache_path, excluded.local_cache_path);
            """, batch)
            conn.commit()

    def get_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM items WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_item_by_path(self, parent_id: str, name: str, account_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            names_to_try = [name]
            if "／" in name:
                names_to_try.append(name.replace("／", "/"))
            if "/" in name:
                names_to_try.append(name.replace("/", "／"))

            for try_name in names_to_try:
                if account_id:
                    cursor.execute(
                        "SELECT * FROM items WHERE parent_id = ? AND name = ? AND account_id = ? AND is_trashed = 0",
                        (parent_id, try_name, account_id)
                    )
                else:
                    cursor.execute("SELECT * FROM items WHERE parent_id = ? AND name = ? AND is_trashed = 0", (parent_id, try_name))
                row = cursor.fetchone()
                if row:
                    return dict(row)
            return None

    def get_children(self, parent_id: str, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if account_id:
                cursor.execute(
                    "SELECT * FROM items WHERE parent_id = ? AND account_id = ? AND is_trashed = 0 ORDER BY is_directory DESC, name ASC",
                    (parent_id, account_id)
                )
            else:
                cursor.execute("SELECT * FROM items WHERE parent_id = ? AND is_trashed = 0 ORDER BY is_directory DESC, name ASC", (parent_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_sync_stats(self, account_id: Optional[str] = None) -> Dict[str, int]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if account_id:
                cursor.execute("""
                    SELECT
                        COUNT(*),
                        COALESCE(SUM(CASE WHEN is_directory = 0 THEN 1 ELSE 0 END), 0),
                        COALESCE(SUM(CASE WHEN is_directory = 1 THEN 1 ELSE 0 END), 0),
                        COALESCE(SUM(size), 0)
                    FROM items
                    WHERE is_trashed = 0 AND account_id = ?
                """, (account_id,))
            else:
                cursor.execute("""
                    SELECT
                        COUNT(*),
                        COALESCE(SUM(CASE WHEN is_directory = 0 THEN 1 ELSE 0 END), 0),
                        COALESCE(SUM(CASE WHEN is_directory = 1 THEN 1 ELSE 0 END), 0),
                        COALESCE(SUM(size), 0)
                    FROM items
                    WHERE is_trashed = 0
                """)
            row = cursor.fetchone()
            total_items = row[0] if row else 0
            total_files = row[1] if row else 0
            total_dirs = row[2] if row else 0
            indexed_bytes = row[3] if row else 0
            if account_id:
                cursor.execute("SELECT COUNT(*) FROM operations WHERE status IN ('pending', 'running') AND account_id = ?", (account_id,))
            else:
                cursor.execute("SELECT COUNT(*) FROM operations WHERE status IN ('pending', 'running')")
            op_row = cursor.fetchone()
            pending_ops = op_row[0] if op_row else 0
            return {
                "total_items": total_items,
                "total_files": total_files,
                "total_dirs": total_dirs,
                "indexed_bytes": indexed_bytes,
                "pending_ops": pending_ops,
            }

    def get_relative_path(self, item_id: str) -> str:
        """Reconstruct the relative path of an item inside its account mount (e.g. 'Pasta/Subpasta/arquivo.pdf')."""
        parts = []
        visited = set()
        curr_id = item_id
        with self._get_connection() as conn:
            cursor = conn.cursor()
            while curr_id and curr_id != "root" and curr_id not in visited and len(parts) < 30:
                visited.add(curr_id)
                cursor.execute("SELECT name, parent_id, is_directory, mime_type FROM items WHERE id = ?", (curr_id,))
                row = cursor.fetchone()
                if not row:
                    break
                name = row["name"]
                if not parts:
                    mime = row["mime_type"] or ""
                    if not row["is_directory"] and mime.startswith("application/vnd.google-apps."):
                        if "spreadsheet" in mime:
                            ext = ".gsheet"
                        elif "presentation" in mime:
                            ext = ".gslides"
                        elif "form" in mime:
                            ext = ".gform"
                        else:
                            ext = ".gdoc"
                        if not name.endswith(ext):
                            name = f"{name}{ext}"
                parts.append(name)
                curr_id = row["parent_id"]
        parts.reverse()
        return "/".join(parts)

    def search_items(self, query: str, limit: int = 80, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search local metadata catalog by name (across active or all connected accounts) and include relative path."""
        q_clean = (query or "").strip()
        if not q_clean:
            return []
        like_pat = f"%{q_clean}%"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if account_id:
                cursor.execute("""
                    SELECT id, account_id, name, parent_id, mime_type, is_directory, size, sync_status, modified_time
                    FROM items
                    WHERE is_trashed = 0 AND account_id = ? AND name LIKE ?
                    ORDER BY is_directory DESC, modified_time DESC, name ASC
                    LIMIT ?
                """, (account_id, like_pat, limit))
            else:
                cursor.execute("""
                    SELECT id, account_id, name, parent_id, mime_type, is_directory, size, sync_status, modified_time
                    FROM items
                    WHERE is_trashed = 0 AND name LIKE ?
                    ORDER BY is_directory DESC, modified_time DESC, name ASC
                    LIMIT ?
                """, (like_pat, limit))
            rows = [dict(r) for r in cursor.fetchall()]

        for r in rows:
            r["relative_path"] = self.get_relative_path(r["id"])
        return rows

    def get_recent_items(self, limit: int = 50, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if account_id:
                cursor.execute("""
                    SELECT id, name, mime_type, is_directory, size, sync_status, modified_time
                    FROM items
                    WHERE is_trashed = 0 AND account_id = ?
                    ORDER BY modified_time DESC, is_directory DESC, name ASC
                    LIMIT ?
                """, (account_id, limit))
            else:
                cursor.execute("""
                    SELECT id, name, mime_type, is_directory, size, sync_status, modified_time
                    FROM items
                    WHERE is_trashed = 0
                    ORDER BY modified_time DESC, is_directory DESC, name ASC
                    LIMIT ?
                """, (limit,))
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

    def get_pending_operations(self, limit: int = 20, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if account_id:
                cursor.execute("SELECT * FROM operations WHERE status = 'pending' AND account_id = ? ORDER BY id ASC LIMIT ?", (account_id, limit))
            else:
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
            cursor.execute("SELECT * FROM accounts WHERE is_active = 1 ORDER BY rowid ASC")
            rows = [dict(row) for row in cursor.fetchall()]
        try:
            from core.config import _read_raw_config
            active_id = _read_raw_config().get("active_account_id")
            if active_id and len(rows) > 1:
                rows.sort(key=lambda r: 0 if r.get("account_id") == active_id else 1)
        except Exception:
            pass
        return rows

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

    # --- Incremental Delta Sync Tokens ---
    def get_sync_token(self, account_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM sync_tokens WHERE account_id = ?", (account_id,)
            ).fetchone()
            return dict(row) if row else None

    def save_sync_token(self, account_id: str, page_token: Optional[str], initial_scan_complete: bool = True):
        now = int(time.time())
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO sync_tokens (account_id, changes_page_token, initial_scan_complete, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    changes_page_token = COALESCE(excluded.changes_page_token, sync_tokens.changes_page_token),
                    initial_scan_complete = MAX(sync_tokens.initial_scan_complete, excluded.initial_scan_complete),
                    updated_at = excluded.updated_at;
            """, (account_id, page_token, 1 if initial_scan_complete else 0, now))
            conn.commit()

    def mark_item_trashed(self, item_id: str, trashed: bool = True):
        with self._get_connection() as conn:
            conn.execute("UPDATE items SET is_trashed = ? WHERE id = ?", (1 if trashed else 0, item_id))
            conn.commit()

    def replace_synthetic_id(self, old_id: str, new_id: str):
        """Promote a temporary local_* synthetic ID to its real Google Drive ID."""
        if not old_id or not new_id or old_id == new_id:
            return
        with self._get_connection() as conn:
            conn.execute("UPDATE items SET parent_id = ? WHERE parent_id = ?", (new_id, old_id))
            conn.execute("UPDATE operations SET item_id = ? WHERE item_id = ?", (new_id, old_id))
            conn.execute("DELETE FROM items WHERE id = ?", (old_id,))
            conn.commit()

    # --- Offline Pinning Helpers ---
    def get_pinned_items_needing_hydration(self, account_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Return pinned non-directory files that do not yet have a local_cache_path on disk."""
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM items
                WHERE account_id = ? AND is_pinned = 1 AND is_directory = 0 AND is_trashed = 0
                LIMIT ?
            """, (account_id, limit)).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                lp = d.get("local_cache_path")
                if not lp or not os.path.exists(lp):
                    result.append(d)
            return result

    def get_shared_items(self, account_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM items
                WHERE account_id = ? AND is_shared = 1 AND is_trashed = 0
                ORDER BY is_directory DESC, name ASC
            """, (account_id,)).fetchall()
            return [dict(r) for r in rows]

    # --- Conflicts ---
    def record_conflict(
        self,
        account_id: str,
        item_id: str,
        conflict_type: str,
        local_path: str = "",
        cloud_name: str = "",
        local_md5: str = "",
        cloud_md5: str = "",
        resolution: str = "pending",
    ) -> int:
        now = int(time.time())
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO conflicts (account_id, item_id, conflict_type, local_path, cloud_name, local_md5, cloud_md5, resolution, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (account_id, item_id, conflict_type, local_path, cloud_name, local_md5, cloud_md5, resolution, now))
            conn.commit()
            return cur.lastrowid

    def get_pending_conflicts(self, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            if account_id:
                rows = conn.execute(
                    "SELECT * FROM conflicts WHERE account_id = ? AND resolution = 'pending' ORDER BY created_at DESC",
                    (account_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM conflicts WHERE resolution = 'pending' ORDER BY created_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def resolve_conflict(self, conflict_id: int, resolution: str):
        now = int(time.time())
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE conflicts SET resolution = ?, resolved_at = ? WHERE id = ?",
                (resolution, now, conflict_id),
            )
            conn.commit()

    # --- Structured Activity Feed ---
    def log_activity(self, account_id: str, action_type: str, item_id: str = "", item_name: str = "", details: str = ""):
        now = int(time.time())
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO activity_log (account_id, action_type, item_id, item_name, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (account_id, action_type, item_id, item_name, details, now))
            # Keep activity table bounded to latest 2000 entries
            conn.execute("""
                DELETE FROM activity_log WHERE id NOT IN (
                    SELECT id FROM activity_log ORDER BY id DESC LIMIT 2000
                )
            """)
            conn.commit()

    def get_activity_log(self, account_id: Optional[str] = None, action_type: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        clauses = []
        params = []
        if account_id:
            clauses.append("account_id = ?")
            params.append(account_id)
        if action_type:
            clauses.append("action_type = ?")
            params.append(action_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._get_connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM activity_log {where} ORDER BY created_at DESC, id DESC LIMIT ?",
                tuple(params),
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Local Selective Sync Rules ---
    def upsert_local_sync_rule(
        self,
        account_id: str,
        local_path: str,
        cloud_folder_id: str = "root",
        sync_mode: str = "TWO_WAY",
        export_mode: str = "LINK",
        enabled: bool = True,
    ):
        now = int(time.time())
        clean_path = os.path.abspath(os.path.expanduser(local_path))
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO local_sync_rules (account_id, local_path, cloud_folder_id, sync_mode, export_mode, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(local_path) DO UPDATE SET
                    account_id = excluded.account_id,
                    cloud_folder_id = excluded.cloud_folder_id,
                    sync_mode = excluded.sync_mode,
                    export_mode = excluded.export_mode,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at;
            """, (account_id, clean_path, cloud_folder_id, sync_mode, export_mode, 1 if enabled else 0, now, now))
            conn.commit()

    def get_local_sync_rules(self, account_id: Optional[str] = None, enabled_only: bool = True) -> List[Dict[str, Any]]:
        clauses = []
        params = []
        if account_id:
            clauses.append("account_id = ?")
            params.append(account_id)
        if enabled_only:
            clauses.append("enabled = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._get_connection() as conn:
            rows = conn.execute(f"SELECT * FROM local_sync_rules {where} ORDER BY id ASC", tuple(params)).fetchall()
            return [dict(r) for r in rows]

    def update_local_sync_rule_count(self, rule_id: int, item_count: int):
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE local_sync_rules SET last_item_count = ?, updated_at = ? WHERE id = ?",
                (item_count, int(time.time()), rule_id),
            )
            conn.commit()

    def delete_local_sync_rule(self, local_path: str):
        clean_path = os.path.abspath(os.path.expanduser(local_path))
        with self._get_connection() as conn:
            conn.execute("DELETE FROM local_sync_rules WHERE local_path = ?", (clean_path,))
            conn.commit()
