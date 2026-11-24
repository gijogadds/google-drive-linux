"""
Google Drive for Linux - Next-Gen Sync & Upload Queue Worker
Features:
  - True incremental delta sync via Drive API v3 changes.list (eliminates rate limits)
  - Bounded ThreadPoolExecutor concurrency (max 4 parallel transfers per account)
  - Real mkdir, safe trash, and 4-case Conflict Resolution Matrix before upload
  - Automatic background hydration for offline-pinned files & folders
  - .gitignore-compatible Ignore Rules filtering
  - Accurate RFC 3339 timestamp parsing & structured activity feed logging
"""
import time
import threading
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Dict, Any, List

from core.db import MetadataDB
from core.api import DriveAPI
from core.ignore import IgnoreRulesEngine
from core.conflicts import ConflictResolver, compute_file_md5, make_conflict_copy_name


def parse_rfc3339_timestamp(iso_str: Optional[str], fallback: Optional[int] = None) -> int:
    """Convert Google Drive RFC 3339 timestamp (e.g. '2026-11-02T11:30:00.123Z') to UNIX epoch."""
    if not iso_str:
        return fallback if fallback is not None else int(time.time())
    try:
        clean = iso_str.strip()
        if clean.endswith("Z"):
            clean = clean[:-1] + "+00:00"
        return int(datetime.fromisoformat(clean).timestamp())
    except Exception:
        return fallback if fallback is not None else int(time.time())


class SyncWorker(threading.Thread):
    def __init__(
        self,
        db: MetadataDB,
        api: DriveAPI,
        account_id: str,
        poll_interval_sec: int = 30,
        cache_manager=None,
        config: Optional[Dict[str, Any]] = None,
        max_workers: int = 4,
    ):
        super().__init__(daemon=True, name=f"SyncWorker-{account_id[:8]}")
        self.db = db
        self.api = api
        self.account_id = account_id
        self.cache = cache_manager
        self.config = config or {}
        self.poll_interval = poll_interval_sec
        self.max_workers = max(1, min(int(max_workers), 12))
        self.ignore_engine = IgnoreRulesEngine()
        self.conflict_resolver = ConflictResolver(self.config)

        self.running = True
        self.paused = False
        self.is_syncing = False
        self.initial_scan_complete = False
        self.sync_phase = "Iniciando sincronização..."
        self.last_synced_file = ""
        self.session_synced_count = 0
        self.last_sync_timestamp = 0
        self.last_quota_timestamp = 0
        self.drive_total_bytes = 0
        self.resume_page_token: Optional[str] = None
        self.changes_page_token: Optional[str] = None
        self.recent_synced_items: List[Dict[str, Any]] = []
        self.current_activity = "Sincronizando catálogo..."
        self.active_transfers: Dict[str, Any] = {}

        # Restore persisted delta token if available
        tok_row = self.db.get_sync_token(self.account_id)
        if tok_row:
            self.changes_page_token = tok_row.get("changes_page_token")
            self.initial_scan_complete = bool(tok_row.get("initial_scan_complete"))

    def run(self):
        print(f"[SyncWorker] Iniciado para a conta: {self.account_id} (delta_token={'ativo' if self.changes_page_token else 'inicial'})")
        self.sync_remote_metadata()

        last_poll = time.time()
        while self.running:
            try:
                if not self.paused:
                    self._process_pending_operations()
                    self._hydrate_pinned_offline_items()

                    if self.resume_page_token or (time.time() - last_poll >= self.poll_interval):
                        self.sync_remote_metadata()
                        last_poll = time.time()
            except Exception as e:
                print(f"[SyncWorker] Erro no loop de sincronização: {e}")

            time.sleep(2 if self.resume_page_token else 5)

    def pause(self):
        self.paused = True
        self.current_activity = "Sincronização pausada"

    def resume(self):
        self.paused = False
        self.current_activity = "Retomando sincronização..."

    def stop(self):
        self.running = False

    # --- Bounded Concurrent Operation Queue ---
    def _execute_single_operation(self, op: Dict[str, Any]):
        op_id = op["id"]
        op_type = op["op_type"]
        item_id = op["item_id"]
        source = op["source_path"]
        target = op.get("target_path")

        self.db.update_operation_status(op_id, "running")
        self.current_activity = f"Processando: {op_type} ({target or item_id})"

        success = False
        error_msg = None
        try:
            if op_type == "upload_new":
                if source and os.path.exists(source):
                    db_item = self.db.get_item(item_id) if item_id else None
                    parent_id = target or (db_item or {}).get("parent_id") or "root"
                    name = (db_item or {}).get("name") or os.path.basename(source)
                    if self.ignore_engine.is_ignored(name, is_dir=False):
                        self.db.log_activity(self.account_id, "IGNORE", item_id or "", name, "Ignorado por regra .gitignore")
                        self.db.update_operation_status(op_id, "completed")
                        return
                    uploaded = self.api.upload_new_file(source, name, parent_id)
                    if uploaded and uploaded.get("id"):
                        real_id = uploaded["id"]
                        now = int(time.time())
                        self.db.upsert_item({
                            "id": real_id,
                            "account_id": self.account_id,
                            "name": uploaded.get("name", name),
                            "parent_id": parent_id,
                            "mime_type": uploaded.get("mimeType", "application/octet-stream"),
                            "size": int(uploaded.get("size", os.path.getsize(source))),
                            "md5_checksum": uploaded.get("md5Checksum") or compute_file_md5(source),
                            "is_directory": 0,
                            "modified_time": parse_rfc3339_timestamp(uploaded.get("modifiedTime"), now),
                            "created_time": parse_rfc3339_timestamp(uploaded.get("createdTime"), now),
                            "sync_status": "synced",
                        })
                        if item_id and item_id.startswith("local_"):
                            self.db.replace_synthetic_id(item_id, real_id)
                        self.db.log_activity(self.account_id, "UPLOAD", real_id, name, "Novo arquivo enviado")
                        success = True

            elif op_type == "upload_modify":
                if source and os.path.exists(source):
                    db_item = self.db.get_item(item_id) if item_id else None
                    remote_meta = self.api.get_file_metadata(item_id) if (item_id and not item_id.startswith("local_")) else None
                    action, conflict_type = self.conflict_resolver.resolve_upload_conflict(source, db_item or {}, remote_meta)

                    if action == "KEEP_CLOUD":
                        if self.cache and item_id:
                            self.cache.invalidate_item_cache(item_id)
                        self.db.log_activity(self.account_id, "CONFLICT", item_id or "", (db_item or {}).get("name", ""), f"{conflict_type} -> KEEP_CLOUD")
                        success = True
                    elif action == "KEEP_BOTH":
                        orig_name = (db_item or {}).get("name") or os.path.basename(source)
                        parent_id = (db_item or {}).get("parent_id") or "root"
                        conflict_name = make_conflict_copy_name(orig_name)
                        uploaded = self.api.upload_new_file(source, conflict_name, parent_id)
                        if uploaded and uploaded.get("id"):
                            self.db.record_conflict(
                                account_id=self.account_id,
                                item_id=item_id or "",
                                conflict_type=conflict_type or "LOCAL_EDIT_CLOUD_EDIT",
                                local_path=source,
                                cloud_name=conflict_name,
                                local_md5=compute_file_md5(source),
                                cloud_md5=(remote_meta or {}).get("md5Checksum", ""),
                                resolution="KEEP_BOTH",
                            )
                            self.db.log_activity(self.account_id, "CONFLICT", uploaded["id"], conflict_name, f"{conflict_type} -> cópia de conflito salva")
                            success = True
                    elif action == "DEFER_ASK":
                        self.db.record_conflict(
                            account_id=self.account_id,
                            item_id=item_id or "",
                            conflict_type=conflict_type or "LOCAL_EDIT_CLOUD_EDIT",
                            local_path=source,
                            cloud_name=(db_item or {}).get("name", ""),
                            local_md5=compute_file_md5(source),
                            cloud_md5=(remote_meta or {}).get("md5Checksum", ""),
                            resolution="pending",
                        )
                        error_msg = f"Conflito pendente ({conflict_type})"
                        success = False
                    else:
                        uploaded = self.api.update_file_content(item_id, source)
                        if uploaded:
                            new_md5 = uploaded.get("md5Checksum") or compute_file_md5(source)
                            with self.db._get_connection() as conn:
                                conn.execute(
                                    "UPDATE items SET sync_status = 'synced', md5_checksum = ?, size = ?, modified_time = ? WHERE id = ?",
                                    (new_md5, os.path.getsize(source), int(time.time()), item_id),
                                )
                                conn.commit()
                            self.db.log_activity(self.account_id, "EDIT", item_id or "", (db_item or {}).get("name", ""), "Conteúdo atualizado na nuvem")
                            success = True

            elif op_type == "delete":
                # Safe Trash on Google Drive (reversible!)
                success = self.api.delete_file(item_id, permanent=False)
                if success:
                    self.db.mark_item_trashed(item_id, True)
                    if self.cache:
                        self.cache.invalidate_item_cache(item_id)
                    self.db.log_activity(self.account_id, "DELETE", item_id or "", target or item_id or "", "Movido para a Lixeira do Google Drive")

            elif op_type == "rename":
                new_name = target
                if new_name:
                    success = self.api.rename_file(item_id, new_name)
                    if success:
                        with self.db._get_connection() as conn:
                            conn.execute("UPDATE items SET name = ? WHERE id = ?", (new_name, item_id))
                            conn.commit()
                        self.db.log_activity(self.account_id, "RENAME", item_id or "", new_name, f"Renomeado de {source or '?'}")

            elif op_type == "mkdir":
                dir_name = target
                db_dir = self.db.get_item(item_id) if item_id else None
                parent_id = (db_dir or {}).get("parent_id") or "root"
                if dir_name:
                    created = self.api.create_folder(dir_name, parent_id=parent_id)
                    if created and created.get("id"):
                        real_id = created["id"]
                        now = int(time.time())
                        self.db.upsert_item({
                            "id": real_id,
                            "account_id": self.account_id,
                            "name": created.get("name", dir_name),
                            "parent_id": parent_id,
                            "mime_type": "application/vnd.google-apps.folder",
                            "is_directory": 1,
                            "size": 0,
                            "modified_time": parse_rfc3339_timestamp(created.get("modifiedTime"), now),
                            "created_time": parse_rfc3339_timestamp(created.get("createdTime"), now),
                            "sync_status": "synced",
                        })
                        if item_id and item_id.startswith("local_"):
                            self.db.replace_synthetic_id(item_id, real_id)
                        self.db.log_activity(self.account_id, "MKDIR", real_id, dir_name, "Pasta criada no Google Drive")
                        success = True

        except Exception as e:
            error_msg = str(e)
            print(f"[SyncWorker] Erro ao executar operação {op_id} ({op_type}): {e}")

        if success:
            self.db.update_operation_status(op_id, "completed")
        else:
            self.db.update_operation_status(op_id, "failed", error_message=error_msg or "Falha na API")

    def _process_pending_operations(self):
        ops = self.db.get_pending_operations(limit=12, account_id=self.account_id)
        if not ops:
            if not self.paused and self.current_activity != "Ocioso - Tudo atualizado":
                self.current_activity = "Ocioso - Tudo atualizado"
            return

        # Execute up to max_workers operations concurrently (bounded CPU/RAM)
        workers = min(self.max_workers, len(ops))
        if workers == 1:
            for op in ops:
                if not self.running or self.paused:
                    break
                self._execute_single_operation(op)
            return

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(self._execute_single_operation, op) for op in ops if self.running and not self.paused]
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as e:
                    print(f"[SyncWorker] Erro em worker paralelo: {e}")

    # --- Automatic Offline Pin Hydration ---
    def _hydrate_pinned_offline_items(self):
        if not self.cache:
            return
        pinned = self.db.get_pinned_items_needing_hydration(self.account_id, limit=4)
        if not pinned:
            return
        export_mode = self.config.get("docs_export_mode", "OPEN_DOCUMENT")
        for item in pinned:
            if not self.running or self.paused:
                break
            item_id = item["id"]
            name = item.get("name", item_id)
            self.current_activity = f"Baixando offline: {name}"
            ok = self.cache.pin_file_offline(item_id, item.get("size", 0), export_mode=export_mode)
            if ok:
                self.db.log_activity(self.account_id, "PIN", item_id, name, "Disponível offline em cache local")

    # --- Quota & Metadata Ingestion ---
    def _update_account_quota(self):
        about = self.api.get_about()
        if not about:
            return
        quota = about.get("storageQuota", {})
        user = about.get("user", {})
        photo_link = user.get("photoLink", "")
        display_name = user.get("displayName", "")
        total = int(quota.get("limit", 0))
        used = int(quota.get("usage", 0))
        drive_used = int(quota.get("usageInDrive", 0)) or used
        if drive_used > 0:
            self.drive_total_bytes = drive_used
        if total > 0 or used > 0:
            with self.db._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE accounts
                    SET storage_total = ?,
                        storage_used = ?,
                        photo_url = CASE WHEN ? != '' THEN ? ELSE photo_url END,
                        display_name = CASE WHEN ? != '' THEN ? ELSE display_name END,
                        last_sync = ?
                    WHERE account_id = ?
                    """,
                    (total, used, photo_link, photo_link, display_name, display_name, int(time.time()), self.account_id),
                )
                conn.commit()

    def _ingest_files(self, files: List[Dict[str, Any]], now: int, force_parent_root: bool = False, mark_shared: bool = False):
        batch = []
        for f in files:
            parents = f.get("parents", [])
            parent_id = parents[0] if parents else ("__shared__" if mark_shared else "root")
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
            new_md5 = f.get("md5Checksum", "")

            if self.cache and new_md5 and not is_dir:
                self.cache.invalidate_if_changed(f["id"], new_md5)

            batch.append({
                "id": f["id"],
                "account_id": self.account_id,
                "name": f["name"],
                "parent_id": parent_id,
                "mime_type": f.get("mimeType", ""),
                "is_directory": is_dir,
                "size": size,
                "md5_checksum": new_md5,
                "modified_time": parse_rfc3339_timestamp(f.get("modifiedTime"), now),
                "created_time": parse_rfc3339_timestamp(f.get("createdTime"), now),
                "is_pinned": 0,
                "is_trashed": 1 if f.get("trashed") else 0,
                "sync_status": "online_only",
                "local_cache_path": None,
                "is_shared": 1 if (mark_shared or f.get("shared")) else 0,
                "drive_id": f.get("driveId", ""),
                "is_starred": 1 if f.get("starred") else 0,
            })
        if batch:
            self.db.upsert_items_batch(batch)
            self.session_synced_count += len(batch)
            self.last_synced_file = batch[-1]["name"]
            for item in batch[-15:]:
                self.recent_synced_items.insert(0, item)
            if len(self.recent_synced_items) > 60:
                self.recent_synced_items = self.recent_synced_items[:60]

    # --- Incremental Delta Sync (changes.list) ---
    def sync_incremental_changes(self) -> bool:
        """Apply only remote deltas since self.changes_page_token using Drive API v3 changes.list."""
        if not self.changes_page_token:
            return False

        now = int(time.time())
        token = self.changes_page_token
        total_changes = 0

        for _ in range(10):
            if not self.running or self.paused:
                break
            resp = self.api.list_changes(page_token=token, page_size=500)
            changes = resp.get("changes", [])
            updated_files = []
            for ch in changes:
                fid = ch.get("fileId")
                fobj = ch.get("file")
                if ch.get("removed") or (fobj and fobj.get("trashed")):
                    if fid:
                        self.db.mark_item_trashed(fid, True)
                        if self.cache:
                            self.cache.invalidate_item_cache(fid)
                        total_changes += 1
                elif fobj:
                    updated_files.append(fobj)
                    total_changes += 1

            if updated_files:
                self._ingest_files(updated_files, now, force_parent_root=False)

            next_tok = resp.get("nextPageToken")
            new_start_tok = resp.get("newStartPageToken")
            if next_tok:
                token = next_tok
                time.sleep(0.5)
            elif new_start_tok:
                self.changes_page_token = new_start_tok
                self.db.save_sync_token(self.account_id, new_start_tok, initial_scan_complete=True)
                break
            else:
                break

        if total_changes > 0:
            self.db.log_activity(self.account_id, "DOWNLOAD", "", f"{total_changes} alterações", "Delta incremental aplicado via changes.list")
        return True

    def sync_remote_metadata(self):
        """
        Sync remote catalog:
          - If initial scan is complete and changes_page_token exists -> fast incremental changes.list!
          - Otherwise -> acquire startPageToken, run bounded initial scan, then switch to delta mode.
        """
        self.is_syncing = True
        self.sync_phase = "Consultando cota e alterações na nuvem..."
        self.current_activity = self.sync_phase
        now = int(time.time())

        try:
            if not self.resume_page_token and (now - self.last_quota_timestamp > 300):
                self._update_account_quota()
                self.last_quota_timestamp = now

            # Fast path: true incremental delta sync!
            if self.initial_scan_complete and self.changes_page_token and not self.resume_page_token:
                self.sync_phase = "Verificando alterações incrementais..."
                self.current_activity = self.sync_phase
                self.sync_incremental_changes()
                self.last_sync_timestamp = int(time.time())
                return

            # Obtain startPageToken BEFORE initial scan so no changes during scan are missed
            if not self.changes_page_token:
                start_tok = self.api.get_start_page_token()
                if start_tok:
                    self.changes_page_token = start_tok
                    self.db.save_sync_token(self.account_id, start_tok, initial_scan_complete=False)

            # 1. Priority sync: Root directory ('Meu Drive')
            if not self.resume_page_token:
                self.sync_phase = "Sincronizando pasta raiz (Meu Drive)..."
                self.current_activity = self.sync_phase
                root_token = None
                while self.running and not self.paused:
                    resp = self.api.list_files(q="'root' in parents and trashed = false", page_size=500, page_token=root_token)
                    files = resp.get("files", [])
                    self._ingest_files(files, now, force_parent_root=True)
                    root_token = resp.get("nextPageToken")
                    if not root_token:
                        break
                    time.sleep(1.0)

                # Index Shared with Me items (first page)
                shared_resp = self.api.list_shared_with_me(page_size=200)
                if shared_resp.get("files"):
                    self._ingest_files(shared_resp["files"], now, force_parent_root=False, mark_shared=True)

            # 2. Map folders across Drive on initial scan
            if not self.initial_scan_complete and not self.resume_page_token:
                self.sync_phase = "Mapeando estrutura de pastas do Google Drive..."
                self.current_activity = self.sync_phase
                folder_token = None
                folder_pages = 0
                while self.running and not self.paused and folder_pages < 10:
                    resp = self.api.list_files(
                        q="mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                        page_size=1000,
                        page_token=folder_token,
                    )
                    files = resp.get("files", [])
                    self._ingest_files(files, now, force_parent_root=False)
                    folder_pages += 1
                    folder_token = resp.get("nextPageToken")
                    if not folder_token:
                        break
                    time.sleep(1.0)

            # 3. Background file tree sync continuing from resume_page_token
            page_token = self.resume_page_token
            pages_fetched = 0
            while self.running and not self.paused and pages_fetched < 6:
                stats = self.db.get_sync_stats(account_id=self.account_id)
                self.sync_phase = f"Sincronizando catálogo ({stats['total_items']} itens indexados)..."
                self.current_activity = self.sync_phase
                resp = self.api.list_files(q="trashed = false", page_size=500, page_token=page_token)
                files = resp.get("files", [])
                self._ingest_files(files, now, force_parent_root=False)
                pages_fetched += 1
                page_token = resp.get("nextPageToken")
                self.resume_page_token = page_token
                if not page_token:
                    self.initial_scan_complete = True
                    self.resume_page_token = None
                    self.db.save_sync_token(self.account_id, self.changes_page_token, initial_scan_complete=True)
                    break
                time.sleep(1.5)

            self.last_sync_timestamp = int(time.time())
        finally:
            if self.initial_scan_complete or not self.resume_page_token:
                self.is_syncing = False
                self.sync_phase = "Sincronizado e atualizado"
                self.current_activity = "Ocioso - Tudo atualizado"
            else:
                self.is_syncing = True
                stats = self.db.get_sync_stats(account_id=self.account_id)
                self.sync_phase = f"Sincronizando em segundo plano ({stats['total_items']} itens)..."
                self.current_activity = self.sync_phase
