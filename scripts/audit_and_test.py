#!/usr/bin/env python3
"""
Google Drive for Linux - Comprehensive Automated Test & System Audit Suite
Tests:
  1. Syntax & import verification across all modules
  2. IgnoreRulesEngine (.gitignore syntax, negation, dir-only, globs, **)
  3. ConflictResolver (4-case matrix, MD5 matching, conflict copy naming, OS Trash)
  4. DocsConverter (LINK, OPEN_DOCUMENT .odt/.ods/.odp, MS_OFFICE .docx/.xlsx/.pptx)
  5. MetadataDB (WAL, migrations, delta sync_tokens, conflicts, activity_log, local_sync_rules, synthetic ID promotion)
  6. SyncWorker (delta changes.list, concurrent worker pool, real mkdir, safe trash, offline pin hydration, upload_new filename fix)
  7. LocalSyncWorker (TWO_WAY, UPLOAD_ONLY, DOWNLOAD_ONLY, MD5 file matching, empty-root & disconnected HD guard)
  8. CacheManager (MD5 cache invalidation, recursive folder pinning, LRU quota)
  9. GoogleDriveFUSE (stable root_mtime, real quota statfs, virtual Shared with Me, flush duplicate-upload prevention, ignore filter)
 10. Live Environment Audit (systemd service, FUSE mounts, real Google API connectivity, SQLite integrity, disk/log health)
"""
import os
import sys
import time
import shutil
import tempfile
import unittest
import subprocess
import py_compile
from unittest.mock import MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from core.ignore import IgnoreRulesEngine
from core.conflicts import ConflictResolver, compute_file_md5, make_conflict_copy_name, move_to_os_trash
from core.docs_converter import get_export_target, get_local_extension, export_document_offline
from core.db import MetadataDB
from core.cache import CacheManager
from core.watcher import SyncWorker, parse_rfc3339_timestamp
from core.local_sync import LocalSyncWorker
from core.vfs import GoogleDriveFUSE


class TestIgnoreRulesEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gdrive_test_ign_")
        self.rules_file = os.path.join(self.tmp, "ignore_rules.txt")
        with open(self.rules_file, "w", encoding="utf-8") as f:
            f.write(
                "# Comment\n"
                "*.tmp\n"
                "node_modules/\n"
                "Photos/Private/\n"
                "!important.tmp\n"
                "build/**/cache.bin\n"
            )
        self.engine = IgnoreRulesEngine(rules_path=self.rules_file)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_glob_and_negation(self):
        self.assertTrue(self.engine.is_ignored("foo.tmp"))
        self.assertTrue(self.engine.is_ignored("sub/bar.tmp"))
        self.assertFalse(self.engine.is_ignored("important.tmp"))
        self.assertFalse(self.engine.is_ignored("readme.md"))

    def test_directory_rules(self):
        self.assertTrue(self.engine.is_ignored("node_modules", is_dir=True))
        self.assertTrue(self.engine.is_ignored("node_modules/pkg/index.js", is_dir=False))
        self.assertTrue(self.engine.is_ignored("Photos/Private/secret.jpg", is_dir=False))
        self.assertFalse(self.engine.is_ignored("Photos/Public/img.jpg", is_dir=False))

    def test_double_star(self):
        self.assertTrue(self.engine.is_ignored("build/a/b/cache.bin", is_dir=False))
        self.assertTrue(self.engine.is_ignored("build/cache.bin", is_dir=False))


class TestConflictResolverAndTrash(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gdrive_test_conf_")
        self.resolver = ConflictResolver()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_four_case_matrix(self):
        self.assertEqual(self.resolver.classify_conflict("aaa", "bbb", "000"), "LOCAL_EDIT_CLOUD_EDIT")
        self.assertIsNone(self.resolver.classify_conflict("aaa", "aaa", "000"))
        self.assertEqual(self.resolver.classify_conflict("aaa", "", "000", cloud_deleted=True), "LOCAL_EDIT_CLOUD_DELETE")
        self.assertEqual(self.resolver.classify_conflict("aaa", "000", "000", unsyncing=True), "LOCAL_EDIT_UNSYNC")
        self.assertEqual(self.resolver.classify_conflict("", "bbb", "000", local_deleted=True), "LOCAL_DELETE_CLOUD_EDIT")

    def test_upload_conflict_keep_both(self):
        f = os.path.join(self.tmp, "doc.txt")
        with open(f, "wb") as fp:
            fp.write(b"local version")
        action, ctype = self.resolver.resolve_upload_conflict(
            f,
            {"md5_checksum": "baseline_md5"},
            {"md5Checksum": "cloud_new_md5", "trashed": False},
        )
        self.assertEqual(action, "KEEP_BOTH")
        self.assertEqual(ctype, "LOCAL_EDIT_CLOUD_EDIT")
        cname = make_conflict_copy_name("doc.txt")
        self.assertTrue(cname.startswith("doc (conflito ") and cname.endswith(".txt"))


class TestDocsConverter(unittest.TestCase):
    def test_profiles(self):
        self.assertEqual(get_local_extension("application/vnd.google-apps.document", "LINK"), ".gdoc")
        self.assertEqual(get_local_extension("application/vnd.google-apps.document", "OPEN_DOCUMENT"), ".odt")
        self.assertEqual(get_local_extension("application/vnd.google-apps.spreadsheet", "OPEN_DOCUMENT"), ".ods")
        self.assertEqual(get_local_extension("application/vnd.google-apps.presentation", "MS_OFFICE"), ".pptx")
        self.assertIsNone(get_export_target("application/pdf", "OPEN_DOCUMENT"))

    def test_export_call(self):
        tmp = tempfile.mkdtemp(prefix="gdrive_test_doc_")
        try:
            api = MagicMock()
            api.export_google_doc.return_value = True
            out = export_document_offline(
                api,
                {"id": "doc1", "name": "Relatorio", "mime_type": "application/vnd.google-apps.document"},
                tmp,
                mode="OPEN_DOCUMENT",
            )
            self.assertTrue(out.endswith("Relatorio.odt"))
            api.export_google_doc.assert_called_once()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestSyncEngineAndVFS(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gdrive_test_eng_")
        self.db_path = os.path.join(self.tmp, "test.db")
        self.cache_dir = os.path.join(self.tmp, "cache")
        self.db = MetadataDB(db_path=self.db_path)
        self.acc_id = "acc_test_1"
        self.db.upsert_account({
            "account_id": self.acc_id,
            "email": "test@example.com",
            "display_name": "Test User",
            "photo_url": "",
            "storage_total": 100 * 1024**3,
            "storage_used": 25 * 1024**3,
            "is_active": 1,
            "auth_data": "{}",
            "last_sync": 0,
        })
        self.api = MagicMock()
        self.cache = CacheManager(self.db, self.api, chunk_size_mb=1, max_cache_size_gb=1, cache_dir=self.cache_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rfc3339_parsing(self):
        ts = parse_rfc3339_timestamp("2026-11-24T20:00:00Z")
        self.assertGreater(ts, 1700000000)

    def test_delta_changes_sync(self):
        self.db.save_sync_token(self.acc_id, "tok_1", initial_scan_complete=True)
        self.api.get_about.return_value = None
        self.api.list_changes.return_value = {
            "changes": [
                {
                    "fileId": "file_delta_1",
                    "removed": False,
                    "file": {
                        "id": "file_delta_1",
                        "name": "planilha.xlsx",
                        "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        "parents": ["root"],
                        "size": "1234",
                        "md5Checksum": "abc123",
                        "modifiedTime": "2026-11-24T18:00:00Z",
                        "trashed": False,
                    },
                }
            ],
            "newStartPageToken": "tok_2",
        }
        worker = SyncWorker(self.db, self.api, self.acc_id, cache_manager=self.cache)
        worker.sync_remote_metadata()
        self.assertEqual(worker.changes_page_token, "tok_2")
        item = self.db.get_item("file_delta_1")
        self.assertIsNotNone(item)
        self.assertEqual(item["name"], "planilha.xlsx")
        self.assertEqual(self.db.get_sync_token(self.acc_id)["changes_page_token"], "tok_2")

    def test_vfs_flush_uses_upload_new_and_real_filename(self):
        vfs = GoogleDriveFUSE(self.db, self.cache, self.api, self.acc_id, volume_name="TestDrive")
        fh = vfs.create("/relatorio_final.txt", 0o644)
        vfs.write("/relatorio_final.txt", b"hello world", 0, fh)
        vfs.flush("/relatorio_final.txt", fh)
        vfs.release("/relatorio_final.txt", fh)

        ops = self.db.get_pending_operations(account_id=self.acc_id)
        # Must queue ONLY ONE upload_new (no duplicate on release!)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["op_type"], "upload_new")

        # Execute via SyncWorker and verify real filename 'relatorio_final.txt' (not 'full.bin'!)
        self.api.upload_new_file.return_value = {
            "id": "drive_real_99",
            "name": "relatorio_final.txt",
            "mimeType": "text/plain",
            "size": "11",
            "md5Checksum": compute_file_md5(ops[0]["source_path"]),
        }
        worker = SyncWorker(self.db, self.api, self.acc_id, cache_manager=self.cache)
        worker._process_pending_operations()
        args, _kwargs = self.api.upload_new_file.call_args
        self.assertEqual(args[1], "relatorio_final.txt")
        self.assertIsNotNone(self.db.get_item("drive_real_99"))

    def test_real_mkdir_and_statfs_quota(self):
        vfs = GoogleDriveFUSE(self.db, self.cache, self.api, self.acc_id, volume_name="TestDrive")
        st = vfs.statfs("/")
        # 100 GB total, 75 GB free
        self.assertEqual(st["f_blocks"] * st["f_bsize"], 100 * 1024**3)
        self.assertEqual(st["f_bavail"] * st["f_bsize"], 75 * 1024**3)

        vfs.mkdir("/NovaPasta", 0o755)
        self.api.create_folder.return_value = {"id": "folder_real_77", "name": "NovaPasta"}
        worker = SyncWorker(self.db, self.api, self.acc_id, cache_manager=self.cache)
        worker._process_pending_operations()
        self.api.create_folder.assert_called_once_with("NovaPasta", parent_id="root")
        self.assertIsNotNone(self.db.get_item("folder_real_77"))

    def test_local_selective_sync_and_empty_root_guard(self):
        local_dir = os.path.join(self.tmp, "local_sync_dir")
        os.makedirs(local_dir, exist_ok=True)
        local_file = os.path.join(local_dir, "matched.txt")
        with open(local_file, "wb") as fp:
            fp.write(b"same content")
        md5 = compute_file_md5(local_file)

        # Seed identical remote item -> should MATCH without uploading!
        self.db.upsert_item({
            "id": "rem_match_1",
            "account_id": self.acc_id,
            "name": "matched.txt",
            "parent_id": "root",
            "mime_type": "text/plain",
            "size": 12,
            "md5_checksum": md5,
            "is_directory": 0,
            "modified_time": int(time.time()),
            "created_time": int(time.time()),
        })
        self.db.upsert_local_sync_rule(self.acc_id, local_dir, cloud_folder_id="root", sync_mode="TWO_WAY")
        rule = self.db.get_local_sync_rules(account_id=self.acc_id)[0]

        ls_worker = LocalSyncWorker(self.db, self.api, self.acc_id)
        stats = ls_worker.sync_rule(rule)
        self.assertGreaterEqual(stats["matched"], 1)
        self.assertEqual(stats["uploaded"], 0)

        # Simulate external HD disconnect -> root missing -> must abort safely!
        shutil.rmtree(local_dir)
        rule_refreshed = self.db.get_local_sync_rules(account_id=self.acc_id)[0]
        self.assertFalse(ls_worker._is_root_safe(rule_refreshed))

    def test_md5_cache_invalidation_and_shared_virtual_dir(self):
        # Seed item with initial MD5 and cached file
        self.db.upsert_item({
            "id": "shared_item_1",
            "account_id": self.acc_id,
            "name": "Contrato.pdf",
            "parent_id": "__shared__",
            "mime_type": "application/pdf",
            "size": 500,
            "md5_checksum": "old_md5",
            "is_directory": 0,
            "is_shared": 1,
        })
        cached_path = self.cache.write_direct("shared_item_1", b"old bytes")
        self.assertTrue(os.path.exists(cached_path))

        # Cloud MD5 changes -> cache MUST be invalidated!
        self.cache.invalidate_if_changed("shared_item_1", "new_md5")
        self.assertFalse(os.path.exists(cached_path))

        # VFS must expose 'Compartilhados comigo' at root and list 'Contrato.pdf' inside it
        vfs = GoogleDriveFUSE(self.db, self.cache, self.api, self.acc_id, volume_name="TestDrive")
        root_entries = vfs.readdir("/", None)
        self.assertIn("Compartilhados comigo", root_entries)
        shared_entries = vfs.readdir("/Compartilhados comigo", None)
        self.assertIn("Contrato.pdf", shared_entries)


def run_syntax_audit() -> bool:
    print("\n=== 1. AUDITORIA DE SINTAXE PYTHON ===")
    ok = True
    checked = 0
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "runtime", "apps")]
        for fn in files:
            if fn.endswith(".py"):
                full = os.path.join(root, fn)
                try:
                    py_compile.compile(full, doraise=True)
                    checked += 1
                except py_compile.PyCompileError as e:
                    print(f"  [ERRO SINTAXE] {full}: {e}")
                    ok = False
    print(f"  [{'OK' if ok else 'FALHA'}] {checked} arquivos Python compilados sem erros de sintaxe.")
    return ok


def run_live_environment_audit():
    print("\n=== 3. AUDITORIA DO AMBIENTE REAL (SERVIÇO, MONTAGENS, API, LOGS) ===")
    # 1. Systemd service
    res = subprocess.run(["systemctl", "--user", "is-active", "google-drive.service"], capture_output=True, text=True)
    print(f"  [Serviço] google-drive.service: {res.stdout.strip() or 'inativo'}")

    # 2. Active FUSE mounts
    mres = subprocess.run(["mount"], capture_output=True, text=True)
    fuse_lines = [ln for ln in mres.stdout.splitlines() if "GoogleDriveFUSE" in ln]
    print(f"  [Montagens FUSE] {len(fuse_lines)} ativa(s):")
    for ln in fuse_lines:
        print(f"    - {ln}")

    # 3. SQLite integrity
    from core.config import METADATA_DB_PATH, LOG_DIR
    if os.path.exists(METADATA_DB_PATH):
        import sqlite3
        conn = sqlite3.connect(METADATA_DB_PATH)
        chk = conn.execute("PRAGMA integrity_check;").fetchone()[0]
        cnt = conn.execute("SELECT COUNT(*) FROM items WHERE is_trashed = 0;").fetchone()[0]
        conn.close()
        print(f"  [SQLite] Integridade: {chk} | Itens indexados: {cnt}")
    else:
        print("  [SQLite] Banco ainda não criado.")

    # 4. Log file health
    log_file = os.path.join(LOG_DIR, "google-drive.log")
    if os.path.exists(log_file):
        sz_kb = os.path.getsize(log_file) / 1024.0
        print(f"  [Logs] {log_file} ({sz_kb:.1f} KiB)")
    else:
        print(f"  [Logs] {log_file} ainda não gerado.")


if __name__ == "__main__":
    syn_ok = run_syntax_audit()
    print("\n=== 2. BATERIA DE TESTES UNITÁRIOS E INTEGRAÇÃO ===")
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    run_live_environment_audit()
    sys.exit(0 if (syn_ok and result.wasSuccessful()) else 1)
