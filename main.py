#!/usr/bin/env python3
"""
Google Drive for Linux - Main Daemon & Service Entrypoint
Coordinates VFS mount, background sync worker, Omaha auto-updater, and tray indicator.
"""
import sys
import os
import signal
import threading
import time

# Ensure project root is in python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import load_config, save_config, ensure_directories, DEFAULT_MOUNT_POINT
from core.db import MetadataDB
from core.auth import AuthManager
from core.api import DriveAPI
from core.cache import CacheManager
from core.vfs import mount_fuse
from core.watcher import SyncWorker
from updater.omaha_updater import OmahaUpdater
from updater.github_updater import GitHubUpdater
from ui.indicator import DriveTrayIndicator
from ui.webview_window import SettingsDialog
from core.diagnostic import install_global_crash_hook


class GoogleDriveDaemon:
    def __init__(self):
        install_global_crash_hook()
        ensure_directories()
        self.config = load_config()
        self.db = MetadataDB()
        self.auth = AuthManager()
        self.sync_worker = None
        self.updater = None
        self.fuse_thread = None
        self.running = True

    def start(self):
        print("==================================================")
        print("    Iniciando Google Drive para Linux (Ubuntu)    ")
        print("==================================================")

        accounts = self.db.get_accounts()
        if not accounts:
            print("[Aviso] Nenhuma conta configurada.")
            print("[Info] Abra o aplicativo ou rode 'gdrive-cli login' para conectar.")
            # If no account, we will prompt in settings or wait for tray interaction

        account_id = accounts[0]["account_id"] if accounts else None

        # 1. Start Auto-Updaters (Dual Channel: Google Omaha & GitHub Releases/Hotfixes)
        if self.config.get("auto_update", True):
            interval = self.config.get("update_check_interval_hours", 12)
            self.updater = OmahaUpdater(
                current_version="132.0.0.0",
                check_interval_hours=interval,
                on_update_found=self._on_update_found
            )
            self.updater.start()

            self.github_updater = GitHubUpdater(check_interval_hours=4)
            self.github_updater.start()

        # 2. Start Sync Worker & VFS if account is connected
        if account_id:
            api = DriveAPI(self.auth, account_id)
            cache = CacheManager(
                db=self.db,
                api=api,
                chunk_size_mb=self.config.get("chunk_size_mb", 16),
                max_cache_size_gb=self.config.get("max_cache_size_gb", 10),
                cache_dir=self.config.get("cache_dir")
            )

            # Start Sync Worker
            self.sync_worker = SyncWorker(db=self.db, api=api, account_id=account_id)
            self.sync_worker.start()

            # Start FUSE VFS Mount in a background thread
            mount_pt = self.config.get("mount_point", DEFAULT_MOUNT_POINT)
            try:
                os.makedirs(mount_pt, exist_ok=True)
            except Exception as e:
                print(f"[Aviso] Não foi possível criar o diretório de montagem ({mount_pt}): {e}")
                print("[Info] Se você configurou uma HD externa, certifique-se de que ela está conectada e montada.")
            self.fuse_thread = threading.Thread(
                target=mount_fuse,
                args=(mount_pt, self.db, cache, api, account_id),
                kwargs={"foreground": True},
                daemon=True,
                name="FUSEThread"
            )
            self.fuse_thread.start()

        # 3. Setup Signal Handlers
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        # 4. Start Tray Indicator (blocking main thread)
        def _open_settings():
            dialog = SettingsDialog(self.config, self.auth, self._on_settings_saved)
            dialog.show()

        indicator = DriveTrayIndicator(
            config=self.config,
            db=self.db,
            sync_worker=self.sync_worker,
            omaha_updater=self.updater,
            github_updater=getattr(self, 'github_updater', None),
            on_open_settings=_open_settings
        )

        try:
            indicator.run()
        except KeyboardInterrupt:
            self._handle_shutdown(None, None)

    def _on_update_found(self, update_info):
        print(f"[Notificação] Nova versão do Google Drive disponível: {update_info['latest_version']}")

    def _on_settings_saved(self, new_config):
        self.config = new_config
        save_config(self.config)
        print("[Config] Novas configurações aplicadas com sucesso.")

    def _handle_shutdown(self, signum, frame):
        print("\n[Encerrando] Desmontando sistema de arquivos e finalizando threads...")
        self.running = False
        if self.sync_worker:
            self.sync_worker.stop()
        if self.updater:
            self.updater.stop()
        if hasattr(self, 'github_updater') and self.github_updater:
            self.github_updater.stop()
        sys.exit(0)


if __name__ == "__main__":
    daemon = GoogleDriveDaemon()
    daemon.start()
