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

# Ensure project root is in python path and desktop app identity is set before GTK init
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.argv[0] = "google-drive"

try:
    import gi
    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gdk, Gtk
    GLib.set_prgname("google-drive")
    if GLib.get_application_name() != "Google Drive":
        GLib.set_application_name("Google Drive")
    Gdk.set_program_class("google-drive")
    _icon_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icons", "drive_fs.png")
    if os.path.exists(_icon_file):
        try:
            Gtk.Window.set_default_icon_from_file(_icon_file)
        except Exception:
            pass
    HAS_GLIB = True
except Exception:
    HAS_GLIB = False

from core.config import (
    load_config, save_config, ensure_directories, unmount_fuse_path,
    ensure_apparmor_allows_mount, ensure_nautilus_mount_icons,
    get_account_mount_point, get_account_display_name,
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
        self.account_runtimes = {}
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

    def _mount_vfs_for_account(self, account_id: str, mount_pt: str, api, cache) -> tuple:
        """Create mount directory and start FUSE thread at mount_pt for a specific account."""
        rt = self.account_runtimes.get(account_id, {})
        old_mount = rt.get("mount_point")
        if old_mount and old_mount != mount_pt:
            unmount_fuse_path(old_mount, remove_if_empty=True)
        if mount_pt != DEFAULT_MOUNT_POINT and os.path.exists(DEFAULT_MOUNT_POINT):
            # Only clean default mount point if no account uses it
            used_mounts = set((self.config.get("account_mounts") or {}).values())
            if DEFAULT_MOUNT_POINT not in used_mounts:
                unmount_fuse_path(DEFAULT_MOUNT_POINT, remove_if_empty=True)

        ensure_apparmor_allows_mount(mount_pt)
        ensure_nautilus_mount_icons(self.config)
        try:
            if os.path.islink(mount_pt):
                os.unlink(mount_pt)
            os.makedirs(mount_pt, exist_ok=True)
        except Exception as e:
            msg = f"Não foi possível criar a pasta de montagem ({mount_pt}): {e}"
            print(f"[Aviso] {msg}")
            return False, msg

        vol_name = get_account_display_name(self.config, account_id)
        fuse_thread = threading.Thread(
            target=mount_fuse,
            args=(mount_pt, self.db, cache, api, account_id),
            kwargs={"foreground": True, "volume_name": vol_name},
            daemon=True,
            name=f"FUSEThread-{account_id[:6]}"
        )
        fuse_thread.start()
        if account_id in self.account_runtimes:
            self.account_runtimes[account_id]["mount_point"] = mount_pt
            self.account_runtimes[account_id]["fuse_thread"] = fuse_thread
        if account_id == self.account_id:
            self.active_mount_point = mount_pt
            self.fuse_thread = fuse_thread
        return True, f"As alterações foram aplicadas com sucesso!\n\nPasta do Google Drive ativa em:\n{mount_pt}"

    def _mount_vfs_at(self, mount_pt: str) -> tuple:
        """Mount VFS for the currently active account."""
        if not self.account_id or not self.api or not self.cache:
            return False, "Nenhuma conta ativa para montar."
        if "account_mounts" not in self.config or not isinstance(self.config["account_mounts"], dict):
            self.config["account_mounts"] = {}
        self.config["account_mounts"][self.account_id] = mount_pt
        save_config(self.config)
        return self._mount_vfs_for_account(self.account_id, mount_pt, self.api, self.cache)

    def ensure_sync_started(self):
        """Start SyncWorker and FUSE mount for all connected accounts simultaneously, and focus the active account."""
        accounts = self.db.get_accounts()
        if not accounts:
            return
        self.auth.accounts = self.auth._load_accounts_from_disk()
        if "account_mounts" not in self.config or not isinstance(self.config.get("account_mounts"), dict):
            self.config["account_mounts"] = {}

        # Primary account is the first account registered in accounts.json (or first in DB)
        primary_id = list(self.auth.accounts.keys())[0] if self.auth.accounts else accounts[0]["account_id"]
        if primary_id not in self.config["account_mounts"]:
            self.config["account_mounts"][primary_id] = self.config.get("mount_point", DEFAULT_MOUNT_POINT)
            save_config(self.config)

        for idx, acc in enumerate(accounts):
            acc_id = acc["account_id"]
            email = acc.get("email", "")
            is_primary = (acc_id == primary_id)
            mount_pt = get_account_mount_point(self.config, acc_id, email=email, is_primary=is_primary)

            if acc_id not in self.account_runtimes:
                api = DriveAPI(self.auth, acc_id)
                base_cache = self.config.get("cache_dir") or os.path.expanduser("~/.cache/google-drive-linux")
                acc_cache_dir = base_cache if is_primary else os.path.join(base_cache, f"acc_{acc_id}")
                cache = CacheManager(
                    db=self.db,
                    api=api,
                    chunk_size_mb=self.config.get("chunk_size_mb", 16),
                    max_cache_size_gb=self.config.get("max_cache_size_gb", 10),
                    cache_dir=acc_cache_dir
                )
                worker = SyncWorker(db=self.db, api=api, account_id=acc_id)
                worker.start()
                self.account_runtimes[acc_id] = {
                    "api": api,
                    "cache": cache,
                    "sync_worker": worker,
                    "fuse_thread": None,
                    "mount_point": mount_pt,
                    "email": email,
                }
                self._mount_vfs_for_account(acc_id, mount_pt, api, cache)

        # Active account is accounts[0] (ordered by last_sync DESC)
        active_acc = accounts[0]
        active_id = active_acc["account_id"]
        rt = self.account_runtimes[active_id]
        self.account_id = active_id
        self.api = rt["api"]
        self.cache = rt["cache"]
        self.sync_worker = rt["sync_worker"]
        self.fuse_thread = rt["fuse_thread"]
        self.active_mount_point = rt["mount_point"]

        if self.indicator:
            self.indicator.sync_worker = self.sync_worker
        if self.home_window:
            self.home_window.sync_worker = self.sync_worker

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

    def _on_account_connected(self, acc):
        if isinstance(acc, dict) and acc.get("mount_point") and acc.get("account_id"):
            if "account_mounts" not in self.config or not isinstance(self.config.get("account_mounts"), dict):
                self.config["account_mounts"] = {}
            acc_id = acc["account_id"]
            new_mount = acc["mount_point"]
            self.config["account_mounts"][acc_id] = new_mount
            save_config(self.config)
            if acc_id in self.account_runtimes:
                rt = self.account_runtimes[acc_id]
                if rt.get("mount_point") != new_mount or not os.path.exists(new_mount):
                    self._mount_vfs_for_account(acc_id, new_mount, rt["api"], rt["cache"])
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
        accounts = self.db.get_accounts()
        if accounts and len(self.account_runtimes) < len(accounts):
            self.ensure_sync_started()
        return True

    def _on_update_found(self, update_info):
        print(f"[Notificação] Nova versão do Google Drive disponível: {update_info['latest_version']}")

    def _on_settings_saved(self, new_config):
        old_mount = self.active_mount_point or self.config.get("mount_point", DEFAULT_MOUNT_POINT)
        new_mount = new_config.get("mount_point", DEFAULT_MOUNT_POINT)
        self.config = dict(new_config)
        save_config(self.config)
        ensure_nautilus_mount_icons(self.config)

        if self.home_window:
            self.home_window.config = self.config
        if self.indicator:
            self.indicator.config = self.config
            self.indicator.update_menu()
        if self.settings_dialog:
            self.settings_dialog.config = self.config

        # Apply any renamed/relocated account mounts immediately
        new_account_mounts = self.config.get("account_mounts") or {}
        remounted_any = False
        last_msg = ""
        for acc_id, target_mp in list(new_account_mounts.items()):
            if acc_id in self.account_runtimes and target_mp:
                rt = self.account_runtimes[acc_id]
                cur_mp = rt.get("mount_point")
                if cur_mp != target_mp or not os.path.exists(target_mp):
                    ok, msg = self._mount_vfs_for_account(acc_id, target_mp, rt["api"], rt["cache"])
                    remounted_any = True
                    last_msg = msg
                    if not ok:
                        return ok, msg

        if not remounted_any and (new_mount != old_mount or not os.path.exists(new_mount)):
            ok, msg = self._mount_vfs_at(new_mount)
            print(f"[Config] {msg}")
            return ok, msg

        if remounted_any:
            return True, last_msg

        print("[Config] Novas configurações aplicadas com sucesso.")
        return True, f"Configurações salvas e aplicadas com sucesso!\n\nPasta do Google Drive ativa em:\n{new_mount}"

    def _handle_shutdown(self, signum, frame):
        print("\n[Encerrando] Desmontando sistema de arquivos e finalizando threads...")
        self.running = False
        for acc_id, rt in list(self.account_runtimes.items()):
            mnt = rt.get("mount_point")
            if mnt:
                unmount_fuse_path(mnt, remove_if_empty=False)
            worker = rt.get("sync_worker")
            if worker:
                worker.stop()
        mount_pt = self.config.get("mount_point", DEFAULT_MOUNT_POINT)
        unmount_fuse_path(mount_pt, remove_if_empty=False)
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
