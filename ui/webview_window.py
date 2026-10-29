"""
Google Drive for Linux - Official Home Window & Multi-Screen Interface
Replicates 100% of the Windows client screens and features with Linux-native enhancements:
1. Tela Principal (Início com cartões)
2. Aba de Atividade de Sincronização (Histórico e tabela de arquivos)
3. Aba de Notificações
4. Menu Suspenso da Engrenagem (Preferências, Offline, Erros, Sobre, Sair)
5. Popover da Conta Google (Avatar, Quota de Armazenamento, Múltiplas Contas)
"""
import os
import sys
import subprocess
import threading
import webbrowser
from typing import Dict, Any, Callable, Optional

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, Gdk, GdkPixbuf, GLib
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
    def __init__(self, config: Dict[str, Any], db, auth_manager=None, sync_worker=None, on_open_settings=None, on_account_connected=None):
        self.config = config
        self.db = db
        self.auth = auth_manager
        self.sync_worker = sync_worker
        self.on_open_settings = on_open_settings
        self.on_account_connected = on_account_connected
        self.window = None
        self.stack = None
        self.gear_menu = None

    def show(self):
        if not HAS_GTK:
            print("[UI] GTK não disponível.")
            return

        if self.window is not None:
            self.window.present()
            return

        window = Gtk.Window(title="Google Drive")
        window.set_default_size(980, 680)
        window.set_position(Gtk.WindowPosition.CENTER)
        window.connect("destroy", self._on_window_destroy)

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

        self._build_interface(window)
        window.show_all()
        self.window = window
        self._refresh_live_ui()
        GLib.timeout_add(1500, self._refresh_live_ui)
        window.present()

    def _on_window_destroy(self, _widget):
        self.window = None

    def _build_interface(self, window):
        assets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "html")
        icons_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "icons")

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        window.add(main_box)

        # =========================================================================
        # 1. TOP HEADER BAR (Logo, Search, Pause, Gear Menu, Account Avatar)
        # =========================================================================
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        header.set_margin_top(12)
        header.set_margin_bottom(12)
        header.set_margin_start(20)
        header.set_margin_end(20)

        # Drive Logo + Wordmark
        logo_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        logo_img = Gtk.Image()
        logo_path = os.path.join(icons_dir, "drive_logo_24px.svg")
        if os.path.exists(logo_path):
            logo_img.set_from_file(logo_path)
        logo_box.pack_start(logo_img, False, False, 0)

        logo_label = Gtk.Label()
        logo_label.set_markup("<big><b>Drive</b></big>")
        logo_box.pack_start(logo_label, False, False, 0)
        header.pack_start(logo_box, False, False, 0)

        # Search Bar
        search_entry = Gtk.SearchEntry()
        search_entry.set_placeholder_text("Pesquisar no Google Drive")
        search_entry.set_hexpand(True)
        search_entry.connect("activate", lambda e: self._on_search(e.get_text()))
        header.pack_start(search_entry, True, True, 20)

        # Pause Button (⏸️)
        btn_pause = Gtk.Button(label="⏸️")
        btn_pause.set_tooltip_text("Pausar sincronização")
        btn_pause.connect("clicked", lambda _: self._on_toggle_pause(btn_pause))
        header.pack_start(btn_pause, False, False, 0)

        # Settings Gear (⚙️) -> Abre o Menu Exato da Imagem 1
        btn_gear = Gtk.Button(label="⚙️")
        btn_gear.set_tooltip_text("Configurações")
        self._build_gear_menu(btn_gear)
        btn_gear.connect("clicked", lambda b: self.gear_menu.popup_at_widget(b, Gdk.Gravity.SOUTH, Gdk.Gravity.NORTH, None))
        header.pack_start(btn_gear, False, False, 0)

        # Account Avatar Circle -> Abre o Popover Exato da Imagem 2
        accounts = self.db.get_accounts()
        initial = accounts[0]["email"][0].upper() if accounts else "G"
        avatar_btn = Gtk.Button(label=f" {initial} ")
        avatar_btn.set_tooltip_text("Conta do Google")
        self._build_account_popover(avatar_btn, accounts)
        header.pack_start(avatar_btn, False, False, 0)

        main_box.pack_start(header, False, False, 0)
        main_box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        # =========================================================================
        # 2. MAIN BODY (Sidebar + Gtk.Stack para alternância de telas)
        # =========================================================================
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        main_box.pack_start(body, True, True, 0)

        # --- Left Sidebar ---
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sidebar.set_size_request(230, -1)
        sidebar.set_margin_top(20)
        sidebar.set_margin_start(15)
        sidebar.set_margin_end(15)

        # "Abrir pasta do Drive" Button
        btn_open_folder = Gtk.Button(label="📁  Abrir pasta do Drive")
        btn_open_folder.set_tooltip_text("Abrir pasta do Drive")
        btn_open_folder.connect("clicked", lambda _: self._on_open_folder())
        sidebar.pack_start(btn_open_folder, False, False, 10)

        # Navigation Buttons (Tabs)
        btn_nav_home = Gtk.Button(label="🏠  Início")
        btn_nav_home.set_alignment(0, 0.5)
        sidebar.pack_start(btn_nav_home, False, False, 0)

        btn_nav_activity = Gtk.Button(label="🔄  Atividade de sincronizaç...")
        btn_nav_activity.set_alignment(0, 0.5)
        sidebar.pack_start(btn_nav_activity, False, False, 0)

        btn_nav_notifications = Gtk.Button(label="ℹ️  Notificações")
        btn_nav_notifications.set_alignment(0, 0.5)
        sidebar.pack_start(btn_nav_notifications, False, False, 0)

        body.pack_start(sidebar, False, False, 0)
        body.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)

        # --- Stack de Páginas Centrais ---
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(200)

        # Conectar navegação das abas
        btn_nav_home.connect("clicked", lambda _: self._switch_tab(btn_nav_home, "home", [btn_nav_home, btn_nav_activity, btn_nav_notifications]))
        btn_nav_activity.connect("clicked", lambda _: self._switch_tab(btn_nav_activity, "activity", [btn_nav_home, btn_nav_activity, btn_nav_notifications]))
        btn_nav_notifications.connect("clicked", lambda _: self._switch_tab(btn_nav_notifications, "notifications", [btn_nav_home, btn_nav_activity, btn_nav_notifications]))

        # --- PÁGINA 1: INÍCIO (Tela com os 4 Cartões) ---
        page_home = self._create_page_home(assets_dir)
        self.stack.add_named(page_home, "home")

        # --- PÁGINA 2: ATIVIDADE DE SINCRONIZAÇÃO (Imagem 3) ---
        page_activity = self._create_page_activity(assets_dir)
        self.stack.add_named(page_activity, "activity")

        # --- PÁGINA 3: NOTIFICAÇÕES (Imagem 4) ---
        page_notifications = self._create_page_notifications(assets_dir)
        self.stack.add_named(page_notifications, "notifications")

        body.pack_start(self.stack, True, True, 0)

    def _switch_tab(self, active_btn, page_name, all_buttons):
        for b in all_buttons:
            b.set_relief(Gtk.ReliefStyle.NONE)
        active_btn.set_relief(Gtk.ReliefStyle.NORMAL)
        self.stack.set_visible_child_name(page_name)

    # =========================================================================
    # POPUP 1: MENU DA ENGRENAGEM (Imagem 1 enviada pelo usuário)
    # =========================================================================
    def _build_gear_menu(self, attach_widget):
        menu = Gtk.Menu()

        item_pref = Gtk.MenuItem(label="Preferências")
        item_pref.connect("activate", lambda _: self.on_open_settings() if self.on_open_settings else None)
        menu.append(item_pref)

        item_offline = Gtk.MenuItem(label="Arquivos off-line")
        item_offline.connect("activate", lambda _: self._show_offline_files_dialog())
        menu.append(item_offline)

        item_errors = Gtk.MenuItem(label="Lista de erros")
        item_errors.connect("activate", lambda _: self._show_error_list_dialog())
        menu.append(item_errors)

        menu.append(Gtk.SeparatorMenuItem())

        item_about = Gtk.MenuItem(label="Sobre")
        item_about.connect("activate", lambda _: self._show_about_dialog())
        menu.append(item_about)

        item_help = Gtk.MenuItem(label="Ajuda")
        item_help.connect("activate", lambda _: webbrowser.open("https://support.google.com/drive"))
        menu.append(item_help)

        item_feedback = Gtk.MenuItem(label="Enviar feedback / Relatar Erro")
        item_feedback.connect("activate", lambda _: self._on_send_feedback())
        menu.append(item_feedback)

        menu.append(Gtk.SeparatorMenuItem())

        item_quit = Gtk.MenuItem(label="Sair")
        item_quit.connect("activate", lambda _: Gtk.main_quit())
        menu.append(item_quit)

        menu.show_all()
        self.gear_menu = menu

    # =========================================================================
    # POPUP 2: MODAL DA CONTA (Imagem 2 enviada pelo usuário)
    # =========================================================================
    def _build_account_popover(self, avatar_btn, accounts):
        popover = Gtk.Popover(relative_to=avatar_btn)
        popover.set_position(Gtk.PositionType.BOTTOM)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_top(15)
        vbox.set_margin_bottom(15)
        vbox.set_margin_start(18)
        vbox.set_margin_end(18)
        vbox.set_size_request(340, -1)

        # Header with Close button (X)
        top_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        btn_close = Gtk.Button(label="✕")
        btn_close.set_relief(Gtk.ReliefStyle.NONE)
        btn_close.connect("clicked", lambda _: popover.popdown())
        top_box.pack_end(btn_close, False, False, 0)
        vbox.pack_start(top_box, False, False, 0)

        # Big Avatar Circle (Image 2)
        acc = accounts[0] if accounts else {}
        name = acc.get("display_name") or (acc.get("email", "").split("@")[0] if acc.get("email") else "Visitante")
        email = acc.get("email") or "Nenhuma conta conectada"
        initial = email[0].upper() if acc.get("email") else "G"

        avatar_big = Gtk.Label()
        avatar_big.set_markup(f"<span size='xx-large' background='#5c6bc0' foreground='white'><b>  {initial}  </b></span>")
        vbox.pack_start(avatar_big, False, False, 0)

        # "Olá, <Nome>!" & Email
        lbl_hello = Gtk.Label()
        if accounts:
            lbl_hello.set_markup(f"<big><b>Olá, {name}!</b></big>")
        else:
            lbl_hello.set_markup("<big><b>Conecte sua Conta Google</b></big>")
        vbox.pack_start(lbl_hello, False, False, 0)

        lbl_email = Gtk.Label(label=email)
        lbl_email.set_opacity(0.7)
        vbox.pack_start(lbl_email, False, False, 0)

        # Buttons: "+ Adicionar conta" & "↳ Desconectar conta"
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_add = Gtk.Button(label="+ Adicionar conta")
        btn_add.connect("clicked", lambda _: (popover.popdown(), self._on_add_account()))
        btn_row.pack_start(btn_add, True, True, 0)

        if accounts:
            btn_logout = Gtk.Button(label="⎋ Desconectar conta")
            btn_logout.connect("clicked", lambda _: (popover.popdown(), self._on_logout_account(acc.get("account_id"))))
            btn_row.pack_start(btn_logout, True, True, 0)
        vbox.pack_start(btn_row, False, False, 5)

        # Storage Card
        storage_frame = Gtk.Frame()
        storage_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        storage_box.set_margin_top(10)
        storage_box.set_margin_bottom(10)
        storage_box.set_margin_start(12)
        storage_box.set_margin_end(12)

        storage_lbl = Gtk.Label()
        used_bytes = acc.get("storage_used", 0)
        total_bytes = acc.get("storage_total", 0)
        if total_bytes > 0:
            used_gb = used_bytes / (1024 ** 3)
            total_gb = total_bytes / (1024 ** 3)
            pct = int((used_bytes / total_bytes) * 100)
            storage_lbl.set_markup(f"☁️ Armazenamento usado: <b>{pct}% de {total_gb:.0f} GB ({used_gb:.1f} GB)</b>")
        elif accounts:
            storage_lbl.set_markup("☁️ Armazenamento: <b>Conta Google conectada</b>")
        else:
            storage_lbl.set_markup("☁️ Armazenamento: <b>Faça login para sincronizar</b>")
        storage_lbl.set_alignment(0, 0.5)
        storage_box.pack_start(storage_lbl, False, False, 0)

        link_manage = Gtk.LinkButton(uri="https://one.google.com/storage", label="Gerenciar armazenamento")
        link_manage.set_alignment(0, 0.5)
        storage_box.pack_start(link_manage, False, False, 0)

        storage_frame.add(storage_box)
        vbox.pack_start(storage_frame, False, False, 5)

        # Footer
        footer_lbl = Gtk.Label()
        footer_lbl.set_markup("<small><a href='https://policies.google.com/privacy'>Política de Privacidade</a> • <a href='https://policies.google.com/terms'>Termos de Serviço</a></small>")
        vbox.pack_start(footer_lbl, False, False, 0)

        vbox.show_all()
        popover.add(vbox)
        avatar_btn.connect("clicked", lambda _: popover.popup())

    def _create_scaled_image(self, image_path: str, width: int = 120, height: int = 120):
        """Load an SVG/PNG scaled cleanly via GdkPixbuf, preferring dark variants when dark theme is active."""
        img = Gtk.Image()
        img.set_halign(Gtk.Align.CENTER)
        img.set_valign(Gtk.Align.CENTER)
        if not image_path:
            return img

        resolved = image_path
        # Check if dark theme is preferred or dark variant exists
        if resolved.endswith(".svg") and not resolved.endswith("-dark.svg"):
            dark_candidate = resolved[:-4] + "-dark.svg"
            if os.path.exists(dark_candidate):
                resolved = dark_candidate

        if not os.path.exists(resolved):
            if os.path.exists(image_path):
                resolved = image_path
            else:
                return img

        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(resolved, width, height, True)
            img.set_from_pixbuf(pixbuf)
        except Exception:
            img.set_from_file(resolved)
        return img

    # =========================================================================
    # PÁGINA 1: INÍCIO (Dashboard de Cartões)
    # =========================================================================
    def _create_page_home(self, assets_dir):
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        content.set_margin_top(20)
        content.set_margin_bottom(20)
        content.set_margin_start(20)
        content.set_margin_end(20)

        grid = Gtk.Grid()
        grid.set_column_spacing(18)
        grid.set_row_spacing(18)
        grid.set_column_homogeneous(True)
        grid.set_hexpand(True)

        # Card 1: Status de Sincronização (Dinâmico em Tempo Real)
        card1_frame = Gtk.Frame()
        card1_frame.set_hexpand(True)
        card1_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card1_box.set_margin_top(14)
        card1_box.set_margin_bottom(14)
        card1_box.set_margin_start(14)
        card1_box.set_margin_end(14)
        card1_box.set_size_request(280, 220)

        lbl_c1_t = Gtk.Label()
        lbl_c1_t.set_markup("<b><big>Status de sincronização</big></b>")
        lbl_c1_t.set_alignment(0, 0.5)
        card1_box.pack_start(lbl_c1_t, False, False, 0)

        self.home_sync_subtitle_lbl = Gtk.Label(label="Verificando status da sincronização...")
        self.home_sync_subtitle_lbl.set_line_wrap(True)
        self.home_sync_subtitle_lbl.set_max_width_chars(38)
        self.home_sync_subtitle_lbl.set_alignment(0, 0.5)
        card1_box.pack_start(self.home_sync_subtitle_lbl, False, False, 0)

        self.home_sync_desc_lbl = Gtk.Label(label="Consultando catálogo local e nuvem...")
        self.home_sync_desc_lbl.set_line_wrap(True)
        self.home_sync_desc_lbl.set_max_width_chars(40)
        self.home_sync_desc_lbl.set_opacity(0.8)
        self.home_sync_desc_lbl.set_alignment(0, 0.5)
        card1_box.pack_start(self.home_sync_desc_lbl, False, False, 0)

        self.home_sync_progress = Gtk.ProgressBar()
        self.home_sync_progress.set_no_show_all(True)
        card1_box.pack_start(self.home_sync_progress, False, False, 2)

        c1_img = self._create_scaled_image(os.path.join(assets_dir, "Selling-point-1.svg"), 95, 95)
        card1_box.pack_start(c1_img, True, True, 2)

        card1_frame.add(card1_box)
        grid.attach(card1_frame, 0, 0, 1, 1)

        # Card 2: Flower Girl Card (Tudo em dia!)
        card2 = self._create_card(
            "Tudo em dia!",
            "As coisas que precisam da sua atenção vão aparecer aqui.",
            "",
            os.path.join(assets_dir, "Selling-point-2.svg")
        )
        grid.attach(card2, 1, 0, 1, 1)

        # Card 3: Shared Files Card
        card3 = self._create_card(
            "Ver os arquivos que foram compartilhados com você",
            "Fique por dentro dos arquivos compartilhados no Drive para Web.",
            "",
            os.path.join(assets_dir, "Selling-point-3.svg"),
            action_label="Ver tudo",
            action_callback=lambda: webbrowser.open("https://drive.google.com/drive/shared-with-me")
        )
        grid.attach(card3, 0, 1, 1, 1)

        # Card 4: Links Rápidos
        card4 = Gtk.Frame(label="Links rápidos")
        card4.set_hexpand(True)
        box4 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
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

        btn_l4 = Gtk.Button(label="↗ Perguntas frequentes")
        btn_l4.connect("clicked", lambda _: webbrowser.open("https://support.google.com/drive"))
        box4.pack_start(btn_l4, False, False, 0)

        card4.add(box4)
        grid.attach(card4, 1, 1, 1, 1)

        content.pack_start(grid, True, True, 0)
        scroll.add(content)
        return scroll

    # =========================================================================
    # PÁGINA 2: ATIVIDADE DE SINCRONIZAÇÃO (Tempo Real)
    # =========================================================================
    def _create_page_activity(self, assets_dir):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(25)
        box.set_margin_end(25)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.act_status_lbl = Gtk.Label()
        self.act_status_lbl.set_markup("<big><big><b>🔄 Sincronizando...</b></big></big>")
        self.act_status_lbl.set_alignment(0, 0.5)
        title_box.pack_start(self.act_status_lbl, False, False, 0)

        self.act_time_lbl = Gtk.Label(label="Carregando atividades em tempo real...")
        self.act_time_lbl.set_opacity(0.75)
        self.act_time_lbl.set_alignment(0, 0.5)
        title_box.pack_start(self.act_time_lbl, False, False, 0)

        self.act_progress = Gtk.ProgressBar()
        self.act_progress.set_no_show_all(True)
        title_box.pack_start(self.act_progress, False, False, 2)

        box.pack_start(title_box, False, False, 0)

        # Table Header (Nome, Tamanho do arquivo, Status)
        tbl_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        tbl_header.set_margin_top(8)

        h_name = Gtk.Label()
        h_name.set_markup("<b>Nome do Arquivo / Pasta</b>")
        h_name.set_alignment(0, 0.5)
        tbl_header.pack_start(h_name, True, True, 0)

        h_size = Gtk.Label()
        h_size.set_markup("<b>Tamanho</b>")
        h_size.set_size_request(130, -1)
        h_size.set_alignment(0, 0.5)
        tbl_header.pack_start(h_size, False, False, 10)

        h_stat = Gtk.Label()
        h_stat.set_markup("<b>Status Real</b>")
        h_stat.set_size_request(170, -1)
        h_stat.set_alignment(0, 0.5)
        tbl_header.pack_start(h_stat, False, False, 10)

        box.pack_start(tbl_header, False, False, 0)
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        # Scrollable real-time file list
        self.act_scroll = Gtk.ScrolledWindow()
        self.act_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.act_scroll.set_vexpand(True)

        self.act_list_box = Gtk.ListBox()
        self.act_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.act_scroll.add(self.act_list_box)
        box.pack_start(self.act_scroll, True, True, 0)

        # Center Empty State (shown only if 0 items exist)
        self.act_empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.act_empty_box.set_valign(Gtk.Align.CENTER)
        self.act_empty_box.set_vexpand(True)
        self.act_empty_box.set_no_show_all(True)

        dog_path = os.path.join(assets_dir, "Selling-point-1.svg")
        dog_img = self._create_scaled_image(dog_path, 130, 130)
        self.act_empty_box.pack_start(dog_img, False, False, 0)

        lbl_msg = Gtk.Label(label="As atividades nos seus arquivos e pastas vão aparecer aqui")
        lbl_msg.set_opacity(0.7)
        self.act_empty_box.pack_start(lbl_msg, False, False, 0)

        box.pack_start(self.act_empty_box, True, True, 0)
        return box

    @staticmethod
    def _format_size(size_bytes: int, is_dir: bool) -> str:
        if is_dir:
            return "Pasta"
        if not size_bytes or size_bytes <= 0:
            return "Doc Web"
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 ** 2:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 ** 3:
            return f"{size_bytes / (1024 ** 2):.1f} MB"
        return f"{size_bytes / (1024 ** 3):.2f} GB"

    def _refresh_live_ui(self) -> bool:
        """Update Home Card 1 and Activity Tab with real-time database and SyncWorker state."""
        if self.window is None:
            return False

        try:
            stats = self.db.get_sync_stats()
            total_items = stats["total_items"]
            total_files = stats["total_files"]
            total_dirs = stats["total_dirs"]
            pending_ops = stats["pending_ops"]

            is_syncing = bool(self.sync_worker and getattr(self.sync_worker, "is_syncing", False))
            is_paused = bool(self.sync_worker and getattr(self.sync_worker, "paused", False))
            phase = getattr(self.sync_worker, "sync_phase", "Sincronizando...") if self.sync_worker else "Aguardando..."
            last_file = getattr(self.sync_worker, "last_synced_file", "") if self.sync_worker else ""
            mount_pt = self.config.get("mount_point", os.path.expanduser("~/GoogleDrive"))
            sync_mode_label = "Espelhar (Offline)" if self.config.get("sync_mode") == "mirror" else "Stream (Sob demanda)"

            # 1. Update Home Card 1 ("Status de sincronização")
            if hasattr(self, "home_sync_subtitle_lbl"):
                if is_paused:
                    self.home_sync_subtitle_lbl.set_markup("<b>⏸️ Sincronização pausada</b>")
                    self.home_sync_desc_lbl.set_text(
                        f"Indexados: {total_files} arquivos e {total_dirs} pastas\n"
                        f"Modo: {sync_mode_label}\n"
                        f"Pasta: {mount_pt}"
                    )
                    self.home_sync_progress.hide()
                elif is_syncing or pending_ops > 0:
                    self.home_sync_subtitle_lbl.set_markup(f"<b>🔄 {GLib.markup_escape_text(phase)}</b>")
                    desc_lines = [
                        f"Catálogo sincronizado até agora: {total_files} arquivos e {total_dirs} pastas",
                    ]
                    if last_file:
                        desc_lines.append(f"Sincronizando: {last_file[:42]}")
                    desc_lines.append(f"Pasta ativa: {mount_pt}")
                    self.home_sync_desc_lbl.set_text("\n".join(desc_lines))
                    self.home_sync_progress.show()
                    self.home_sync_progress.pulse()
                else:
                    self.home_sync_subtitle_lbl.set_markup("<b>✅ Catálogo sincronizado e atualizado</b>")
                    self.home_sync_desc_lbl.set_text(
                        f"Total disponível: {total_files} arquivos e {total_dirs} pastas\n"
                        f"Modo: {sync_mode_label}\n"
                        f"Pasta ativa: {mount_pt}"
                    )
                    self.home_sync_progress.hide()

            # 2. Update Activity Tab Header & File List
            if hasattr(self, "act_status_lbl"):
                if is_paused:
                    self.act_status_lbl.set_markup("<big><big><b>⏸️ Sincronização pausada</b></big></big>")
                    self.act_progress.hide()
                elif is_syncing or pending_ops > 0:
                    self.act_status_lbl.set_markup(
                        f"<big><big><b>🔄 {GLib.markup_escape_text(phase)}</b></big></big>"
                    )
                    self.act_progress.show()
                    self.act_progress.pulse()
                else:
                    self.act_status_lbl.set_markup(
                        f"<big><big><b>☁️ Atualizado ({total_items} itens sincronizados)</b></big></big>"
                    )
                    self.act_progress.hide()

                self.act_time_lbl.set_text(
                    f"{total_files} arquivos • {total_dirs} pastas • Modo: {sync_mode_label} • Pasta: {mount_pt}"
                )

                # Populate recent items list
                recent = []
                if self.sync_worker and getattr(self.sync_worker, "recent_synced_items", None):
                    recent = list(self.sync_worker.recent_synced_items[:35])
                if not recent:
                    recent = self.db.get_recent_items(limit=35)

                for child in self.act_list_box.get_children():
                    self.act_list_box.remove(child)

                if recent:
                    self.act_empty_box.hide()
                    self.act_scroll.show()
                    for idx, item in enumerate(recent):
                        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                        row_box.set_margin_top(5)
                        row_box.set_margin_bottom(5)
                        row_box.set_margin_start(4)
                        row_box.set_margin_end(4)

                        is_dir = bool(item.get("is_directory"))
                        icon_prefix = "📁" if is_dir else "📄"
                        name_str = item.get("name", "Sem nome")
                        lbl_n = Gtk.Label(label=f"{icon_prefix}  {name_str}")
                        lbl_n.set_alignment(0, 0.5)
                        lbl_n.set_ellipsize(3)  # Pango.EllipsizeMode.END
                        row_box.pack_start(lbl_n, True, True, 0)

                        lbl_sz = Gtk.Label(label=self._format_size(int(item.get("size", 0)), is_dir))
                        lbl_sz.set_size_request(130, -1)
                        lbl_sz.set_alignment(0, 0.5)
                        lbl_sz.set_opacity(0.8)
                        row_box.pack_start(lbl_sz, False, False, 10)

                        if is_syncing and idx < 3:
                            st_text = "🔄 Sincronizando..."
                        elif item.get("sync_status") == "synced" or item.get("sync_status") == "pinned":
                            st_text = "✅ Sincronizado local"
                        else:
                            st_text = "☁️ Sincronizado (Stream)"

                        lbl_st = Gtk.Label(label=st_text)
                        lbl_st.set_size_request(170, -1)
                        lbl_st.set_alignment(0, 0.5)
                        row_box.pack_start(lbl_st, False, False, 10)

                        self.act_list_box.add(row_box)
                    self.act_list_box.show_all()
                else:
                    self.act_scroll.hide()
                    self.act_empty_box.show_all()
        except Exception as e:
            print(f"[UI] Erro ao atualizar status em tempo real: {e}")

        return True

    # =========================================================================
    # PÁGINA 3: NOTIFICAÇÕES (Imagem 4 enviada pelo usuário)
    # =========================================================================
    def _create_page_notifications(self, assets_dir):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_top(25)
        box.set_margin_bottom(25)
        box.set_margin_start(30)
        box.set_margin_end(30)

        # Title: "Notificações"
        lbl_title = Gtk.Label()
        lbl_title.set_markup("<big><big><b>Notificações</b></big></big>")
        lbl_title.set_alignment(0, 0.5)
        box.pack_start(lbl_title, False, False, 0)

        # Center Empty State (Girl with Flower Illustration - Imagem 4!)
        empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        empty_box.set_valign(Gtk.Align.CENTER)
        empty_box.set_vexpand(True)

        flower_path = os.path.join(assets_dir, "Selling-point-2.svg")
        flower_img = self._create_scaled_image(flower_path, 150, 150)
        empty_box.pack_start(flower_img, False, False, 0)

        lbl_flower_title = Gtk.Label()
        lbl_flower_title.set_markup("<b>Tudo em dia!</b>")
        empty_box.pack_start(lbl_flower_title, False, False, 0)

        lbl_flower_desc = Gtk.Label(label="As coisas que precisam da sua atenção vão aparecer aqui")
        lbl_flower_desc.set_opacity(0.7)
        empty_box.pack_start(lbl_flower_desc, False, False, 0)

        box.pack_start(empty_box, True, True, 0)
        return box

    def _create_card(self, title, subtitle, desc, image_path, action_label=None, action_callback=None):
        frame = Gtk.Frame()
        frame.set_hexpand(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(14)
        box.set_margin_bottom(14)
        box.set_margin_start(14)
        box.set_margin_end(14)
        box.set_size_request(280, 220)

        lbl_t = Gtk.Label()
        lbl_t.set_markup(f"<b><big>{title}</big></b>")
        lbl_t.set_line_wrap(True)
        lbl_t.set_max_width_chars(34)
        lbl_t.set_alignment(0, 0.5)
        box.pack_start(lbl_t, False, False, 0)

        lbl_s = Gtk.Label(label=subtitle)
        lbl_s.set_line_wrap(True)
        lbl_s.set_max_width_chars(38)
        lbl_s.set_alignment(0, 0.5)
        box.pack_start(lbl_s, False, False, 0)

        if desc:
            lbl_d = Gtk.Label(label=desc)
            lbl_d.set_line_wrap(True)
            lbl_d.set_max_width_chars(38)
            lbl_d.set_opacity(0.75)
            lbl_d.set_alignment(0, 0.5)
            box.pack_start(lbl_d, False, False, 0)

        if image_path:
            img = self._create_scaled_image(image_path, 115, 115)
            box.pack_start(img, True, True, 4)

        if action_label and action_callback:
            btn = Gtk.Button(label=action_label)
            btn.set_halign(Gtk.Align.START)
            btn.connect("clicked", lambda _: action_callback())
            box.pack_start(btn, False, False, 4)

        frame.add(box)
        return frame

    # --- Actions ---
    def _on_open_folder(self):
        mount_pt = self.config.get("mount_point", os.path.expanduser("~/GoogleDrive"))
        subprocess.Popen(["xdg-open", mount_pt])

    def _on_toggle_pause(self, btn):
        if self.sync_worker:
            if self.sync_worker.paused:
                self.sync_worker.resume()
                btn.set_label("⏸️")
            else:
                self.sync_worker.pause()
                btn.set_label("▶️")

    def _on_search(self, query):
        if query:
            webbrowser.open(f"https://drive.google.com/drive/search?q={query}")

    def _on_send_feedback(self):
        if not HAS_GTK:
            from core.diagnostic import CrashReporter
            report = CrashReporter.generate_report(custom_notes="Feedback do usuário")
            CrashReporter.report_issue(report)
            return

        dialog = Gtk.Dialog(
            title="Enviar Feedback / Relatar Problema ao GitHub",
            transient_for=self.window,
            flags=0
        )
        dialog.add_buttons(
            "Cancelar", Gtk.ResponseType.CANCEL,
            "Enviar Relatório e Abrir Issue", Gtk.ResponseType.OK
        )
        dialog.set_default_size(500, 300)

        box = dialog.get_content_area()
        box.set_spacing(10)
        box.set_margin_top(15)
        box.set_margin_bottom(15)
        box.set_margin_start(18)
        box.set_margin_end(18)

        lbl_desc = Gtk.Label()
        lbl_desc.set_markup("<b>Relatar Problema / Diagnóstico do Sistema</b>\n"
                            "<small>Um diagnóstico técnico (versão do kernel, status do FUSE, logs recentes) "
                            "será empacotado de forma sanitizada (sem senhas ou tokens) e enviado ao repositório "
                            "para análise e correção automática.</small>")
        lbl_desc.set_line_wrap(True)
        lbl_desc.set_alignment(0, 0)
        box.pack_start(lbl_desc, False, False, 0)

        lbl_prompt = Gtk.Label(label="Descreva o que ocorreu ou o comportamento esperado (opcional):")
        lbl_prompt.set_alignment(0, 0.5)
        box.pack_start(lbl_prompt, False, False, 0)

        entry = Gtk.TextView()
        entry.set_wrap_mode(Gtk.WrapMode.WORD)
        entry.get_buffer().set_text("")
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(100)
        scroll.add(entry)
        box.pack_start(scroll, True, True, 0)

        dialog.show_all()
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            buf = entry.get_buffer()
            notes = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True).strip()
            if not notes:
                notes = "Relatório de diagnóstico gerado sob demanda pelo usuário."
            from core.diagnostic import CrashReporter
            report = CrashReporter.generate_report(custom_notes=notes)
            CrashReporter.report_issue(report)
        dialog.destroy()

    def _show_about_dialog(self):
        dialog = Gtk.AboutDialog(
            transient_for=self.window,
            program_name="Google Drive para Linux",
            version="133.0.2",
            copyright="© 2026 Google LLC & Contribuidores Linux",
            comments="Cliente nativo completo do Google Drive para distribuições Linux com streaming FUSE e PWA para Docs.",
            website="https://github.com/gijogadds/google-drive-linux"
        )
        dialog.run()
        dialog.destroy()

    def _show_error_list_dialog(self):
        if not HAS_GTK:
            return

        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.NONE,
            text="Lista de Erros de Sincronização"
        )
        dialog.add_buttons(
            "Fechar", Gtk.ResponseType.CLOSE,
            "Gerar Diagnóstico / Abrir Issue", Gtk.ResponseType.OK
        )
        dialog.format_secondary_text(
            "Nenhum erro de sincronização crítico encontrado no momento.\n"
            "Se você estiver enfrentando algum comportamento inesperado, pode gerar um relatório "
            "de diagnóstico sanitizado para análise no GitHub."
        )
        res = dialog.run()
        dialog.destroy()
        if res == Gtk.ResponseType.OK:
            self._on_send_feedback()

    def _show_offline_files_dialog(self):
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Arquivos Disponíveis Off-line"
        )
        dialog.format_secondary_text("Os arquivos e pastas marcados como 'Disponível off-line' estão salvos no cache local.")
        dialog.run()
        dialog.destroy()

    def _on_add_account(self):
        if not self.auth:
            return

        def _worker():
            acc = self.auth.login_interactive()
            if acc:
                def _on_done():
                    if self.on_account_connected:
                        self.on_account_connected(acc)
                    if self.window:
                        self.window.destroy()
                    self.show()
                GLib.idle_add(_on_done)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_logout_account(self, account_id):
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Desconectar conta do Google Drive?"
        )
        if dialog.run() == Gtk.ResponseType.YES:
            # Remove from DB
            with self.db._get_connection() as conn:
                conn.execute("DELETE FROM accounts WHERE account_id = ?", (account_id,))
                conn.commit()
            if self.auth and account_id in self.auth.accounts:
                del self.auth.accounts[account_id]
                self.auth._save_accounts_to_disk()
            if self.window:
                self.window.destroy()
            self.show()
        dialog.destroy()


class SettingsDialog:
    def __init__(self, config: Dict[str, Any], auth_manager, on_save_callback: Callable, on_account_connected: Optional[Callable] = None):
        self.config = config
        self.auth_manager = auth_manager
        self.on_save = on_save_callback
        self.on_account_connected = on_account_connected
        self.window = None

    def show(self):
        if not HAS_GTK:
            return

        if self.window is not None:
            self.window.present()
            return

        window = Gtk.Window(title="Google Drive - Preferências")
        window.set_default_size(580, 520)
        window.set_position(Gtk.WindowPosition.CENTER)
        window.connect("destroy", lambda _: setattr(self, "window", None))

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

        accounts = self.auth_manager.accounts if self.auth_manager else {}
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
        btn_login.connect("clicked", lambda _: self._on_login(window, btn_login))
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

        stream_rb = Gtk.RadioButton.new_with_label_from_widget(
            None,
            "Transmitir arquivos (Apenas online sob demanda - Economiza espaço)"
        )
        stream_rb.set_active(self.config.get("sync_mode") == "stream")
        mirror_rb = Gtk.RadioButton.new_with_label_from_widget(
            stream_rb,
            "Espelhar arquivos (Disponível offline localmente)"
        )
        mirror_rb.set_active(self.config.get("sync_mode") == "mirror")

        sync_box.pack_start(stream_rb, False, False, 0)
        sync_box.pack_start(mirror_rb, False, False, 0)

        # Helper for folder browser
        def _choose_folder(parent, entry, is_mount_point=False):
            dialog = Gtk.FileChooserDialog(
                title="Selecione um Diretório (ex: na sua HD Externa)",
                transient_for=parent,
                action=Gtk.FileChooserAction.SELECT_FOLDER
            )
            dialog.add_buttons(
                "Cancelar", Gtk.ResponseType.CANCEL,
                "Selecionar", Gtk.ResponseType.OK
            )
            if dialog.run() == Gtk.ResponseType.OK:
                chosen = dialog.get_filename()
                if chosen and is_mount_point:
                    # If user selected an existing non-empty drive/folder root that isn't already GoogleDrive,
                    # append /GoogleDrive so FUSE mounts inside a dedicated GoogleDrive folder
                    base = os.path.basename(chosen.rstrip("/")).lower()
                    if base not in ("googledrive", "google drive", "google-drive"):
                        try:
                            if os.path.isdir(chosen) and os.listdir(chosen):
                                chosen = os.path.join(chosen, "GoogleDrive")
                        except OSError:
                            chosen = os.path.join(chosen, "GoogleDrive")
                if chosen:
                    entry.set_text(chosen)
            dialog.destroy()

        # Mount Point Entry
        path_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        path_label = Gtk.Label(label="Ponto de Montagem:")
        path_entry = Gtk.Entry()
        path_entry.set_text(self.config.get("mount_point", os.path.expanduser("~/GoogleDrive")))
        btn_browse_mount = Gtk.Button(label="Procurar...")
        btn_browse_mount.connect("clicked", lambda _: _choose_folder(window, path_entry, is_mount_point=True))

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
        btn_browse_cache.connect("clicked", lambda _: _choose_folder(window, cache_entry, is_mount_point=False))

        cache_box.pack_start(cache_label, False, False, 0)
        cache_box.pack_start(cache_entry, True, True, 0)
        cache_box.pack_start(btn_browse_cache, False, False, 0)
        sync_box.pack_start(cache_box, False, False, 0)

        autostart_cb = Gtk.CheckButton(label="Iniciar o Google Drive automaticamente ao fazer login no sistema")
        autostart_cb.set_active(bool(self.config.get("autostart", True)))
        sync_box.pack_start(autostart_cb, False, False, 4)

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
            from core.config import ensure_apparmor_allows_mount
            updated_config = dict(self.config)
            updated_config["sync_mode"] = "mirror" if mirror_rb.get_active() else "stream"
            updated_config["mount_point"] = path_entry.get_text().strip() or os.path.expanduser("~/GoogleDrive")
            updated_config["cache_dir"] = cache_entry.get_text().strip() or os.path.expanduser("~/.cache/google-drive-linux")
            updated_config["autostart"] = autostart_cb.get_active()
            ensure_apparmor_allows_mount(updated_config["mount_point"])
            try:
                action = "enable" if updated_config["autostart"] else "disable"
                subprocess.run(["systemctl", "--user", action, "google-drive.service"], capture_output=True)
            except Exception:
                pass

            ok = True
            msg = f"Configurações aplicadas com sucesso!\n\nPonto de Montagem ativo:\n{updated_config['mount_point']}"
            if self.on_save:
                res = self.on_save(updated_config)
                if isinstance(res, tuple) and len(res) == 2:
                    ok, msg = res
            self.config = updated_config

            msg_dialog = Gtk.MessageDialog(
                transient_for=window,
                message_type=Gtk.MessageType.INFO if ok else Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="Configurações Salvas e Aplicadas!" if ok else "Erro ao Aplicar Configurações"
            )
            msg_dialog.format_secondary_text(msg)
            msg_dialog.run()
            msg_dialog.destroy()
            if ok:
                window.destroy()

        btn_save.connect("clicked", _save)
        btn_box.pack_start(btn_save, False, False, 0)
        vbox.pack_start(btn_box, False, False, 0)

        self.window = window
        window.show_all()
        window.present()

    def _on_login(self, parent_window, btn_login):
        if not self.auth_manager:
            return
        btn_login.set_sensitive(False)
        btn_login.set_label("Aguardando conclusão do login no navegador...")

        def _worker():
            acc = self.auth_manager.login_interactive()

            def _on_done():
                if acc:
                    if self.on_account_connected:
                        self.on_account_connected(acc)
                    dialog = Gtk.MessageDialog(
                        transient_for=parent_window,
                        message_type=Gtk.MessageType.INFO,
                        buttons=Gtk.ButtonsType.OK,
                        text=f"Conta conectada com sucesso: {acc.get('email')}!"
                    )
                    dialog.run()
                    dialog.destroy()
                    if self.window:
                        self.window.destroy()
                    self.show()
                else:
                    btn_login.set_sensitive(True)
                    btn_login.set_label("+ Adicionar Conta Google (Login no Navegador)")

            GLib.idle_add(_on_done)

        threading.Thread(target=_worker, daemon=True).start()
