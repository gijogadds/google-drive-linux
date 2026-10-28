"""
Google Drive for Linux - Official Home Window & WebKit Frontend
Renders the exact official Google Drive Home Window (home_dialog_pt_br.html) via WebKit2GTK,
with full bridge callbacks for native Linux actions and GTK fallback.
"""
import os
import sys
import locale
import subprocess
import webbrowser
from typing import Dict, Any, Callable, Optional

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, GdkPixbuf
    try:
        try:
            gi.require_version("WebKit2", "4.1")
        except (ValueError, ImportError):
            gi.require_version("WebKit2", "4.0")
        from gi.repository import WebKit2
        HAS_WEBKIT = True
    except (ValueError, ImportError):
        HAS_WEBKIT = False
    HAS_GTK = True
except Exception:
    HAS_GTK = False
    HAS_WEBKIT = False


class GoogleDriveHomeWindow:
    def __init__(self, config: Dict[str, Any], db, sync_worker=None, on_open_settings=None):
        self.config = config
        self.db = db
        self.sync_worker = sync_worker
        self.on_open_settings = on_open_settings
        self.window = None

    def show(self):
        if not HAS_GTK:
            print("[UI] GTK não disponível.")
            return

        window = Gtk.Window(title="Google Drive")
        window.set_default_size(980, 680)
        window.set_position(Gtk.WindowPosition.CENTER)

        # Set official launcher icon (256x256 PNG)
        icon_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "assets", "icons", "drive_fs.png"
        )
        if os.path.exists(icon_path):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(icon_path)
                window.set_icon(pixbuf)
            except Exception:
                pass

        html_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "assets", "html"
        )

        # Detect locale for proper HTML version
        lang = locale.getdefaultlocale()[0] or "pt_BR"
        html_file = "home_dialog_pt_br.html" if "pt" in lang.lower() else "home_dialog_en.html"
        html_path = os.path.join(html_dir, html_file)
        if not os.path.exists(html_path):
            html_path = os.path.join(html_dir, "home_dialog_pt_br.html")

        if HAS_WEBKIT and os.path.exists(html_path):
            self._build_webkit_view(window, html_path)
        else:
            self._build_native_gtk_view(window)

        window.show_all()
        self.window = window

    def _build_webkit_view(self, window, html_path):
        """Render official Google Drive HTML/CSS frontend with JavaScript bridge."""
        content_manager = WebKit2.UserContentManager()
        
        # Register script message handlers
        content_manager.register_script_message_handler("openFolder")
        content_manager.register_script_message_handler("openSettings")
        content_manager.register_script_message_handler("togglePause")
        content_manager.register_script_message_handler("openWeb")

        content_manager.connect("script-message-received::openFolder", self._on_js_open_folder)
        content_manager.connect("script-message-received::openSettings", self._on_js_open_settings)
        content_manager.connect("script-message-received::togglePause", self._on_js_toggle_pause)
        content_manager.connect("script-message-received::openWeb", self._on_js_open_web)

        webview = WebKit2.WebView.new_with_user_content_manager(content_manager)
        settings = webview.get_settings()
        settings.set_enable_javascript(True)
        settings.set_allow_file_access_from_file_urls(True)
        settings.set_allow_universal_access_from_file_urls(True)

        uri = f"file://{os.path.abspath(html_path)}"
        webview.load_uri(uri)
        window.add(webview)

    def _build_native_gtk_view(self, window):
        """Native GTK3 high-fidelity visual replica of Google Drive Home."""
        assets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "html")
        icons_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "icons")

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        window.add(main_box)

        # 1. Top Header Bar
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        header.set_margin_top(12)
        header.set_margin_bottom(12)
        header.set_margin_start(20)
        header.set_margin_end(20)

        logo_label = Gtk.Label()
        logo_label.set_markup("<big><b>Google Drive</b></big>")
        header.pack_start(logo_label, False, False, 0)

        # Search Bar
        search_entry = Gtk.SearchEntry()
        search_entry.set_placeholder_text("Pesquisar no Google Drive...")
        search_entry.set_hexpand(True)
        header.pack_start(search_entry, True, True, 20)

        # Pause button
        btn_pause = Gtk.Button(label="⏸️")
        btn_pause.set_tooltip_text("Pausar sincronização")
        btn_pause.connect("clicked", lambda _: self._on_toggle_pause())
        header.pack_start(btn_pause, False, False, 0)

        # Settings Gear
        btn_gear = Gtk.Button(label="⚙️")
        btn_gear.set_tooltip_text("Preferências")
        btn_gear.connect("clicked", lambda _: self.on_open_settings() if self.on_open_settings else None)
        header.pack_start(btn_gear, False, False, 0)

        # Account Avatar Circle
        accounts = self.db.get_accounts()
        initial = accounts[0]["email"][0].upper() if accounts else "G"
        avatar_btn = Gtk.Button(label=f" {initial} ")
        avatar_btn.set_tooltip_text(accounts[0]["email"] if accounts else "Conta Google")
        header.pack_start(avatar_btn, False, False, 0)

        main_box.pack_start(header, False, False, 0)
        main_box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        # 2. Main Body: Sidebar + Dashboard Cards
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        main_box.pack_start(body, True, True, 0)

        # Left Sidebar
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        sidebar.set_size_request(220, -1)
        sidebar.set_margin_top(20)
        sidebar.set_margin_start(15)
        sidebar.set_margin_end(15)

        btn_open_folder = Gtk.Button(label="📂 Abrir pasta do Drive")
        btn_open_folder.connect("clicked", lambda _: self._on_open_folder())
        sidebar.pack_start(btn_open_folder, False, False, 0)

        btn_home = Gtk.Button(label="🏠 Início")
        btn_home.set_relief(Gtk.ReliefStyle.NONE)
        sidebar.pack_start(btn_home, False, False, 0)

        btn_activity = Gtk.Button(label="🔄 Atividade de sincronização")
        btn_activity.set_relief(Gtk.ReliefStyle.NONE)
        sidebar.pack_start(btn_activity, False, False, 0)

        btn_notifications = Gtk.Button(label="ℹ️ Notificações")
        btn_notifications.set_relief(Gtk.ReliefStyle.NONE)
        sidebar.pack_start(btn_notifications, False, False, 0)

        body.pack_start(sidebar, False, False, 0)
        body.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)

        # Right Dashboard Content
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        content.set_margin_top(25)
        content.set_margin_bottom(25)
        content.set_margin_start(25)
        content.set_margin_end(25)

        # Grid of Cards
        grid = Gtk.Grid()
        grid.set_column_spacing(20)
        grid.set_row_spacing(20)

        # Card 1: Status de Sincronizacao (The Dog Card!)
        card1 = self._create_card(
            "Status de sincronização",
            "Seus arquivos estão atualizados",
            "As atividades nos seus arquivos e pastas vão aparecer aqui.",
            os.path.join(assets_dir, "Selling-point-1.svg")
        )
        grid.attach(card1, 0, 0, 1, 1)

        # Card 2: Tudo em dia! (Girl with Flower!)
        card2 = self._create_card(
            "Tudo em dia!",
            "As coisas que precisam da sua atenção vão aparecer aqui.",
            "",
            os.path.join(assets_dir, "Selling-point-2.svg")
        )
        grid.attach(card2, 1, 0, 1, 1)

        # Card 3: Ver arquivos compartilhados
        card3 = self._create_card(
            "Ver arquivos compartilhados com você",
            "Fique por dentro dos arquivos compartilhados no Drive para Web.",
            "",
            os.path.join(assets_dir, "Selling-point-3.svg"),
            action_label="Ver tudo",
            action_callback=lambda: webbrowser.open("https://drive.google.com/drive/shared-with-me")
        )
        grid.attach(card3, 0, 1, 1, 1)

        # Card 4: Links Rapidos
        card4 = Gtk.Frame(label="Links rápidos")
        box4 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box4.set_margin_top(15)
        box4.set_margin_bottom(15)
        box4.set_margin_start(15)
        box4.set_margin_end(15)

        btn_l1 = Gtk.Button(label="+ Adicionar mais pastas para sincronizar")
        btn_l1.connect("clicked", lambda _: self.on_open_settings() if self.on_open_settings else None)
        box4.pack_start(btn_l1, False, False, 0)

        btn_l2 = Gtk.Button(label="↗ Abrir o Google Drive na Web")
        btn_l2.connect("clicked", lambda _: webbrowser.open("https://drive.google.com"))
        box4.pack_start(btn_l2, False, False, 0)

        btn_l3 = Gtk.Button(label="↗ Saiba mais sobre arquivos off-line")
        btn_l3.connect("clicked", lambda _: webbrowser.open("https://support.google.com/drive/answer/2375012"))
        box4.pack_start(btn_l3, False, False, 0)

        card4.add(box4)
        grid.attach(card4, 1, 1, 1, 1)

        content.pack_start(grid, True, True, 0)
        body.pack_start(content, True, True, 0)

    def _create_card(self, title, subtitle, desc, image_path, action_label=None, action_callback=None):
        frame = Gtk.Frame()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(15)
        box.set_margin_bottom(15)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_size_request(320, 180)

        lbl_t = Gtk.Label()
        lbl_t.set_markup(f"<b><big>{title}</big></b>")
        lbl_t.set_alignment(0, 0.5)
        box.pack_start(lbl_t, False, False, 0)

        lbl_s = Gtk.Label(label=subtitle)
        lbl_s.set_line_wrap(True)
        lbl_s.set_alignment(0, 0.5)
        box.pack_start(lbl_s, False, False, 0)

        if desc:
            lbl_d = Gtk.Label(label=desc)
            lbl_d.set_line_wrap(True)
            lbl_d.set_alignment(0, 0.5)
            box.pack_start(lbl_d, False, False, 0)

        if action_label and action_callback:
            btn = Gtk.Button(label=action_label)
            btn.connect("clicked", lambda _: action_callback())
            box.pack_start(btn, False, False, 5)

        frame.add(box)
        return frame

    # --- Actions ---
    def _on_open_folder(self):
        mount_pt = self.config.get("mount_point", os.path.expanduser("~/GoogleDrive"))
        subprocess.Popen(["xdg-open", mount_pt])

    def _on_toggle_pause(self):
        if self.sync_worker:
            if self.sync_worker.paused:
                self.sync_worker.resume()
            else:
                self.sync_worker.pause()

    def _on_js_open_folder(self, manager, message):
        self._on_open_folder()

    def _on_js_open_settings(self, manager, message):
        if self.on_open_settings:
            self.on_open_settings()

    def _on_js_toggle_pause(self, manager, message):
        self._on_toggle_pause()

    def _on_js_open_web(self, manager, message):
        webbrowser.open("https://drive.google.com")


# Settings Dialog class maintained for direct preferences access
class SettingsDialog:
    def __init__(self, config: Dict[str, Any], auth_manager, on_save_callback: Callable):
        self.config = config
        self.auth_manager = auth_manager
        self.on_save = on_save_callback

    def show(self):
        if not HAS_GTK:
            return

        window = Gtk.Window(title="Google Drive - Preferências")
        window.set_default_size(560, 480)
        window.set_position(Gtk.WindowPosition.CENTER)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.set_margin_top(20)
        vbox.set_margin_bottom(20)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)
        window.add(vbox)

        title_label = Gtk.Label()
        title_label.set_markup("<big><b>Preferências do Google Drive</b></big>")
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

        stream_rb = Gtk.RadioButton.new_with_label_from_widget(None, "Transmitir arquivos (Apenas online sob demanda - Economiza espaço)")
        stream_rb.set_active(self.config.get("sync_mode") == "stream")
        mirror_rb = Gtk.RadioButton.new_with_label_from_widget(stream_rb, "Espelhar arquivos (Disponível offline localmente)")
        mirror_rb.set_active(self.config.get("sync_mode") == "mirror")

        sync_box.pack_start(stream_rb, False, False, 0)
        sync_box.pack_start(mirror_rb, False, False, 0)

        # Helper for folder browser
        def _choose_folder(parent, entry):
            dialog = Gtk.FileChooserDialog(
                title="Selecione um Diretório (ex: na sua HD Externa)",
                transient_for=parent,
                action=Gtk.FileChooserAction.SELECT_FOLDER
            )
            dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
            if dialog.run() == Gtk.ResponseType.OK:
                entry.set_text(dialog.get_filename())
            dialog.destroy()

        # Mount Point Entry
        path_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        path_label = Gtk.Label(label="Ponto de Montagem:")
        path_entry = Gtk.Entry()
        path_entry.set_text(self.config.get("mount_point", os.path.expanduser("~/GoogleDrive")))
        btn_browse_mount = Gtk.Button(label="Procurar...")
        btn_browse_mount.connect("clicked", lambda _: _choose_folder(window, path_entry))

        path_box.pack_start(path_label, False, False, 0)
        path_box.pack_start(path_entry, True, True, 0)
        path_box.pack_start(btn_browse_mount, False, False, 0)
        sync_box.pack_start(path_box, False, False, 0)

        # Cache & Offline Storage Directory (Ideal for External HD)
        cache_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        cache_label = Gtk.Label(label="Pasta de Cache / Offline:")
        cache_entry = Gtk.Entry()
        default_cache = self.config.get("cache_dir", os.path.expanduser("~/.cache/google-drive-linux"))
        cache_entry.set_text(default_cache)
        btn_browse_cache = Gtk.Button(label="Procurar...")
        btn_browse_cache.connect("clicked", lambda _: _choose_folder(window, cache_entry))

        cache_box.pack_start(cache_label, False, False, 0)
        cache_box.pack_start(cache_entry, True, True, 0)
        cache_box.pack_start(btn_browse_cache, False, False, 0)
        sync_box.pack_start(cache_box, False, False, 0)

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
            self.config["cache_dir"] = cache_entry.get_text()
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
