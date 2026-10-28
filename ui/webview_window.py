"""
Google Drive for Linux - WebKit / GTK Settings & Onboarding Window
Renders official HTML/CSS dialogs or fallback native GTK dialogs for settings, account login and sync modes.
"""
import os
import sys
from typing import Dict, Any, Callable

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    try:
        gi.require_version("WebKit2", "4.0")
        from gi.repository import WebKit2
        HAS_WEBKIT = True
    except (ValueError, ImportError):
        HAS_WEBKIT = False
    HAS_GTK = True
except Exception:
    HAS_GTK = False
    HAS_WEBKIT = False


class SettingsDialog:
    def __init__(self, config: Dict[str, Any], auth_manager, on_save_callback: Callable):
        self.config = config
        self.auth_manager = auth_manager
        self.on_save = on_save_callback

    def show(self):
        if not HAS_GTK:
            print("[UI] GTK não disponível neste ambiente.")
            return

        window = Gtk.Window(title="Google Drive - Preferências")
        window.set_default_size(520, 420)
        window.set_position(Gtk.WindowPosition.CENTER)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.set_margin_top(20)
        vbox.set_margin_bottom(20)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)
        window.add(vbox)

        # Title
        title_label = Gtk.Label()
        title_label.set_markup("<big><b>Configurações do Google Drive</b></big>")
        title_label.set_alignment(0, 0.5)
        vbox.pack_start(title_label, False, False, 0)

        # Accounts Box
        acc_frame = Gtk.Frame(label="Contas Conectadas")
        acc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        acc_box.set_margin_top(10)
        acc_box.set_margin_start(10)
        acc_box.set_margin_end(10)
        acc_box.set_margin_bottom(10)

        accounts = self.auth_manager.accounts
        if accounts:
            for acc_id, acc_info in accounts.items():
                lbl = Gtk.Label(label=f"✓ {acc_info.get('email')} ({acc_info.get('display_name', '')})")
                lbl.set_alignment(0, 0.5)
                acc_box.pack_start(lbl, False, False, 0)
        else:
            lbl = Gtk.Label(label="Nenhuma conta conectada.")
            lbl.set_alignment(0, 0.5)
            acc_box.pack_start(lbl, False, False, 0)

        btn_login = Gtk.Button(label="+ Adicionar Conta Google (Login no Navegador)")
        btn_login.connect("activate", lambda _: self._on_login(window))
        btn_login.connect("clicked", lambda _: self._on_login(window))
        acc_box.pack_start(btn_login, False, False, 0)
        acc_frame.add(acc_box)
        vbox.pack_start(acc_frame, False, False, 0)

        # Mount Point & Sync Mode
        sync_frame = Gtk.Frame(label="Opções de Sincronização")
        sync_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        sync_box.set_margin_top(10)
        sync_box.set_margin_start(10)
        sync_box.set_margin_end(10)
        sync_box.set_margin_bottom(10)

        # Sync Mode Radio
        stream_rb = Gtk.RadioButton.new_with_label_from_widget(None, "Transmitir arquivos (Apenas online sob demanda - Economiza espaço)")
        stream_rb.set_active(self.config.get("sync_mode") == "stream")
        mirror_rb = Gtk.RadioButton.new_with_label_from_widget(stream_rb, "Espelhar arquivos (Disponível offline localmente)")
        mirror_rb.set_active(self.config.get("sync_mode") == "mirror")

        sync_box.pack_start(stream_rb, False, False, 0)
        sync_box.pack_start(mirror_rb, False, False, 0)

        # Mount Point Entry
        path_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        path_label = Gtk.Label(label="Ponto de Montagem:")
        path_entry = Gtk.Entry()
        path_entry.set_text(self.config.get("mount_point", os.path.expanduser("~/GoogleDrive")))
        path_box.pack_start(path_label, False, False, 0)
        path_box.pack_start(path_entry, True, True, 0)
        sync_box.pack_start(path_box, False, False, 0)

        sync_frame.add(sync_box)
        vbox.pack_start(sync_frame, False, False, 0)

        # Bottom Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_box.set_halign(Gtk.Align.END)

        btn_cancel = Gtk.Button(label="Cancelar")
        btn_cancel.connect("clicked", lambda _: window.destroy())
        btn_box.pack_start(btn_cancel, False, False, 0)

        btn_save = Gtk.Button(label="Salvar e Aplicar")
        def _save(_):
            self.config["sync_mode"] = "mirror" if mirror_rb.get_active() else "stream"
            self.config["mount_point"] = path_entry.get_text()
            self.on_save(self.config)
            window.destroy()

        btn_save.connect("clicked", _save)
        btn_box.pack_start(btn_save, False, False, 0)
        vbox.pack_start(btn_box, False, False, 0)

        window.show_all()

    def _on_login(self, parent_window):
        acc = self.auth_manager.login_interactive()
        if acc:
            dialog = Gtk.MessageDialog(
                transient_for=parent_window,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text=f"Conta conectada: {acc.get('email')}!"
            )
            dialog.run()
            dialog.destroy()
            parent_window.destroy()
            self.show()
