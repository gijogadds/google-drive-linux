"""
Google Drive for Linux - Desktop System Tray Indicator
Integrates with Ubuntu GNOME and KDE using AyatanaAppIndicator3 / GTK3.
"""
import os
import sys
import webbrowser
import subprocess
from typing import Optional, Any

# Graceful import of GTK and AppIndicator
try:
    import gi
    gi.require_version("Gtk", "3.0")
    try:
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as AppIndicator3
    except (ValueError, ImportError):
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3
    from gi.repository import Gtk, GLib
    HAS_GTK = True
except Exception:
    HAS_GTK = False


class DriveTrayIndicator:
    def __init__(self, config: dict, db, sync_worker, omaha_updater, github_updater=None, on_open_settings=None):
        self.config = config
        self.db = db
        self.sync_worker = sync_worker
        self.updater = omaha_updater
        self.github_updater = github_updater
        self.on_open_settings = on_open_settings
        self.indicator = None
        self.menu = None

        assets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "icons")
        self.icon_active = os.path.join(assets_dir, "drive_logo_24px.svg")
        self.icon_syncing = os.path.join(assets_dir, "drive_logo_grey_20px.svg")

        if HAS_GTK:
            self._init_indicator()

    def _init_indicator(self):
        icon_path = self.icon_active if os.path.exists(self.icon_active) else "google-drive"
        self.indicator = AppIndicator3.Indicator.new(
            "google-drive-linux",
            icon_path,
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.update_menu()

        # Update periodic status in tray
        GLib.timeout_add_seconds(3, self._on_timer_tick)

    def _on_timer_tick(self):
        self.update_menu()
        return True

    def update_menu(self):
        if not HAS_GTK:
            return

        menu = Gtk.Menu()

        # 1. Accounts Header
        accounts = self.db.get_accounts()
        if accounts:
            for acc in accounts:
                acc_item = Gtk.MenuItem(label=f"👤 {acc.get('email', 'Conta Conectada')}")
                acc_item.set_sensitive(False)
                menu.append(acc_item)
        else:
            login_item = Gtk.MenuItem(label="➕ Conectar Conta Google...")
            login_item.connect("activate", self._on_login_click)
            menu.append(login_item)

        # 2. Sync Status
        activity = self.sync_worker.current_activity if self.sync_worker else "Pronto"
        status_item = Gtk.MenuItem(label=f"Status: {activity}")
        status_item.set_sensitive(False)
        menu.append(status_item)

        menu.append(Gtk.SeparatorMenuItem())

        # 3. Actions
        open_folder = Gtk.MenuItem(label="📂 Abrir pasta do Google Drive")
        open_folder.connect("activate", self._on_open_folder)
        menu.append(open_folder)

        open_web = Gtk.MenuItem(label="🌐 Abrir Google Drive na Web")
        open_web.connect("activate", lambda _: webbrowser.open("https://drive.google.com"))
        menu.append(open_web)

        menu.append(Gtk.SeparatorMenuItem())

        # 4. Pause / Resume
        if self.sync_worker and self.sync_worker.paused:
            pause_item = Gtk.MenuItem(label="▶️ Retomar Sincronização")
            pause_item.connect("activate", lambda _: self.sync_worker.resume())
        else:
            pause_item = Gtk.MenuItem(label="⏸️ Pausar Sincronização")
            pause_item.connect("activate", lambda _: self.sync_worker.pause() if self.sync_worker else None)
        menu.append(pause_item)

        # 5. Check Updates (Dual Channel)
        update_omaha = Gtk.MenuItem(label="🔄 Atualização Oficial do Google Drive (Google)")
        update_omaha.connect("activate", self._on_check_updates)
        menu.append(update_omaha)

        update_github = Gtk.MenuItem(label="⬆️ Atualização do Programa Linux (GitHub)")
        update_github.connect("activate", self._on_check_github_updates)
        menu.append(update_github)

        # 6. Preferences
        pref_item = Gtk.MenuItem(label="⚙️ Preferências...")
        if self.on_open_settings:
            pref_item.connect("activate", lambda _: self.on_open_settings())
        menu.append(pref_item)

        # 7. Diagnostic / Report Issue
        report_item = Gtk.MenuItem(label="🐛 Relatar Problema / Diagnóstico...")
        report_item.connect("activate", self._on_report_issue)
        menu.append(report_item)

        menu.append(Gtk.SeparatorMenuItem())

        # 8. Quit
        quit_item = Gtk.MenuItem(label="❌ Encerrar")
        quit_item.connect("activate", lambda _: Gtk.main_quit())
        menu.append(quit_item)

        menu.show_all()
        self.indicator.set_menu(menu)
        self.menu = menu

    def _on_login_click(self, _):
        if self.on_open_settings:
            self.on_open_settings()

    def _on_open_folder(self, _):
        mount_pt = self.config.get("mount_point", os.path.expanduser("~/GoogleDrive"))
        subprocess.Popen(["xdg-open", mount_pt])

    def _on_check_updates(self, _):
        if self.updater:
            res = self.updater.check_update()
            if res and res.get("has_update"):
                dialog = Gtk.MessageDialog(
                    message_type=Gtk.MessageType.INFO,
                    buttons=Gtk.ButtonsType.OK,
                    text=f"Nova versão oficial encontrada: {res['latest_version']}!"
                )
                dialog.format_secondary_text(f"Atualização disponível no CDN da Google.\nTamanho: {res.get('size_bytes', 0) // (1024*1024)} MB.")
                dialog.run()
                dialog.destroy()
            else:
                dialog = Gtk.MessageDialog(
                    message_type=Gtk.MessageType.INFO,
                    buttons=Gtk.ButtonsType.OK,
                    text="Você já está com a versão mais recente do Google Drive."
                )
                dialog.run()
                dialog.destroy()

    def _on_check_github_updates(self, _):
        if self.github_updater:
            res = self.github_updater.check_update()
            if res and res.get("has_update"):
                dialog = Gtk.MessageDialog(
                    message_type=Gtk.MessageType.INFO,
                    buttons=Gtk.ButtonsType.OK,
                    text=f"Nova melhoria do cliente Linux disponível: {res['new_version']}!"
                )
                dialog.format_secondary_text(f"Atualização: {res.get('title')}\nAplicando atualização...")
                dialog.run()
                dialog.destroy()
                self.github_updater.apply_update(res)
                self.github_updater.notify_user_and_restart(res)
            else:
                dialog = Gtk.MessageDialog(
                    message_type=Gtk.MessageType.INFO,
                    buttons=Gtk.ButtonsType.OK,
                    text="O programa Linux já está com as melhorias mais recentes do GitHub."
                )
                dialog.run()
                dialog.destroy()

    def _on_report_issue(self, _):
        from core.diagnostic import CrashReporter
        report = CrashReporter.generate_report(custom_notes="Relatado via menu da bandeja")
        CrashReporter.report_issue(report)

    def run(self):
        if HAS_GTK:
            Gtk.main()
