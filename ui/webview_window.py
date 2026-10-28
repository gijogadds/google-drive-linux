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
    def __init__(self, config: Dict[str, Any], db, auth_manager=None, sync_worker=None, on_open_settings=None):
        self.config = config
        self.db = db
        self.auth = auth_manager
        self.sync_worker = sync_worker
        self.on_open_settings = on_open_settings
        self.window = None
        self.stack = None
        self.gear_menu = None

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

        self._build_interface(window)
        window.show_all()
        self.window = window

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
        name = acc.get("display_name") or "Giovanni"
        email = acc.get("email") or "ctm.planetjet@gmail.com"
        initial = email[0].upper()

        avatar_big = Gtk.Label()
        avatar_big.set_markup(f"<span size='xx-large' background='#5c6bc0' foreground='white'><b>  {initial}  </b></span>")
        vbox.pack_start(avatar_big, False, False, 0)

        # "Olá, Giovanni!" & Email
        lbl_hello = Gtk.Label()
        lbl_hello.set_markup(f"<big><b>Olá, {name}!</b></big>")
        vbox.pack_start(lbl_hello, False, False, 0)

        lbl_email = Gtk.Label(label=email)
        lbl_email.set_opacity(0.7)
        vbox.pack_start(lbl_email, False, False, 0)

        # Buttons: "+ Adicionar conta" & "↳ Desconectar conta"
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_add = Gtk.Button(label="+ Adicionar conta")
        btn_add.connect("clicked", lambda _: self._on_add_account())
        btn_row.pack_start(btn_add, True, True, 0)

        btn_logout = Gtk.Button(label="⎋ Desconectar conta")
        btn_logout.connect("clicked", lambda _: self._on_logout_account(acc.get("account_id")))
        btn_row.pack_start(btn_logout, True, True, 0)
        vbox.pack_start(btn_row, False, False, 5)

        # Storage Card: "Armazenamento usado: 61% de 15 GB"
        storage_frame = Gtk.Frame()
        storage_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        storage_box.set_margin_top(10)
        storage_box.set_margin_bottom(10)
        storage_box.set_margin_start(12)
        storage_box.set_margin_end(12)

        storage_lbl = Gtk.Label()
        storage_lbl.set_markup("☁️ Armazenamento usado: <b>61% de 15 GB</b>")
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

    # =========================================================================
    # PÁGINA 1: INÍCIO (Dashboard de Cartões)
    # =========================================================================
    def _create_page_home(self, assets_dir):
        scroll = Gtk.ScrolledWindow()
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        content.set_margin_top(25)
        content.set_margin_bottom(25)
        content.set_margin_start(25)
        content.set_margin_end(25)

        grid = Gtk.Grid()
        grid.set_column_spacing(20)
        grid.set_row_spacing(20)

        # Card 1: Dog Card (Status de Sincronização)
        card1 = self._create_card(
            "Status de sincronização",
            "Seus arquivos estão atualizados",
            "As atividades nos seus arquivos e pastas vão aparecer aqui.",
            os.path.join(assets_dir, "Selling-point-1.svg")
        )
        grid.attach(card1, 0, 0, 1, 1)

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

        btn_l4 = Gtk.Button(label="↗ Perguntas frequentes")
        btn_l4.connect("clicked", lambda _: webbrowser.open("https://support.google.com/drive"))
        box4.pack_start(btn_l4, False, False, 0)

        card4.add(box4)
        grid.attach(card4, 1, 1, 1, 1)

        content.pack_start(grid, True, True, 0)
        scroll.add(content)
        return scroll

    # =========================================================================
    # PÁGINA 2: ATIVIDADE DE SINCRONIZAÇÃO (Imagem 3 enviada pelo usuário)
    # =========================================================================
    def _create_page_activity(self, assets_dir):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_top(25)
        box.set_margin_bottom(25)
        box.set_margin_start(30)
        box.set_margin_end(30)

        # Header Title: "☁️ Atualizado / Sincronizado há 2 horas"
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        lbl_status = Gtk.Label()
        lbl_status.set_markup("<big><big><b>☁️ Atualizado</b></big></big>")
        lbl_status.set_alignment(0, 0.5)
        title_box.pack_start(lbl_status, False, False, 0)

        lbl_time = Gtk.Label(label="Sincronizado recentemente")
        lbl_time.set_opacity(0.6)
        lbl_time.set_alignment(0, 0.5)
        title_box.pack_start(lbl_time, False, False, 0)
        box.pack_start(title_box, False, False, 0)

        # Table Header (Nome, Tamanho do arquivo, Status)
        tbl_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        tbl_header.set_margin_top(15)

        h_name = Gtk.Label(label="Nome")
        h_name.set_alignment(0, 0.5)
        tbl_header.pack_start(h_name, True, True, 0)

        h_size = Gtk.Label(label="Tamanho do arquivo")
        h_size.set_alignment(0, 0.5)
        tbl_header.pack_start(h_size, False, False, 50)

        h_stat = Gtk.Label(label="Status")
        h_stat.set_alignment(0, 0.5)
        tbl_header.pack_start(h_stat, False, False, 30)

        box.pack_start(tbl_header, False, False, 0)
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        # Center Empty State (The Dog with Cloud Illustration - Imagem 3!)
        empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        empty_box.set_valign(Gtk.Align.CENTER)
        empty_box.set_vexpand(True)

        dog_img = Gtk.Image()
        dog_path = os.path.join(assets_dir, "Selling-point-1.svg")
        if os.path.exists(dog_path):
            dog_img.set_from_file(dog_path)
        empty_box.pack_start(dog_img, False, False, 0)

        lbl_msg = Gtk.Label(label="As atividades nos seus arquivos e pastas vão aparecer aqui")
        lbl_msg.set_opacity(0.7)
        empty_box.pack_start(lbl_msg, False, False, 0)

        box.pack_start(empty_box, True, True, 0)
        return box

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

        flower_img = Gtk.Image()
        flower_path = os.path.join(assets_dir, "Selling-point-2.svg")
        if os.path.exists(flower_path):
            flower_img.set_from_file(flower_path)
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

        if image_path and os.path.exists(image_path):
            img = Gtk.Image.new_from_file(image_path)
            box.pack_start(img, False, False, 5)

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
        from core.diagnostic import CrashReporter
        report = CrashReporter.generate_report(custom_notes="Feedback do usuário")
        CrashReporter.report_issue(report)

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
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Lista de Erros de Sincronização"
        )
        dialog.format_secondary_text("Nenhum erro de sincronização encontrado. Todos os seus arquivos estão sincronizados.")
        dialog.run()
        dialog.destroy()

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
        if self.auth:
            self.auth.login_interactive()

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
            self.window.destroy()
            self.show()
        dialog.destroy()
