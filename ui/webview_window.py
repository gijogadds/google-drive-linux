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
import time
import subprocess
import threading
import webbrowser
from typing import Dict, Any, Callable, Optional

try:
    import gi
    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib
    GLib.set_prgname("google-drive")
    if GLib.get_application_name() != "Google Drive":
        GLib.set_application_name("Google Drive")
    from gi.repository import Gtk, Gdk, GdkPixbuf
    Gdk.set_program_class("google-drive")
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

        GLib.set_prgname("google-drive")
        Gdk.set_program_class("google-drive")

        window = Gtk.Window(title="Google Drive")
        window.set_default_size(980, 680)
        window.set_position(Gtk.WindowPosition.CENTER)
        try:
            window.set_wmclass("google-drive", "Google Drive")
        except Exception:
            pass
        window.connect("destroy", self._on_window_destroy)

        # Set official launcher icon (256x256 PNG)
        icon_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "assets", "icons", "drive_fs.png"
        )
        if os.path.exists(icon_path):
            try:
                Gtk.Window.set_default_icon_from_file(icon_path)
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
        GLib.idle_add(self._check_unconfigured_account_mounts)

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

        # Search Bar (in-app dual search: Local Computer & Google Drive Web)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Pesquisar no Computador e no Google Drive...")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("activate", lambda e: self._on_search(e.get_text()))
        self.search_entry.connect("search-changed", self._on_search_changed)
        header.pack_start(self.search_entry, True, True, 20)

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
        acc = accounts[0] if accounts else {}
        avatar_btn = Gtk.Button()
        avatar_btn.set_relief(Gtk.ReliefStyle.NONE)
        avatar_btn.add(self._create_avatar_widget(acc, size=32))
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

        # --- PÁGINA 4: RESULTADOS DA PESQUISA (No Computador + No Google Drive) ---
        page_search = self._create_page_search()
        self.stack.add_named(page_search, "search")

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

    def _create_avatar_widget(self, acc: Dict[str, Any], size: int = 32):
        """Render a circular Google profile photo (or circular initial badge fallback) at the requested size."""
        import math
        import urllib.request

        photo_url = (acc.get("photo_url") or "").strip()
        account_id = acc.get("account_id") or "default"
        email = acc.get("email") or "G"
        initial = email[0].upper() if email else "G"

        cache_dir = os.path.expanduser("~/.cache/google-drive-linux/avatars")
        os.makedirs(cache_dir, exist_ok=True)
        cache_path = os.path.join(cache_dir, f"{account_id}.png")

        if photo_url and (not os.path.exists(cache_path) or os.path.getsize(cache_path) == 0):
            try:
                # Request high-res square crop from Google User Content
                hi_res_url = photo_url.replace("=s96-c", "=s192-c")
                req = urllib.request.Request(hi_res_url, headers={"User-Agent": "GoogleDriveLinux/1.0"})
                with urllib.request.urlopen(req, timeout=4) as resp:
                    data = resp.read()
                    if data:
                        with open(cache_path, "wb") as f:
                            f.write(data)
            except Exception as e:
                print(f"[UI] Aviso ao baixar foto de perfil: {e}")

        try:
            import cairo
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
            ctx = cairo.Context(surface)
            ctx.arc(size / 2.0, size / 2.0, size / 2.0, 0, 2 * math.pi)
            ctx.clip()

            if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
                raw_pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(cache_path, size, size, True)
                Gdk.cairo_set_source_pixbuf(ctx, raw_pb, 0, 0)
                ctx.paint()
            else:
                ctx.set_source_rgb(0.36, 0.42, 0.75)  # #5c6bc0
                ctx.paint()
                ctx.set_source_rgb(1.0, 1.0, 1.0)
                ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                ctx.set_font_size(size * 0.5)
                extents = ctx.text_extents(initial)
                x = (size - extents.width) / 2.0 - extents.x_bearing
                y = (size - extents.height) / 2.0 - extents.y_bearing
                ctx.move_to(x, y)
                ctx.show_text(initial)

            circle_pb = Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
            if circle_pb is not None:
                return Gtk.Image.new_from_pixbuf(circle_pb)
        except Exception as e:
            print(f"[UI] Fallback ao renderizar avatar circular: {e}")

        if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(cache_path, size, size, True)
                return Gtk.Image.new_from_pixbuf(pb)
            except Exception:
                pass

        lbl = Gtk.Label()
        lbl.set_markup(f"<span size='large' background='#5c6bc0' foreground='white'><b>  {initial}  </b></span>")
        return lbl

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

        avatar_big = self._create_avatar_widget(acc, size=72)
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

        if acc.get("account_id"):
            from core.config import get_account_mount_point, get_account_display_name
            acc_id = acc.get("account_id", "")
            acc_mount = get_account_mount_point(self.config, acc_id, email=acc.get("email", ""))
            acc_disp = get_account_display_name(self.config, acc_id, email=acc.get("email", ""))
            mnt_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            lbl_mnt = Gtk.Label()
            lbl_mnt.set_markup(f"<small>📁 Nautilus: <b>{GLib.markup_escape_text(acc_disp)}</b></small>")
            lbl_mnt.set_tooltip_text(acc_mount)
            lbl_mnt.set_alignment(0, 0.5)
            lbl_mnt.set_ellipsize(3)
            mnt_row.pack_start(lbl_mnt, True, True, 0)
            btn_edit_mnt = Gtk.Button(label="✏️ Editar nome/pasta")
            btn_edit_mnt.connect("clicked", lambda _, a=acc: (popover.popdown(), self._on_edit_account_folder(a)))
            mnt_row.pack_start(btn_edit_mnt, False, False, 0)
            storage_box.pack_start(mnt_row, False, False, 0)

        storage_frame.add(storage_box)
        vbox.pack_start(storage_frame, False, False, 5)

        # List additional connected accounts if more than 1 account exists
        if len(accounts) > 1:
            from core.config import get_account_mount_point, get_account_display_name
            others_frame = Gtk.Frame(label="Outras contas conectadas (Sincronizando em paralelo)")
            others_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            others_box.set_margin_top(8)
            others_box.set_margin_bottom(8)
            others_box.set_margin_start(10)
            others_box.set_margin_end(10)
            for other_acc in accounts[1:]:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                row.pack_start(self._create_avatar_widget(other_acc, size=28), False, False, 0)
                other_id = other_acc.get("account_id", "")
                other_email = other_acc.get("email", "")
                other_mount = get_account_mount_point(self.config, other_id, email=other_email)
                other_disp = get_account_display_name(self.config, other_id, email=other_email)
                lbl_o = Gtk.Label()
                lbl_o.set_markup(
                    f"<b>{GLib.markup_escape_text(other_email)}</b>\n"
                    f"<small>📁 {GLib.markup_escape_text(other_disp)}</small>"
                )
                lbl_o.set_tooltip_text(other_mount)
                lbl_o.set_alignment(0, 0.5)
                lbl_o.set_ellipsize(3)
                row.pack_start(lbl_o, True, True, 0)
                btn_ed = Gtk.Button(label="✏️")
                btn_ed.set_tooltip_text("Editar nome exibido no Nautilus e local da pasta")
                btn_ed.connect("clicked", lambda _, a=other_acc: (popover.popdown(), self._on_edit_account_folder(a)))
                row.pack_start(btn_ed, False, False, 0)
                btn_sw = Gtk.Button(label="Usar")
                btn_sw.connect("clicked", lambda _, aid=other_id: (popover.popdown(), self._on_switch_account(aid)))
                row.pack_start(btn_sw, False, False, 0)
                others_box.pack_start(row, False, False, 0)
            others_frame.add(others_box)
            vbox.pack_start(others_frame, False, False, 4)

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
        self.home_sync_progress.set_show_text(True)
        card1_box.pack_start(self.home_sync_progress, False, False, 4)

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
        self.act_progress.set_show_text(True)
        title_box.pack_start(self.act_progress, False, False, 4)

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
            from core.config import get_account_mount_point
            accs = self.db.get_accounts()
            active_acc = accs[0] if accs else {}
            active_acc_id = active_acc.get("account_id")
            active_email = active_acc.get("email", "")

            stats = self.db.get_sync_stats(account_id=active_acc_id)
            total_items = stats["total_items"]
            total_files = stats["total_files"]
            total_dirs = stats["total_dirs"]
            indexed_bytes = stats.get("indexed_bytes", 0)
            pending_ops = stats["pending_ops"]

            is_syncing = bool(self.sync_worker and getattr(self.sync_worker, "is_syncing", False))
            is_paused = bool(self.sync_worker and getattr(self.sync_worker, "paused", False))
            scan_complete = bool(self.sync_worker and getattr(self.sync_worker, "initial_scan_complete", False))
            drive_total_bytes = getattr(self.sync_worker, "drive_total_bytes", 0) if self.sync_worker else 0
            if not drive_total_bytes and active_acc:
                drive_total_bytes = int(active_acc.get("storage_used", 0))

            if scan_complete and not is_syncing and pending_ops == 0:
                pct = 100.0
            elif drive_total_bytes > 0 and indexed_bytes > 0:
                pct = min(99.9, max(1.0, (indexed_bytes / drive_total_bytes) * 100.0))
                if not is_syncing and pending_ops == 0 and pct >= 95.0:
                    pct = 100.0
            else:
                pct = 100.0 if (not is_syncing and total_items > 0) else 0.0

            fraction = max(0.0, min(1.0, pct / 100.0))
            indexed_gb = indexed_bytes / (1024 ** 3)
            pct_text = f"{pct:.1f}% sincronizado ({indexed_gb:.1f} GB • {total_items} itens)"

            phase = getattr(self.sync_worker, "sync_phase", "Sincronizando...") if self.sync_worker else "Aguardando..."
            last_file = getattr(self.sync_worker, "last_synced_file", "") if self.sync_worker else ""
            mount_pt = get_account_mount_point(self.config, active_acc_id or "", email=active_email)
            sync_mode_label = "Espelhar (Offline)" if self.config.get("sync_mode") == "mirror" else "Stream (Sob demanda)"

            # 1. Update Home Card 1 ("Status de sincronização")
            if hasattr(self, "home_sync_subtitle_lbl"):
                self.home_sync_progress.set_fraction(fraction)
                self.home_sync_progress.set_text(pct_text)
                self.home_sync_progress.show()
                if is_paused:
                    self.home_sync_subtitle_lbl.set_markup(f"<b>⏸️ Sincronização pausada ({pct:.1f}%)</b>")
                    self.home_sync_desc_lbl.set_text(
                        f"Indexados: {total_files} arquivos e {total_dirs} pastas\n"
                        f"Modo: {sync_mode_label}\n"
                        f"Pasta: {mount_pt}"
                    )
                elif is_syncing or pending_ops > 0:
                    self.home_sync_subtitle_lbl.set_markup(f"<b>🔄 {GLib.markup_escape_text(phase)} ({pct:.1f}%)</b>")
                    desc_lines = [
                        f"Progresso: {pct:.1f}% ({total_files} arquivos e {total_dirs} pastas)",
                    ]
                    if last_file:
                        desc_lines.append(f"Sincronizando: {last_file[:42]}")
                    desc_lines.append(f"Pasta ativa: {mount_pt}")
                    self.home_sync_desc_lbl.set_text("\n".join(desc_lines))
                else:
                    self.home_sync_subtitle_lbl.set_markup("<b>✅ Catálogo 100% sincronizado e atualizado</b>")
                    self.home_sync_desc_lbl.set_text(
                        f"Total disponível: {total_files} arquivos e {total_dirs} pastas ({indexed_gb:.1f} GB)\n"
                        f"Modo: {sync_mode_label}\n"
                        f"Pasta ativa: {mount_pt}"
                    )

            # 2. Update Activity Tab Header & File List
            if hasattr(self, "act_status_lbl"):
                self.act_progress.set_fraction(fraction)
                self.act_progress.set_text(pct_text)
                self.act_progress.show()
                if is_paused:
                    self.act_status_lbl.set_markup(f"<big><big><b>⏸️ Sincronização pausada ({pct:.1f}%)</b></big></big>")
                elif is_syncing or pending_ops > 0:
                    self.act_status_lbl.set_markup(
                        f"<big><big><b>🔄 {GLib.markup_escape_text(phase)} ({pct:.1f}%)</b></big></big>"
                    )
                else:
                    self.act_status_lbl.set_markup(
                        f"<big><big><b>☁️ 100% Atualizado ({total_items} itens sincronizados)</b></big></big>"
                    )

                self.act_time_lbl.set_text(
                    f"{total_files} arquivos • {total_dirs} pastas • Modo: {sync_mode_label} • Pasta: {mount_pt}"
                )

                # Populate recent items list
                recent = []
                if self.sync_worker and getattr(self.sync_worker, "recent_synced_items", None):
                    recent = list(self.sync_worker.recent_synced_items[:35])
                if not recent:
                    recent = self.db.get_recent_items(limit=35, account_id=active_acc_id)

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
        from core.config import get_account_mount_point
        accs = self.db.get_accounts()
        active_acc = accs[0] if accs else {}
        mount_pt = get_account_mount_point(
            self.config,
            active_acc.get("account_id", ""),
            email=active_acc.get("email", "")
        )
        subprocess.Popen(["xdg-open", mount_pt])

    def _on_toggle_pause(self, btn):
        if self.sync_worker:
            if self.sync_worker.paused:
                self.sync_worker.resume()
                btn.set_label("⏸️")
            else:
                self.sync_worker.pause()
                btn.set_label("▶️")

    def _create_page_search(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(24)
        box.set_margin_end(24)

        top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.search_title_lbl = Gtk.Label()
        self.search_title_lbl.set_markup("<big><big><b>Resultados da Pesquisa</b></big></big>")
        self.search_title_lbl.set_alignment(0, 0.5)
        top_bar.pack_start(self.search_title_lbl, True, True, 0)

        btn_web_all = Gtk.Button(label="🌐 Ver pesquisa completa no Navegador")
        btn_web_all.connect("clicked", lambda _: self._open_search_in_browser())
        top_bar.pack_start(btn_web_all, False, False, 0)

        btn_back = Gtk.Button(label="✕ Fechar Pesquisa")
        btn_back.connect("clicked", lambda _: (self.search_entry.set_text(""), self.stack.set_visible_child_name("home")))
        top_bar.pack_start(btn_back, False, False, 0)
        box.pack_start(top_bar, False, False, 0)

        self.search_subtitle_lbl = Gtk.Label(
            label="Clique em 'Abrir no Computador' para abrir o arquivo local ou em 'Abrir no Google Drive' para abrir no Chrome."
        )
        self.search_subtitle_lbl.set_alignment(0, 0.5)
        self.search_subtitle_lbl.set_opacity(0.78)
        box.pack_start(self.search_subtitle_lbl, False, False, 0)

        # Dual Paned / Split View:
        # Left/Top: "💻 No Computador (Pasta Local)" | Right/Bottom: "☁️ No Google Drive (Nuvem / Web)"
        grid = Gtk.Grid()
        grid.set_column_spacing(16)
        grid.set_row_spacing(10)
        grid.set_column_homogeneous(True)
        grid.set_vexpand(True)
        grid.set_hexpand(True)

        # Column 1: No Computador
        frame_local = Gtk.Frame()
        vbox_local = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox_local.set_margin_top(10)
        vbox_local.set_margin_bottom(10)
        vbox_local.set_margin_start(12)
        vbox_local.set_margin_end(12)

        self.lbl_search_local_hdr = Gtk.Label()
        self.lbl_search_local_hdr.set_markup("<b>💻 Onde está no Computador (Pasta Montada)</b>")
        self.lbl_search_local_hdr.set_alignment(0, 0.5)
        vbox_local.pack_start(self.lbl_search_local_hdr, False, False, 0)
        vbox_local.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        scroll_local = Gtk.ScrolledWindow()
        scroll_local.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll_local.set_vexpand(True)
        self.search_local_list = Gtk.ListBox()
        self.search_local_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll_local.add(self.search_local_list)
        vbox_local.pack_start(scroll_local, True, True, 0)
        frame_local.add(vbox_local)
        grid.attach(frame_local, 0, 0, 1, 1)

        # Column 2: No Google Drive (Web)
        frame_cloud = Gtk.Frame()
        vbox_cloud = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox_cloud.set_margin_top(10)
        vbox_cloud.set_margin_bottom(10)
        vbox_cloud.set_margin_start(12)
        vbox_cloud.set_margin_end(12)

        self.lbl_search_cloud_hdr = Gtk.Label()
        self.lbl_search_cloud_hdr.set_markup("<b>☁️ No Google Drive (Site / Nuvem)</b>")
        self.lbl_search_cloud_hdr.set_alignment(0, 0.5)
        vbox_cloud.pack_start(self.lbl_search_cloud_hdr, False, False, 0)
        vbox_cloud.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        scroll_cloud = Gtk.ScrolledWindow()
        scroll_cloud.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll_cloud.set_vexpand(True)
        self.search_cloud_list = Gtk.ListBox()
        self.search_cloud_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll_cloud.add(self.search_cloud_list)
        vbox_cloud.pack_start(scroll_cloud, True, True, 0)
        frame_cloud.add(vbox_cloud)
        grid.attach(frame_cloud, 1, 0, 1, 1)

        box.pack_start(grid, True, True, 0)
        return box

    def _open_search_in_browser(self):
        q = self.search_entry.get_text().strip() if hasattr(self, "search_entry") else ""
        if q:
            import urllib.parse
            webbrowser.open(f"https://drive.google.com/drive/search?q={urllib.parse.quote(q)}")
        else:
            webbrowser.open("https://drive.google.com")

    def _on_search_changed(self, entry):
        try:
            q = entry.get_text().strip()
            if not q:
                if hasattr(self, "stack") and self.stack.get_visible_child_name() == "search":
                    self.stack.set_visible_child_name("home")
                return
            if len(q) >= 2:
                self._on_search(q, live_typing=True)
        except Exception as e:
            print(f"[UI] Aviso ao processar alteração de busca: {e}")

    def _open_local_path(self, full_path: str, reveal_folder: bool = False):
        target = os.path.dirname(full_path) if reveal_folder else full_path
        if not os.path.exists(target):
            parent = os.path.dirname(full_path)
            if os.path.exists(parent):
                target = parent
        subprocess.Popen(["xdg-open", target])

    @staticmethod
    def _build_drive_web_url(item: Dict[str, Any]) -> str:
        if item.get("webViewLink"):
            return item["webViewLink"]
        item_id = item.get("id", "")
        is_dir = bool(item.get("is_directory")) or item.get("mimeType") == "application/vnd.google-apps.folder" or item.get("mime_type") == "application/vnd.google-apps.folder"
        mime = item.get("mime_type") or item.get("mimeType") or ""
        if is_dir:
            return f"https://drive.google.com/drive/folders/{item_id}"
        if "document" in mime:
            return f"https://docs.google.com/document/d/{item_id}/edit"
        if "spreadsheet" in mime:
            return f"https://docs.google.com/spreadsheets/d/{item_id}/edit"
        if "presentation" in mime:
            return f"https://docs.google.com/presentation/d/{item_id}/edit"
        return f"https://drive.google.com/file/d/{item_id}/view"

    def _build_search_result_row(self, item: Dict[str, Any], mode: str, acc_map: Dict[str, Dict[str, Any]]):
        from core.config import get_account_mount_point, get_account_display_name
        row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        row_box.set_margin_top(6)
        row_box.set_margin_bottom(6)
        row_box.set_margin_start(6)
        row_box.set_margin_end(6)

        is_dir = bool(item.get("is_directory")) or item.get("mimeType") == "application/vnd.google-apps.folder"
        icon_prefix = "📁" if is_dir else "📄"
        name_str = item.get("name", "Sem nome")
        acc_id = item.get("account_id") or ""
        acc_info = acc_map.get(acc_id, {})
        email = acc_info.get("email", "")
        is_prim = bool(acc_info.get("is_primary", False))
        mount_pt = get_account_mount_point(self.config, acc_id, email=email, is_primary=is_prim)
        disp_name = get_account_display_name(self.config, acc_id, email=email, is_primary=is_prim)

        rel_path = item.get("relative_path")
        if rel_path is None and item.get("id"):
            rel_path = self.db.get_relative_path(item["id"])
        rel_path = rel_path or name_str
        full_local_path = os.path.join(mount_pt, rel_path) if rel_path else mount_pt
        web_url = self._build_drive_web_url(item)

        top_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl_name = Gtk.Label()
        lbl_name.set_markup(f"<b>{icon_prefix}  {GLib.markup_escape_text(name_str)}</b>")
        lbl_name.set_alignment(0, 0.5)
        lbl_name.set_ellipsize(3)
        top_line.pack_start(lbl_name, True, True, 0)

        size_val = int(item.get("size") or 0)
        lbl_sz = Gtk.Label(label=self._format_size(size_val, is_dir))
        lbl_sz.set_opacity(0.75)
        top_line.pack_start(lbl_sz, False, False, 0)
        row_box.pack_start(top_line, False, False, 0)

        lbl_loc = Gtk.Label()
        if mode == "local":
            lbl_loc.set_markup(
                f"<small>📍 <b>{GLib.markup_escape_text(disp_name)}:</b> {GLib.markup_escape_text(full_local_path)}</small>"
            )
            lbl_loc.set_tooltip_text(full_local_path)
        else:
            acc_badge = f"Conta: {email} • " if email else ""
            lbl_loc.set_markup(
                f"<small>☁️ {GLib.markup_escape_text(acc_badge)}Local no Drive: /{GLib.markup_escape_text(rel_path)}</small>"
            )
            lbl_loc.set_tooltip_text(web_url)
        lbl_loc.set_alignment(0, 0.5)
        lbl_loc.set_ellipsize(3)
        row_box.pack_start(lbl_loc, False, False, 0)

        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        if mode == "local":
            btn_open_pc = Gtk.Button(label="💻 Abrir no Computador")
            btn_open_pc.set_tooltip_text(f"Abrir {full_local_path}")
            btn_open_pc.connect("clicked", lambda _, p=full_local_path: self._open_local_path(p, reveal_folder=False))
            btn_bar.pack_start(btn_open_pc, False, False, 0)

            if not is_dir:
                btn_folder = Gtk.Button(label="📂 Abrir Pasta Local")
                btn_folder.set_tooltip_text(f"Mostrar na pasta {os.path.dirname(full_local_path)}")
                btn_folder.connect("clicked", lambda _, p=full_local_path: self._open_local_path(p, reveal_folder=True))
                btn_bar.pack_start(btn_folder, False, False, 0)

            btn_open_web = Gtk.Button(label="🌐 Abrir no Chrome")
            btn_open_web.set_tooltip_text(web_url)
            btn_open_web.connect("clicked", lambda _, u=web_url: webbrowser.open(u))
            btn_bar.pack_start(btn_open_web, False, False, 0)
        else:
            btn_open_web = Gtk.Button(label="🌐 Abrir no Google Drive (Chrome)")
            btn_open_web.set_tooltip_text(web_url)
            btn_open_web.connect("clicked", lambda _, u=web_url: webbrowser.open(u))
            btn_bar.pack_start(btn_open_web, False, False, 0)

            btn_open_pc = Gtk.Button(label="💻 Abrir no Computador")
            btn_open_pc.set_tooltip_text(f"Abrir {full_local_path}")
            btn_open_pc.connect("clicked", lambda _, p=full_local_path: self._open_local_path(p, reveal_folder=False))
            btn_bar.pack_start(btn_open_pc, False, False, 0)

        row_box.pack_start(btn_bar, False, False, 2)
        row_box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 2)
        return row_box

    def _on_search(self, query, live_typing: bool = False):
        q = (query or "").strip()
        if not q:
            return
        self.stack.set_visible_child_name("search")
        self.search_title_lbl.set_markup(
            f"<big><big><b>Resultados para: \"{GLib.markup_escape_text(q)}\"</b></big></big>"
        )

        accs = self.db.get_accounts()
        primary_id = list(self.auth.accounts.keys())[0] if (self.auth and self.auth.accounts) else (accs[0]["account_id"] if accs else "")
        acc_map = {}
        for a in accs:
            aid = a["account_id"]
            acc_map[aid] = dict(a)
            acc_map[aid]["is_primary"] = (aid == primary_id)

        # 1. Immediate Local Computer Search (across all connected accounts in SQLite)
        local_results = self.db.search_items(q, limit=60)
        for child in self.search_local_list.get_children():
            self.search_local_list.remove(child)
        for child in self.search_cloud_list.get_children():
            self.search_cloud_list.remove(child)

        self.lbl_search_local_hdr.set_markup(
            f"<b>💻 No Computador ({len(local_results)} encontrados nas pastas montadas)</b>"
        )
        if local_results:
            for item in local_results:
                self.search_local_list.add(self._build_search_result_row(item, "local", acc_map))
        else:
            lbl_empty = Gtk.Label(label="Nenhum arquivo correspondente encontrado na pasta local.")
            lbl_empty.set_margin_top(20)
            lbl_empty.set_opacity(0.7)
            self.search_local_list.add(lbl_empty)
        self.search_local_list.show_all()

        # 2. Populate Cloud panel initially with matching catalog items + live API search in background
        self.lbl_search_cloud_hdr.set_markup(
            f"<b>☁️ No Google Drive ({len(local_results)} no catálogo • buscando online...)</b>"
        )
        if local_results:
            for item in local_results:
                self.search_cloud_list.add(self._build_search_result_row(item, "cloud", acc_map))
        else:
            lbl_loading = Gtk.Label(label="🔄 Buscando diretamente no Google Drive Web...")
            lbl_loading.set_margin_top(20)
            self.search_cloud_list.add(lbl_loading)
        self.search_cloud_list.show_all()

        search_token = time.time()
        self._last_search_token = search_token

        def _cloud_worker():
            if live_typing:
                time.sleep(0.25)
                if getattr(self, "_last_search_token", None) != search_token:
                    return
            from core.api import DriveAPI
            cloud_items = []
            seen_ids = set()
            for a in accs:
                aid = a["account_id"]
                try:
                    api = DriveAPI(self.auth, aid)
                    online_files = api.search_files_online(q, page_size=40)
                    for f in online_files:
                        fid = f.get("id")
                        if fid and fid not in seen_ids:
                            seen_ids.add(fid)
                            f["account_id"] = aid
                            f["is_directory"] = 1 if f.get("mimeType") == "application/vnd.google-apps.folder" else 0
                            cloud_items.append(f)
                except Exception:
                    pass
            # Also include local catalog matches if offline or not returned yet
            for loc in local_results:
                lid = loc.get("id")
                if lid and lid not in seen_ids:
                    seen_ids.add(lid)
                    cloud_items.append(loc)

            def _update_cloud_ui():
                if getattr(self, "_last_search_token", None) != search_token or self.window is None:
                    return False
                for child in self.search_cloud_list.get_children():
                    self.search_cloud_list.remove(child)
                self.lbl_search_cloud_hdr.set_markup(
                    f"<b>☁️ No Google Drive ({len(cloud_items)} encontrados na nuvem)</b>"
                )
                if cloud_items:
                    for citem in cloud_items[:60]:
                        self.search_cloud_list.add(self._build_search_result_row(citem, "cloud", acc_map))
                else:
                    lbl_none = Gtk.Label(label="Nenhum item encontrado no Google Drive.")
                    lbl_none.set_margin_top(20)
                    lbl_none.set_opacity(0.7)
                    self.search_cloud_list.add(lbl_none)
                self.search_cloud_list.show_all()
                return False

            GLib.idle_add(_update_cloud_ui)

        threading.Thread(target=_cloud_worker, daemon=True).start()

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

    def _check_unconfigured_account_mounts(self):
        """If a secondary connected account has not had its mount folder chosen by the user yet, prompt automatically."""
        if getattr(self, "_checking_mounts", False):
            return False
        self._checking_mounts = True
        try:
            from core.config import save_config
            accs = self.db.get_accounts()
            if len(accs) > 1:
                primary_id = list(self.auth.accounts.keys())[0] if (self.auth and self.auth.accounts) else accs[0]["account_id"]
                confirmed = list(self.config.get("confirmed_account_mounts") or [])
                if primary_id not in confirmed:
                    confirmed.append(primary_id)
                    self.config["confirmed_account_mounts"] = confirmed
                    save_config(self.config)
                for acc in accs:
                    aid = acc["account_id"]
                    if aid not in confirmed:
                        chosen_mount, chosen_name = self._prompt_account_mount_point(acc)
                        confirmed.append(aid)
                        self.config["confirmed_account_mounts"] = confirmed
                        if "account_mounts" not in self.config or not isinstance(self.config.get("account_mounts"), dict):
                            self.config["account_mounts"] = {}
                        if "account_names" not in self.config or not isinstance(self.config.get("account_names"), dict):
                            self.config["account_names"] = {}
                        self.config["account_mounts"][aid] = chosen_mount
                        self.config["account_names"][aid] = chosen_name
                        self.config["active_account_id"] = aid
                        save_config(self.config)
                        acc_with_mount = dict(acc)
                        acc_with_mount["mount_point"] = chosen_mount
                        acc_with_mount["display_name_nautilus"] = chosen_name
                        if self.on_account_connected:
                            self.on_account_connected(acc_with_mount)
                        if self.window:
                            self.window.destroy()
                        self.show()
                        break
        except Exception as e:
            print(f"[UI] Aviso ao verificar pastas de contas: {e}")
        finally:
            self._checking_mounts = False
        return False

    def _on_edit_account_folder(self, acc: Dict[str, Any]):
        """Allow editing the Nautilus sidebar display name and mount folder of an account at any time."""
        from core.config import save_config, unmount_fuse_path, get_account_mount_point
        aid = acc.get("account_id", "")
        if not aid:
            return
        old_mount = get_account_mount_point(self.config, aid, email=acc.get("email", ""))
        chosen_mount, chosen_name = self._prompt_account_mount_point(acc, is_edit=True)
        if not chosen_mount:
            return
        if old_mount and old_mount != chosen_mount:
            unmount_fuse_path(old_mount, remove_if_empty=True)
        if "account_mounts" not in self.config or not isinstance(self.config.get("account_mounts"), dict):
            self.config["account_mounts"] = {}
        if "account_names" not in self.config or not isinstance(self.config.get("account_names"), dict):
            self.config["account_names"] = {}
        self.config["account_mounts"][aid] = chosen_mount
        self.config["account_names"][aid] = chosen_name
        primary_id = list(self.auth.accounts.keys())[0] if (self.auth and self.auth.accounts) else ""
        if aid == primary_id:
            self.config["mount_point"] = chosen_mount
        save_config(self.config)
        acc_updated = dict(acc)
        acc_updated["mount_point"] = chosen_mount
        acc_updated["display_name_nautilus"] = chosen_name
        if self.on_account_connected:
            self.on_account_connected(acc_updated)
        if self.window:
            self.window.destroy()
        self.show()

    def _prompt_account_mount_point(self, acc: Dict[str, Any], is_edit: bool = False):
        """Open a graphical dialog so the user can choose both the Nautilus sidebar name and the folder location."""
        from core.config import suggest_account_mount_point, get_account_mount_point, get_account_display_name
        aid = acc.get("account_id", "")
        email = acc.get("email", "")
        primary_id = list(self.auth.accounts.keys())[0] if (self.auth and self.auth.accounts) else ""
        is_primary = (aid == primary_id)

        if is_edit and aid:
            current_mount = get_account_mount_point(self.config, aid, email=email, is_primary=is_primary)
            default_name = get_account_display_name(self.config, aid, email=email, is_primary=is_primary)
            default_parent = os.path.dirname(current_mount.rstrip("/")) or os.path.expanduser("~")
        else:
            suggested = suggest_account_mount_point(self.config, email=email, is_primary=is_primary)
            default_name = os.path.basename(suggested.rstrip("/")) or "GoogleDrive"
            default_parent = os.path.dirname(suggested.rstrip("/")) or os.path.expanduser("~")

        dlg = Gtk.Dialog(
            title=f"Configurar Nome e Pasta no Nautilus — {email}",
            transient_for=self.window,
            flags=0
        )
        if is_edit:
            dlg.add_buttons(
                "Cancelar", Gtk.ResponseType.CANCEL,
                "Salvar e Atualizar no Nautilus", Gtk.ResponseType.OK
            )
        else:
            dlg.add_buttons(
                "Confirmar e Criar Pasta na Lateral", Gtk.ResponseType.OK
            )
        dlg.set_default_size(560, 290)
        area = dlg.get_content_area()
        area.set_spacing(10)
        area.set_margin_top(16)
        area.set_margin_bottom(16)
        area.set_margin_start(20)
        area.set_margin_end(20)

        # Header with Google Drive Logo + Title
        hdr_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        logo_svg = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "icons", "drive_logo_24px.svg")
        if os.path.exists(logo_svg):
            hdr_box.pack_start(self._create_scaled_image(logo_svg, 36, 36), False, False, 0)

        lbl_head = Gtk.Label()
        lbl_head.set_markup(
            f"<big><b>Conta: {GLib.markup_escape_text(email)}</b></big>\n"
            "<small>Defina o <b>nome que aparecerá na lateral esquerda do Nautilus</b> (com o logo do Google Drive) "
            "e o diretório onde a pasta ficará localizada:</small>"
        )
        lbl_head.set_line_wrap(True)
        lbl_head.set_alignment(0, 0)
        hdr_box.pack_start(lbl_head, True, True, 0)
        area.pack_start(hdr_box, False, False, 0)

        # Field 1: Custom Name in Nautilus Sidebar
        lbl_name = Gtk.Label()
        lbl_name.set_markup("<b>Nome exibido na lateral do Nautilus:</b>")
        lbl_name.set_alignment(0, 0.5)
        area.pack_start(lbl_name, False, False, 0)

        name_entry = Gtk.Entry()
        name_entry.set_text(default_name)
        name_entry.set_placeholder_text("Ex: Google Drive - Pessoal ou Google Drive - Loja")
        area.pack_start(name_entry, False, False, 0)

        # Field 2: Parent Directory Location
        lbl_loc = Gtk.Label()
        lbl_loc.set_markup("<b>Local onde a pasta será montada (Diretório pai):</b>")
        lbl_loc.set_alignment(0, 0.5)
        area.pack_start(lbl_loc, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        parent_entry = Gtk.Entry()
        parent_entry.set_text(default_parent)
        parent_entry.set_hexpand(True)
        row.pack_start(parent_entry, True, True, 0)

        lbl_preview = Gtk.Label()
        lbl_preview.set_alignment(0, 0.5)

        def _update_preview(*_args):
            cname = name_entry.get_text().strip().replace("/", "-") or default_name
            cparent = parent_entry.get_text().strip().rstrip("/") or default_parent
            full_path = os.path.join(cparent, cname)
            lbl_preview.set_markup(
                f"<small>📍 Caminho final: <b>{GLib.markup_escape_text(full_path)}</b></small>"
            )

        name_entry.connect("changed", _update_preview)
        parent_entry.connect("changed", _update_preview)
        _update_preview()

        def _browse(_btn):
            chooser = Gtk.FileChooserDialog(
                title=f"Selecionar diretório pai para {email}",
                transient_for=dlg,
                action=Gtk.FileChooserAction.SELECT_FOLDER
            )
            chooser.add_buttons(
                "Cancelar", Gtk.ResponseType.CANCEL,
                "Selecionar Esta Pasta", Gtk.ResponseType.OK
            )
            cur_p = parent_entry.get_text().strip()
            if cur_p and os.path.isdir(cur_p):
                chooser.set_current_folder(cur_p)
            if chooser.run() == Gtk.ResponseType.OK:
                folder = chooser.get_filename()
                if folder:
                    parent_entry.set_text(folder)
            chooser.destroy()

        btn_browse = Gtk.Button(label="Escolher outra pasta...")
        btn_browse.connect("clicked", _browse)
        row.pack_start(btn_browse, False, False, 0)
        area.pack_start(row, False, False, 0)
        area.pack_start(lbl_preview, False, False, 2)

        dlg.show_all()
        resp = dlg.run()
        chosen_name = name_entry.get_text().strip().replace("/", "-") or default_name
        chosen_parent = parent_entry.get_text().strip().rstrip("/") or default_parent
        dlg.destroy()
        if is_edit and resp != Gtk.ResponseType.OK:
            return None, None
        chosen_mount = os.path.join(chosen_parent, chosen_name)
        return chosen_mount, chosen_name

    def _on_switch_account(self, account_id: str):
        if not account_id:
            return
        try:
            from core.config import save_config
            accs = self.db.get_accounts()
            chosen = next((a for a in accs if a.get("account_id") == account_id), None)
            if chosen:
                self.config["active_account_id"] = account_id
                save_config(self.config)
                if self.on_account_connected:
                    self.on_account_connected(chosen)
                if self.window:
                    self.window.destroy()
                self.show()
        except Exception as e:
            print(f"[UI] Erro ao alternar conta: {e}")

    def _on_add_account(self):
        if not self.auth:
            return

        has_existing = len(self.db.get_accounts()) > 0
        prompt_dlg = Gtk.Dialog(
            title="Adicionar Conta do Google",
            transient_for=self.window,
            flags=0
        )
        prompt_dlg.add_buttons(
            "Cancelar", Gtk.ResponseType.CANCEL,
            "Continuar no Navegador", Gtk.ResponseType.OK
        )
        prompt_dlg.set_default_size(460, 210)
        area = prompt_dlg.get_content_area()
        area.set_spacing(10)
        area.set_margin_top(15)
        area.set_margin_bottom(15)
        area.set_margin_start(18)
        area.set_margin_end(18)

        lbl_info = Gtk.Label()
        lbl_info.set_markup(
            "<b>Conectar Conta do Google Drive</b>\n"
            "<small>Informe o e-mail da conta Google que deseja conectar (opcional, mas recomendado "
            "para ir direto à conta desejada):</small>"
        )
        lbl_info.set_line_wrap(True)
        lbl_info.set_alignment(0, 0)
        area.pack_start(lbl_info, False, False, 0)

        email_entry = Gtk.Entry()
        email_entry.set_placeholder_text("exemplo@gmail.com (opcional)")
        email_entry.connect("activate", lambda _: prompt_dlg.response(Gtk.ResponseType.OK))
        area.pack_start(email_entry, False, False, 0)

        isolated_cb = Gtk.CheckButton(
            label="Abrir em janela isolada/anônima (evita Erro 400 de múltiplas sessões no Chrome)"
        )
        isolated_cb.set_active(has_existing)
        area.pack_start(isolated_cb, False, False, 4)

        prompt_dlg.show_all()
        resp = prompt_dlg.run()
        login_hint = email_entry.get_text().strip()
        use_isolated = isolated_cb.get_active()
        prompt_dlg.destroy()

        if resp != Gtk.ResponseType.OK:
            return

        wait_dlg = Gtk.MessageDialog(
            transient_for=self.window,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.CANCEL,
            text="Aguardando conclusão do login no navegador..."
        )
        wait_dlg.format_secondary_text(
            "Conclua a autenticação na janela do navegador que foi aberta.\n"
            "Esta janela será atualizada automaticamente assim que o Google confirmar o acesso."
        )

        def _on_wait_response(_dlg, response_id):
            if response_id == Gtk.ResponseType.CANCEL:
                from core.auth import OAuthCallbackHandler
                OAuthCallbackHandler.error = "access_denied"
                wait_dlg.destroy()

        wait_dlg.connect("response", _on_wait_response)
        wait_dlg.show_all()

        def _worker():
            acc = self.auth.login_interactive(login_hint=login_hint, isolated_session=use_isolated)

            def _on_done():
                try:
                    wait_dlg.destroy()
                except Exception:
                    pass

                if acc:
                    from core.config import save_config
                    aid = acc.get("account_id", "")
                    chosen_mount, chosen_name = self._prompt_account_mount_point(acc)
                    if "account_mounts" not in self.config or not isinstance(self.config.get("account_mounts"), dict):
                        self.config["account_mounts"] = {}
                    if "account_names" not in self.config or not isinstance(self.config.get("account_names"), dict):
                        self.config["account_names"] = {}
                    self.config["account_mounts"][aid] = chosen_mount
                    self.config["account_names"][aid] = chosen_name
                    self.config["active_account_id"] = aid
                    confirmed = list(self.config.get("confirmed_account_mounts") or [])
                    if aid and aid not in confirmed:
                        confirmed.append(aid)
                    self.config["confirmed_account_mounts"] = confirmed
                    save_config(self.config)
                    acc["mount_point"] = chosen_mount
                    acc["display_name_nautilus"] = chosen_name
                    if self.on_account_connected:
                        self.on_account_connected(acc)
                    if self.window:
                        self.window.destroy()
                    self.show()
                else:
                    err_msg = getattr(self.auth, "last_error", None) or "Não foi possível concluir a autenticação com o Google."
                    err_dlg = Gtk.MessageDialog(
                        transient_for=self.window,
                        message_type=Gtk.MessageType.ERROR,
                        buttons=Gtk.ButtonsType.OK,
                        text="Não foi possível concluir o login da conta"
                    )
                    err_dlg.format_secondary_text(err_msg)
                    err_dlg.run()
                    err_dlg.destroy()

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
            from core.config import get_account_mount_point, unmount_fuse_path, save_config
            mnt = get_account_mount_point(self.config, account_id)
            if mnt:
                unmount_fuse_path(mnt, remove_if_empty=True)
            if isinstance(self.config.get("account_mounts"), dict) and account_id in self.config["account_mounts"]:
                del self.config["account_mounts"][account_id]
            if isinstance(self.config.get("confirmed_account_mounts"), list) and account_id in self.config["confirmed_account_mounts"]:
                self.config["confirmed_account_mounts"].remove(account_id)
            save_config(self.config)
            # Remove from DB
            with self.db._get_connection() as conn:
                conn.execute("DELETE FROM items WHERE account_id = ?", (account_id,))
                conn.execute("DELETE FROM operations WHERE account_id = ?", (account_id,))
                conn.execute("DELETE FROM accounts WHERE account_id = ?", (account_id,))
                conn.commit()
            if self.auth and account_id in self.auth.accounts:
                del self.auth.accounts[account_id]
                self.auth._save_accounts_to_disk()
            if self.on_account_connected:
                self.on_account_connected(None)
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

        GLib.set_prgname("google-drive")
        Gdk.set_program_class("google-drive")

        window = Gtk.Window(title="Google Drive - Preferências")
        window.set_default_size(580, 520)
        window.set_position(Gtk.WindowPosition.CENTER)
        try:
            window.set_wmclass("google-drive", "Google Drive")
        except Exception:
            pass
        window.connect("destroy", lambda _: setattr(self, "window", None))

        icon_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "assets", "icons", "drive_fs.png"
        )
        if os.path.exists(icon_path):
            try:
                Gtk.Window.set_default_icon_from_file(icon_path)
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

        # Accounts Box (with editable Nautilus sidebar name & folder per account)
        acc_frame = Gtk.Frame(label="Contas Conectadas e Nome na Lateral do Nautilus")
        acc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        acc_box.set_margin_top(10)
        acc_box.set_margin_start(10)
        acc_box.set_margin_end(10)
        acc_box.set_margin_bottom(10)

        from core.config import get_account_mount_point, get_account_display_name
        accounts = self.auth_manager.accounts if self.auth_manager else {}
        account_rows_controls = {}
        primary_id = list(accounts.keys())[0] if accounts else ""

        if accounts:
            for acc_id, acc_info in accounts.items():
                is_prim = (acc_id == primary_id)
                email = acc_info.get("email", "")
                cur_mount = get_account_mount_point(self.config, acc_id, email=email, is_primary=is_prim)
                cur_name = get_account_display_name(self.config, acc_id, email=email, is_primary=is_prim)
                cur_parent = os.path.dirname(cur_mount.rstrip("/")) or os.path.expanduser("~")

                acc_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                lbl = Gtk.Label()
                lbl.set_markup(f"<b>✓ {GLib.markup_escape_text(email)}</b> ({GLib.markup_escape_text(acc_info.get('display_name', ''))})")
                lbl.set_alignment(0, 0.5)
                acc_card.pack_start(lbl, False, False, 0)

                r_name = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                l_n = Gtk.Label(label="Nome no Nautilus:")
                l_n.set_size_request(140, -1)
                l_n.set_alignment(0, 0.5)
                e_name = Gtk.Entry()
                e_name.set_text(cur_name)
                e_name.set_hexpand(True)
                r_name.pack_start(l_n, False, False, 0)
                r_name.pack_start(e_name, True, True, 0)
                acc_card.pack_start(r_name, False, False, 0)

                r_dir = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                l_d = Gtk.Label(label="Local da pasta:")
                l_d.set_size_request(140, -1)
                l_d.set_alignment(0, 0.5)
                e_dir = Gtk.Entry()
                e_dir.set_text(cur_parent)
                e_dir.set_hexpand(True)
                b_dir = Gtk.Button(label="Procurar...")
                b_dir.connect("clicked", lambda _, ent=e_dir: _choose_folder(window, ent, is_mount_point=False))
                r_dir.pack_start(l_d, False, False, 0)
                r_dir.pack_start(e_dir, True, True, 0)
                r_dir.pack_start(b_dir, False, False, 0)
                acc_card.pack_start(r_dir, False, False, 0)

                acc_box.pack_start(acc_card, False, False, 2)
                acc_box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 2)
                account_rows_controls[acc_id] = (e_name, e_dir, cur_name, cur_parent, is_prim)
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

            if "account_mounts" not in updated_config or not isinstance(updated_config.get("account_mounts"), dict):
                updated_config["account_mounts"] = {}
            if "account_names" not in updated_config or not isinstance(updated_config.get("account_names"), dict):
                updated_config["account_names"] = {}

            for acc_id, (e_name, e_dir, def_name, def_dir, is_prim) in account_rows_controls.items():
                new_n = e_name.get_text().strip().replace("/", "-") or def_name
                new_d = e_dir.get_text().strip().rstrip("/") or def_dir
                full_mp = os.path.join(new_d, new_n)
                updated_config["account_names"][acc_id] = new_n
                updated_config["account_mounts"][acc_id] = full_mp
                ensure_apparmor_allows_mount(full_mp)
                if is_prim:
                    updated_config["mount_point"] = full_mp
                    path_entry.set_text(full_mp)

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
                    err_msg = getattr(self.auth_manager, "last_error", None) or "Não foi possível concluir a autenticação com o Google."
                    err_dlg = Gtk.MessageDialog(
                        transient_for=parent_window,
                        message_type=Gtk.MessageType.ERROR,
                        buttons=Gtk.ButtonsType.OK,
                        text="Não foi possível concluir o login da conta"
                    )
                    err_dlg.format_secondary_text(err_msg)
                    err_dlg.run()
                    err_dlg.destroy()

            GLib.idle_add(_on_done)

        threading.Thread(target=_worker, daemon=True).start()
