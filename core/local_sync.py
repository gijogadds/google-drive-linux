"""
Google Drive for Linux - Hybrid Local Selective Sync Engine (LSS / CSS)
Synchronizes any real local directory with a Google Drive folder in:
  - TWO_WAY: Bidirectional sync with MD5 File Matching & 4-case Conflict Resolution
  - UPLOAD_ONLY: 1-way local-to-cloud backup (never deletes local files)
  - DOWNLOAD_ONLY: 1-way cloud-to-local offline mirror
Includes external-drive disconnect protection, empty-root guard, .gitignore rules,
Google Docs offline export (.odt/.docx), and OS Trash safety for remote deletions.
"""
import os
import time
import threading
from typing import Dict, Any, Optional, Set

from core.db import MetadataDB
from core.api import DriveAPI
from core.ignore import IgnoreRulesEngine
from core.conflicts import (
    ConflictResolver,
    compute_file_md5,
    make_conflict_copy_name,
    move_to_os_trash,
)
from core.docs_converter import is_google_workspace_doc, export_document_offline


class LocalSyncWorker(threading.Thread):
    """Background worker that synchronizes all enabled local_sync_rules for an account."""

    def __init__(
        self,
        db: MetadataDB,
        api: DriveAPI,
        account_id: str,
        config: Optional[Dict[str, Any]] = None,
        scan_interval_sec: int = 20,
    ):
        super().__init__(daemon=True, name=f"LocalSync-{account_id[:8]}")
        self.db = db
        self.api = api
        self.account_id = account_id
        self.config = config or {}
        self.scan_interval = scan_interval_sec
        self.ignore_engine = IgnoreRulesEngine()
        self.conflict_resolver = ConflictResolver(self.config)
        self.running = True
        self.paused = False
        # Track known local files per rule_id to detect deletions safely: {rule_id: {rel_path: item_id}}
        self._known_files: Dict[int, Dict[str, str]] = {}

    def stop(self):
        self.running = False

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def run(self):
        while self.running:
            try:
                if not self.paused:
                    self.sync_all_rules()
            except Exception as e:
                print(f"[LocalSync] Erro no ciclo de sincronização local ({self.account_id}): {e}")
            time.sleep(self.scan_interval)

    def sync_all_rules(self):
        rules = self.db.get_local_sync_rules(account_id=self.account_id, enabled_only=True)
        for rule in rules:
            if not self.running or self.paused:
                break
            self.sync_rule(rule)

    def _is_root_safe(self, rule: Dict[str, Any]) -> bool:
        """
        Guard against external HD disconnection or accidentally empty root folder.
        Prevents catastrophic mass deletions in the cloud.
        """
        local_root = rule["local_path"]
        if not os.path.isdir(local_root):
            print(f"[LocalSync] Pasta local ausente/desconectada (proteção ativa): {local_root}")
            return False

        last_count = rule.get("last_item_count") or 0
        try:
            entries = [e for e in os.listdir(local_root) if not self.ignore_engine.is_ignored(e)]
        except OSError:
            return False

        if last_count > 0 and len(entries) == 0:
            if self.conflict_resolver.missing_file_behavior != "DELETE":
                print(
                    f"[LocalSync] Proteção de pasta vazia ativada para '{local_root}' "
                    f"(tinha {last_count} itens). Deleção na nuvem bloqueada."
                )
                return False
        return True

    def sync_rule(self, rule: Dict[str, Any]) -> Dict[str, int]:
        """Execute one full safe synchronization pass for a single local_sync_rule."""
        stats = {"uploaded": 0, "downloaded": 0, "matched": 0, "conflicts": 0, "trashed": 0, "skipped_ignored": 0}
        if not self._is_root_safe(rule):
            return stats

        rule_id = rule["id"]
        local_root = rule["local_path"]
        cloud_folder_id = rule.get("cloud_folder_id") or "root"
        mode = (rule.get("sync_mode") or "TWO_WAY").upper()
        export_mode = (rule.get("export_mode") or self.config.get("docs_export_mode") or "OPEN_DOCUMENT").upper()

        # 1. Index remote children of cloud_folder_id
        remote_children = self.db.get_children(cloud_folder_id, account_id=self.account_id)
        remote_by_name = {c["name"]: c for c in remote_children if not c.get("is_directory")}

        # 2. Scan local directory (top-level + recursive folders)
        local_files: Dict[str, str] = {}
        for dirpath, dirnames, filenames in os.walk(local_root):
            rel_dir = os.path.relpath(dirpath, local_root)
            rel_dir = "" if rel_dir == "." else rel_dir.replace("\\", "/")

            # Prune ignored directories in-place
            kept_dirs = []
            for d in dirnames:
                d_rel = f"{rel_dir}/{d}" if rel_dir else d
                if self.ignore_engine.is_ignored(d_rel, is_dir=True):
                    stats["skipped_ignored"] += 1
                else:
                    kept_dirs.append(d)
            dirnames[:] = kept_dirs

            for fname in filenames:
                f_rel = f"{rel_dir}/{fname}" if rel_dir else fname
                if self.ignore_engine.is_ignored(f_rel, is_dir=False):
                    stats["skipped_ignored"] += 1
                    continue
                local_files[f_rel] = os.path.join(dirpath, fname)

        # 3. Local -> Cloud pass (TWO_WAY and UPLOAD_ONLY)
        if mode in ("TWO_WAY", "UPLOAD_ONLY"):
            for rel_path, full_local in local_files.items():
                if "/" in rel_path:
                    # Subfolder files: ensure parent cloud folder exists
                    parent_cloud_id = self._ensure_cloud_subpath(cloud_folder_id, os.path.dirname(rel_path))
                    fname = os.path.basename(rel_path)
                    siblings = {c["name"]: c for c in self.db.get_children(parent_cloud_id, account_id=self.account_id) if not c.get("is_directory")}
                    remote_item = siblings.get(fname)
                else:
                    parent_cloud_id = cloud_folder_id
                    fname = rel_path
                    remote_item = remote_by_name.get(fname)

                local_md5 = compute_file_md5(full_local)
                if not local_md5:
                    continue

                if remote_item:
                    remote_md5 = remote_item.get("md5_checksum") or ""
                    # File Matching: identical MD5 -> already in sync, zero upload!
                    if remote_md5 and remote_md5 == local_md5:
                        stats["matched"] += 1
                        continue

                    # Divergent content -> check mtime & conflict policy
                    local_mtime = int(os.path.getmtime(full_local))
                    remote_mtime = int(remote_item.get("modified_time") or 0)

                    if mode == "UPLOAD_ONLY" or local_mtime > remote_mtime + 2:
                        action, ctype = self.conflict_resolver.resolve_upload_conflict(
                            full_local, remote_item, {"md5Checksum": remote_md5, "trashed": False}
                        )
                        if action == "KEEP_BOTH":
                            cname = make_conflict_copy_name(fname)
                            uploaded = self.api.upload_new_file(full_local, cname, parent_cloud_id)
                            if uploaded and uploaded.get("id"):
                                stats["conflicts"] += 1
                                self.db.log_activity(self.account_id, "CONFLICT", uploaded["id"], cname, f"LocalSync {ctype} -> KEEP_BOTH")
                        elif action == "PROCEED_UPLOAD":
                            updated = self.api.update_file_content(remote_item["id"], full_local)
                            if updated:
                                stats["uploaded"] += 1
                                self.db.log_activity(self.account_id, "EDIT", remote_item["id"], fname, "LocalSync atualizado")
                        elif action == "DEFER_ASK":
                            stats["conflicts"] += 1
                            self.db.record_conflict(
                                self.account_id, remote_item["id"], ctype or "LOCAL_EDIT_CLOUD_EDIT",
                                local_path=full_local, cloud_name=fname, local_md5=local_md5, cloud_md5=remote_md5,
                            )
                else:
                    # New local file -> upload to cloud
                    uploaded = self.api.upload_new_file(full_local, fname, parent_cloud_id)
                    if uploaded and uploaded.get("id"):
                        stats["uploaded"] += 1
                        self.db.upsert_item({
                            "id": uploaded["id"],
                            "account_id": self.account_id,
                            "name": uploaded.get("name", fname),
                            "parent_id": parent_cloud_id,
                            "mime_type": uploaded.get("mimeType", "application/octet-stream"),
                            "size": os.path.getsize(full_local),
                            "md5_checksum": uploaded.get("md5Checksum") or local_md5,
                            "is_directory": 0,
                            "modified_time": int(os.path.getmtime(full_local)),
                            "created_time": int(time.time()),
                            "sync_status": "synced",
                        })
                        self.db.log_activity(self.account_id, "UPLOAD", uploaded["id"], fname, f"LocalSync ({mode})")

        # 4. Cloud -> Local pass (TWO_WAY and DOWNLOAD_ONLY)
        if mode in ("TWO_WAY", "DOWNLOAD_ONLY"):
            for fname, ritem in remote_by_name.items():
                if self.ignore_engine.is_ignored(fname, is_dir=False):
                    stats["skipped_ignored"] += 1
                    continue
                if is_google_workspace_doc(ritem.get("mime_type", "")):
                    if export_mode != "LINK":
                        exported = export_document_offline(self.api, ritem, local_root, mode=export_mode)
                        if exported:
                            stats["downloaded"] += 1
                    continue

                dest_path = os.path.join(local_root, fname.replace("/", "／"))
                remote_md5 = ritem.get("md5_checksum") or ""
                if os.path.exists(dest_path):
                    if remote_md5 and compute_file_md5(dest_path) == remote_md5:
                        stats["matched"] += 1
                        continue
                    # Only overwrite locally if cloud is newer (or DOWNLOAD_ONLY)
                    local_mtime = int(os.path.getmtime(dest_path))
                    remote_mtime = int(ritem.get("modified_time") or 0)
                    if mode == "TWO_WAY" and local_mtime >= remote_mtime:
                        continue

                if self.api.download_full_file(ritem["id"], dest_path):
                    stats["downloaded"] += 1
                    self.db.log_activity(self.account_id, "DOWNLOAD", ritem["id"], fname, f"LocalSync ({mode})")

            # Handle remote deletions safely: move disappeared remote files to OS Trash
            prev_known = self._known_files.get(rule_id, {})
            for old_rel, old_id in list(prev_known.items()):
                if old_rel in local_files:
                    item_now = self.db.get_item(old_id)
                    if item_now and item_now.get("is_trashed"):
                        if move_to_os_trash(local_files[old_rel]):
                            stats["trashed"] += 1
                            self.db.log_activity(self.account_id, "DELETE", old_id, old_rel, "Movido para Lixeira local (apagado na nuvem)")

        # Record current state for next pass
        current_map = {}
        for fname, ritem in remote_by_name.items():
            if fname in local_files:
                current_map[fname] = ritem["id"]
        self._known_files[rule_id] = current_map
        self.db.update_local_sync_rule_count(rule_id, len(local_files))
        return stats

    def _ensure_cloud_subpath(self, root_cloud_id: str, rel_dir: str) -> str:
        """Ensure nested subfolders exist in Google Drive, returning the leaf folder ID."""
        curr_parent = root_cloud_id
        for part in [p for p in rel_dir.replace("\\", "/").split("/") if p and p != "."]:
            existing = self.db.get_item_by_path(curr_parent, part, account_id=self.account_id)
            if existing and existing.get("is_directory"):
                curr_parent = existing["id"]
            else:
                created = self.api.create_folder(part, parent_id=curr_parent)
                if created and created.get("id"):
                    parent_for_new = curr_parent
                    curr_parent = created["id"]
                    self.db.upsert_item({
                        "id": curr_parent,
                        "account_id": self.account_id,
                        "name": part,
                        "parent_id": parent_for_new,
                        "mime_type": "application/vnd.google-apps.folder",
                        "is_directory": 1,
                        "size": 0,
                        "modified_time": int(time.time()),
                        "created_time": int(time.time()),
                        "sync_status": "synced",
                    })
                else:
                    break
        return curr_parent
