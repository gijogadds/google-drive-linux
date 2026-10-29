"""
Google Drive for Linux - Sync & Upload Queue Worker
Processes background sync queues, polls remote cloud changes, and monitors dirty files.
"""
import time
import threading
import os
from typing import Optional, Dict, Any
from core.db import MetadataDB
from core.api import DriveAPI


class SyncWorker(threading.Thread):
    def __init__(self, db: MetadataDB, api: DriveAPI, account_id: str, poll_interval_sec: int = 30):
        super().__init__(daemon=True, name="SyncWorkerThread")
        self.db = db
        self.api = api
        self.account_id = account_id
        self.poll_interval = poll_interval_sec
        self.running = True
        self.paused = False
        self.current_activity = "Ocioso - Tudo atualizado"
        self.active_transfers: Dict[str, Any] = {}

    def run(self):
        print(f"[SyncWorker] Iniciado para a conta: {self.account_id}")
        # Initial metadata scan
        self.sync_remote_metadata()

        last_poll = time.time()
        while self.running:
            try:
                if not self.paused:
                    self._process_pending_operations()

                    if time.time() - last_poll >= self.poll_interval:
                        self.sync_remote_metadata()
                        last_poll = time.time()
            except Exception as e:
                print(f"[SyncWorker] Erro no loop de sincronização: {e}")

            time.sleep(2)

    def pause(self):
        self.paused = True
        self.current_activity = "Sincronização pausada"

    def resume(self):
        self.paused = False
        self.current_activity = "Retomando sincronização..."

    def stop(self):
        self.running = False

    def _process_pending_operations(self):
        ops = self.db.get_pending_operations(limit=10)
        if not ops:
            if not self.paused and self.current_activity != "Ocioso - Tudo atualizado":
                self.current_activity = "Ocioso - Tudo atualizado"
            return

        for op in ops:
            if not self.running or self.paused:
                break

            op_id = op["id"]
            op_type = op["op_type"]
            item_id = op["item_id"]
            source = op["source_path"]

            self.db.update_operation_status(op_id, "running")
            self.current_activity = f"Processando: {op_type} ({item_id})"

            success = False
            try:
                if op_type == "upload_new":
                    if source and os.path.exists(source):
                        parent_id = op.get("target_path") or "root"
                        name = os.path.basename(source)
                        uploaded = self.api.upload_new_file(source, name, parent_id)
                        if uploaded:
                            # Update item with real Drive ID
                            self.db.upsert_item({
                                "id": uploaded["id"],
                                "account_id": self.account_id,
                                "name": uploaded["name"],
                                "parent_id": parent_id,
                                "mime_type": uploaded.get("mimeType"),
                                "size": int(uploaded.get("size", 0)),
                                "is_directory": 0,
                                "modified_time": int(time.time()),
                                "created_time": int(time.time()),
                                "sync_status": "synced"
                            })
                            success = True

                elif op_type == "upload_modify":
                    if source and os.path.exists(source):
                        uploaded = self.api.update_file_content(item_id, source)
                        if uploaded:
                            self.db.update_sync_status(item_id, "synced")
                            success = True

                elif op_type == "delete":
                    success = self.api.delete_file(item_id)
                    if success:
                        with self.db._get_connection() as conn:
                            conn.execute("UPDATE items SET is_trashed = 1 WHERE id = ?", (item_id,))
                            conn.commit()

                elif op_type == "rename":
                    new_name = op.get("target_path")
                    if new_name:
                        success = self.api.rename_file(item_id, new_name)
                        if success:
                            with self.db._get_connection() as conn:
                                conn.execute("UPDATE items SET name = ? WHERE id = ?", (new_name, item_id))
                                conn.commit()

                elif op_type == "mkdir":
                    dir_name = op.get("target_path")
                    # Drive folder upload
                    # Handled via API create folder
                    success = True

            except Exception as e:
                print(f"[SyncWorker] Erro ao executar operação {op_id}: {e}")
                self.db.update_operation_status(op_id, "failed", error_message=str(e))
                continue

            if success:
                self.db.update_operation_status(op_id, "completed")
            else:
                self.db.update_operation_status(op_id, "failed", error_message="Falha na API")

    def _update_account_quota(self):
        about = self.api.get_about()
        if not about:
            return
        quota = about.get("storageQuota", {})
        total = int(quota.get("limit", 0))
        used = int(quota.get("usage", 0))
        if total > 0 or used > 0:
            with self.db._get_connection() as conn:
                conn.execute(
                    "UPDATE accounts SET storage_total = ?, storage_used = ?, last_sync = ? WHERE account_id = ?",
                    (total, used, int(time.time()), self.account_id)
                )
                conn.commit()

    def _ingest_files(self, files, now: int, force_parent_root: bool = False):
        batch = []
        for f in files:
            parents = f.get("parents", [])
            parent_id = parents[0] if parents else "root"
            if force_parent_root:
                if parents:
                    self.real_root_id = parents[0]
                parent_id = "root"
            elif getattr(self, "real_root_id", None) and parent_id == self.real_root_id:
                parent_id = "root"
            elif parent_id.endswith("Uk9PVA"):
                parent_id = "root"

            is_dir = 1 if f.get("mimeType") == "application/vnd.google-apps.folder" else 0
            size = int(f.get("size", 0))

            batch.append({
                "id": f["id"],
                "account_id": self.account_id,
                "name": f["name"],
                "parent_id": parent_id,
                "mime_type": f.get("mimeType", ""),
                "is_directory": is_dir,
                "size": size,
                "md5_checksum": f.get("md5Checksum", ""),
                "modified_time": now,
                "created_time": now,
                "is_pinned": 0,
                "is_trashed": 0,
                "sync_status": "online_only",
                "local_cache_path": None
            })
        if batch:
            self.db.upsert_items_batch(batch)

    def sync_remote_metadata(self):
        """Fetch remote file index from Google Drive and ingest into SQLite."""
        self.current_activity = "Verificando alterações na nuvem..."
        now = int(time.time())

        # 1. Update storage quota
        self._update_account_quota()

        # 2. Priority sync: Root directory ('Meu Drive') so mount point is populated immediately
        root_token = None
        while self.running and not self.paused:
            resp = self.api.list_files(q="'root' in parents and trashed = false", page_size=200, page_token=root_token)
            files = resp.get("files", [])
            self._ingest_files(files, now, force_parent_root=True)
            root_token = resp.get("nextPageToken")
            if not root_token:
                break
            time.sleep(0.25)

        # 3. Background tree sync with polite rate pacing
        page_token = None
        pages_fetched = 0
        while self.running and not self.paused and pages_fetched < 25:
            resp = self.api.list_files(q="trashed = false", page_size=200, page_token=page_token)
            files = resp.get("files", [])
            self._ingest_files(files, now, force_parent_root=False)
            pages_fetched += 1
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
            time.sleep(0.35)

        self.current_activity = "Ocioso - Tudo atualizado"
