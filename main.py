#!/usr/bin/env python3
"""
Google Drive for Linux - Main Daemon & Service Entrypoint
Coordinates VFS mount, background sync worker, Omaha auto-updater, and tray indicator.
"""
import sys
import os
import signal
import socket
import argparse
import threading
import time

# Ensure project root is in python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import (
    load_config, save_config, ensure_directories, unmount_fuse_path,
    DEFAULT_MOUNT_POINT, IPC_SOCKET_PATH
)
from core.db import MetadataDB
from core.auth import AuthManager
from core.api import DriveAPI
from core.cache import CacheManager
from core.vfs import mount_fuse
from core.watcher import SyncWorker
from updater.omaha_updater import OmahaUpdater
from updater.github_updater import GitHubUpdater
from ui.indicator import DriveTrayIndicator
from ui.webview_window import SettingsDialog, GoogleDriveHomeWindow
from core.diagnostic import install_global_crash_hook

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib
    HAS_GLIB = True
except Exception:
    HAS_GLIB = False


def send_ipc_command(cmd: str) -> bool:
    """Try sending a command to an already-running GoogleDriveDaemon instance."""
    if not os.path.exists(IPC_SOCKET_PATH):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1.5)
            client.connect(IPC_SOCKET_PATH)
            client.sendall(cmd.encode("utf-8"))
            resp = client.recv(64).decode("utf-8", errors="ignore")
            return resp.strip() == "OK"
    except Exception:
        return False


class GoogleDriveDaemon:
    def __init__(self, open_home_on_start: bool = False, open_settings_on_start: bool = False):
        install_global_crash_hook()
        ensure_directories()
        self.config = load_config()
        self.db = MetadataDB()
        self.auth = AuthManager()
        self.sync_worker = None
        self.api = None
        self.cache = None
        self.account_id = None
        self.active_mount_point = None
        self.updater = None
        self.github_updater = None
        self.fuse_thread = None
        self.ipc_thread = None
        self.home_window = None
        self.settings_dialog = None
        self.indicator = None
        self.open_home_on_start = open_home_on_start
        self.open_settings_on_start = open_settings_on_start
        self.running = True

    def _mount_vfs_at(self, mount_pt: str) -> tuple:
        """Create mount directory and start FUSE thread at mount_pt."""
        if self.active_mount_point and self.active_mount_point != mount_pt:
            unmount_fuse_path(self.active_mount_point, remove_if_empty=True)
        if mount_pt != DEFAULT_MOUNT_POINT and os.path.exists(DEFAULT_MOUNT_POINT):
            unmount_fuse_path(DEFAULT_MOUNT_POINT, remove_if_empty=True)

        try:
            if os.path.islink(mount_pt):
                os.unlink(mount_pt)
            os.makedirs(mount_pt, exist_ok=True)
        except Exception as e:
            msg = f"Não foi possível criar a pasta de montagem ({mount_pt}): {e}"
            print(f"[Aviso] {msg}")
            return False, msg

        self.active_mount_point = mount_pt
        if self.api and self.cache and self.account_id:
            self.fuse_thread = threading.Thread(
                target=mount_fuse,
                args=(mount_pt, self.db, self.cache, self.api, self.account_id),
                kwargs={"foreground": True},
                daemon=True,
                name="FUSEThread"
            )
            self.fuse_thread.start()
        return True, f"As alterações foram aplicadas com sucesso!\n\nPasta do Google Drive ativa em:\n{mount_pt}"

    def ensure_sync_started(self):
        """Start SyncWorker and FUSE mount if an account is connected and not yet running."""
        if self.sync_worker is not None:
            return
        accounts = self.db.get_accounts()
        if not accounts:
            return
        account_id = accounts[0]["account_id"]
        self.account_id = account_id
        self.auth.accounts = self.auth._load_accounts_from_disk()

        self.api = DriveAPI(self.auth, account_id)
        self.cache = CacheManager(
            db=self.db,
            api=self.api,
            chunk_size_mb=self.config.get("chunk_size_mb", 16),
            max_cache_size_gb=self.config.get("max_cache_size_gb", 10),
            cache_dir=self.config.get("cache_dir")
        )

        self.sync_worker = SyncWorker(db=self.db, api=self.api, account_id=account_id)
        self.sync_worker.start()

        if self.indicator:
            self.indicator.sync_worker = self.sync_worker
        if self.home_window:
            self.home_window.sync_worker = self.sync_worker

        mount_pt = self.config.get("mount_point", DEFAULT_MOUNT_POINT)
        self._mount_vfs_at(mount_pt)

    def _start_ipc_server(self):
        """Listen on UNIX socket so desktop icon launches open the window in the running instance."""
        if os.path.exists(IPC_SOCKET_PATH):
            try:
                os.unlink(IPC_SOCKET_PATH)
            except OSError:
                pass

        def _serve():
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                srv.bind(IPC_SOCKET_PATH)
                os.chmod(IPC_SOCKET_PATH, 0o600)
                srv.listen(5)
                while self.running:
                    try:
                        conn, _ = srv.accept()
                        with conn:
                            data = conn.recv(128).decode("utf-8", errors="ignore").strip()
                            if data == "OPEN_HOME" and HAS_GLIB:
                                GLib.idle_add(self.open_home)
                            elif data == "OPEN_SETTINGS" and HAS_GLIB:
                                GLib.idle_add(self.open_settings)
                            elif data == "SYNC_START":
                                self.ensure_sync_started()
                            conn.sendall(b"OK")
                    except Exception:
                        if not self.running:
                            break
            finally:
                try:
                    srv.close()
                except Exception:
                    pass
                if os.path.exists(IPC_SOCKET_PATH):
                    try:
                        os.unlink(IPC_SOCKET_PATH)
                    except OSError:
                        pass

        self.ipc_thread = threading.Thread(target=_serve, daemon=True, name="IPCServerThread")
        self.ipc_thread.start()

    def open_settings(self):
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(
                self.config,
                self.auth,
                self._on_settings_saved,
                on_account_connected=self._on_account_connected
            )
        self.settings_dialog.show()
        return False

    def open_home(self):
        if self.home_window is None:
            self.home_window = GoogleDriveHomeWindow(
                config=self.config,
                db=self.db,
                auth_manager=self.auth,
                sync_worker=self.sync_worker,
                on_open_settings=self.open_settings,
                on_account_connected=self._on_account_connected
            )
        self.home_window.show()
        return False

    def _on_account_connected(self, _acc):
        self.ensure_sync_started()
        if self.indicator:
            self.indicator.update_menu()

    def start(self):
        print("==================================================")
        print("    Iniciando Google Drive para Linux (Ubuntu)    ")
        print("==================================================")

        self._start_ipc_server()

        accounts = self.db.get_accounts()
        if not accounts:
            print("[Aviso] Nenhuma conta configurada.")
            print("[Info] Abra o aplicativo ou rode 'gdrive-cli login' para conectar.")

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
        self.ensure_sync_started()

        # Periodically check if an account was added via CLI while daemon is running
        if HAS_GLIB:
            GLib.timeout_add_seconds(5, self._periodic_account_check)

        # 3. Setup Signal Handlers
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        # 4. Start Tray Indicator (blocking main thread)
        self.indicator = DriveTrayIndicator(
            config=self.config,
            db=self.db,
            sync_worker=self.sync_worker,
            omaha_updater=self.updater,
            github_updater=self.github_updater,
            on_open_settings=self.open_settings,
            on_open_home=self.open_home
        )

        if HAS_GLIB:
            if self.open_settings_on_start:
                GLib.idle_add(self.open_settings)
            elif self.open_home_on_start:
                GLib.idle_add(self.open_home)

        try:
            self.indicator.run()
        except KeyboardInterrupt:
            self._handle_shutdown(None, None)

    def _periodic_account_check(self):
        if self.sync_worker is None and self.db.get_accounts():
            self.ensure_sync_started()
        return True

    def _on_update_found(self, update_info):
        print(f"[Notificação] Nova versão do Google Drive disponível: {update_info['latest_version']}")

    def _on_settings_saved(self, new_config):
        old_mount = self.active_mount_point or self.config.get("mount_point", DEFAULT_MOUNT_POINT)
        new_mount = new_config.get("mount_point", DEFAULT_MOUNT_POINT)
        self.config = dict(new_config)
        save_config(self.config)

        if self.home_window:
            self.home_window.config = self.config
        if self.indicator:
            self.indicator.config = self.config
            self.indicator.update_menu()
        if self.settings_dialog:
            self.settings_dialog.config = self.config

        if new_mount != old_mount or not os.path.exists(new_mount):
            ok, msg = self._mount_vfs_at(new_mount)
            print(f"[Config] {msg}")
            return ok, msg

        print("[Config] Novas configurações aplicadas com sucesso.")
        return True, f"Configurações salvas e aplicadas com sucesso!\n\nPasta do Google Drive ativa em:\n{new_mount}"

    def _handle_shutdown(self, signum, frame):
        print("\n[Encerrando] Desmontando sistema de arquivos e finalizando threads...")
        self.running = False
        mount_pt = self.config.get("mount_point", DEFAULT_MOUNT_POINT)
        try:
            import subprocess
            subprocess.run(["fusermount3", "-uz", mount_pt], stderr=subprocess.DEVNULL, check=False)
            subprocess.run(["fusermount", "-uz", mount_pt], stderr=subprocess.DEVNULL, check=False)
        except Exception:
            pass
        if os.path.exists(IPC_SOCKET_PATH):
            try:
                os.unlink(IPC_SOCKET_PATH)
            except OSError:
                pass
        if self.sync_worker:
            self.sync_worker.stop()
        if self.updater:
            self.updater.stop()
        if self.github_updater:
            self.github_updater.stop()
        sys.exit(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Google Drive para Linux Daemon & UI")
    parser.add_argument("--daemon", action="store_true", help="Inicia apenas em segundo plano (systemd)")
    parser.add_argument("--open-home", action="store_true", help="Abre a janela principal do Google Drive")
    parser.add_argument("--settings", action="store_true", help="Abre a janela de Preferências")
    args, _ = parser.parse_known_args()

    # Check if an instance is already running via IPC
    if args.settings:
        if send_ipc_command("OPEN_SETTINGS"):
            sys.exit(0)
    elif not args.daemon or args.open_home:
        if send_ipc_command("OPEN_HOME"):
            sys.exit(0)
    else:
        if send_ipc_command("SYNC_START"):
            sys.exit(0)

    open_home = (not args.daemon) or args.open_home
    daemon = GoogleDriveDaemon(
        open_home_on_start=open_home,
        open_settings_on_start=args.settings
    )
    daemon.start()
