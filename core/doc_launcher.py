"""
Google Drive for Linux - Native Standalone App Engine for Google Docs, Sheets & Slides
Runs Google Docs, Sheets, and Slides as independent native Linux desktop programs using
GTK3 + WebKit2 (no dependency on Google Chrome being installed), with their own Ubuntu Dock
icons, WM_CLASS, persistent login session, and integrated file download support.
"""
import sys
import os
import json
import urllib.parse
import subprocess
from typing import Optional, Tuple


def _parse_local_doc_file(file_path: str, fallback_type: str = "doc") -> Tuple[str, str, str]:
    """
    Parse a local .gdoc, .gsheet, .gslides, .gform, or legacy .desktop file
    and return (identifier_or_url, doc_type, title).
    """
    ext = os.path.splitext(file_path)[1].lower()
    inferred_type = fallback_type
    if ext == ".gsheet":
        inferred_type = "sheet"
    elif ext == ".gslides":
        inferred_type = "slide"
    elif ext == ".gform":
        inferred_type = "form"
    elif ext == ".gdoc":
        inferred_type = "doc"

    title = os.path.splitext(os.path.basename(file_path))[0]
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read().strip()
        if raw.startswith("{"):
            data = json.loads(raw)
            doc_type = data.get("gd_type") or data.get("doc_type") or inferred_type
            url = data.get("url") or data.get("doc_id") or ""
            title = data.get("title") or title
            if url:
                return url, doc_type, title
        elif "[Desktop Entry]" in raw:
            for line in raw.splitlines():
                if line.startswith("Exec="):
                    parts = line.split()
                    if len(parts) >= 2:
                        doc_id = parts[1]
                        d_type = parts[2] if len(parts) >= 3 else inferred_type
                        return doc_id, d_type, title
    except Exception as e:
        print(f"[DocLauncher] Aviso ao ler arquivo {file_path}: {e}")

    return file_path, inferred_type, title


def get_doc_url(identifier: str, doc_type: str = "doc") -> str:
    """Resolve an app home command, file ID, or URL into the target Google Workspace URL."""
    clean = (identifier or "").strip()

    if clean in ("", "doc", "document", "--home-doc"):
        return "https://docs.google.com/document/u/0/"
    if clean == "--new-doc":
        return "https://docs.google.com/document/create"
    if clean in ("sheet", "spreadsheet", "--home-sheet"):
        return "https://docs.google.com/spreadsheets/u/0/"
    if clean == "--new-sheet":
        return "https://docs.google.com/spreadsheets/create"
    if clean in ("slide", "presentation", "--home-slide"):
        return "https://docs.google.com/presentation/u/0/"
    if clean == "--new-slide":
        return "https://docs.google.com/presentation/create"
    if clean in ("form", "--home-form"):
        return "https://docs.google.com/forms/u/0/"

    if clean.startswith("http://") or clean.startswith("https://"):
        return clean

    if doc_type in ("doc", "document"):
        return f"https://docs.google.com/document/d/{clean}/edit"
    elif doc_type in ("sheet", "spreadsheet"):
        return f"https://docs.google.com/spreadsheets/d/{clean}/edit"
    elif doc_type in ("slide", "presentation"):
        return f"https://docs.google.com/presentation/d/{clean}/edit"
    elif doc_type in ("form",):
        return f"https://docs.google.com/forms/d/{clean}/edit"
    else:
        return f"https://drive.google.com/open?id={clean}"


def _resolve_type_metadata(doc_type: str) -> Tuple[str, str, str, str]:
    """Return (wm_class, app_title, icon_filename, new_doc_url) for doc_type."""
    if doc_type in ("sheet", "spreadsheet"):
        return (
            "google-sheets",
            "Google Sheets",
            "sheets.png",
            "https://docs.google.com/spreadsheets/create",
        )
    if doc_type in ("slide", "presentation"):
        return (
            "google-slides",
            "Google Slides",
            "slides.png",
            "https://docs.google.com/presentation/create",
        )
    return (
        "google-docs",
        "Google Docs",
        "docs.png",
        "https://docs.google.com/document/create",
    )


def _get_primary_account_email() -> str:
    """Return the active/primary Google Drive account email to assist login hint if needed."""
    try:
        from core.config import ACCOUNTS_FILE_PATH
        if os.path.exists(ACCOUNTS_FILE_PATH):
            with open(ACCOUNTS_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for _, info in data.items():
                    if isinstance(info, dict) and info.get("email"):
                        return info["email"]
    except Exception:
        pass
    return ""


def _run_native_webkit_app(url: str, doc_type: str, initial_title: str = "") -> bool:
    """
    Run Google Docs, Sheets, or Slides inside an independent native GTK3 + WebKit2 desktop window.
    Sets the exact Wayland/X11 program class (google-docs, google-sheets, google-slides) so Ubuntu Dock
    shows the standalone app icon instead of grouping under a browser.
    """
    wm_class, app_title, icon_file, new_url = _resolve_type_metadata(doc_type)

    # Set program identity BEFORE importing/initializing GTK so Wayland xdg_toplevel app_id matches .desktop
    sys.argv[0] = wm_class
    import gi
    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    try:
        gi.require_version("WebKit2", "4.1")
    except ValueError:
        gi.require_version("WebKit2", "4.0")
    from gi.repository import GLib, Gdk, Gtk, GdkPixbuf, WebKit2

    GLib.set_prgname(wm_class)
    GLib.set_application_name(app_title)
    Gdk.set_program_class(wm_class)

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    icon_path = os.path.join(project_root, "assets", "icons", icon_file)
    if os.path.exists(icon_path):
        try:
            Gtk.Window.set_default_icon_from_file(icon_path)
        except Exception:
            pass

    # Shared persistent WebKit profile across Docs, Sheets, and Slides so login is kept permanently
    profile_root = os.path.expanduser("~/.config/google-drive-linux/workspace_webkit")
    data_dir = os.path.join(profile_root, "data")
    cache_dir = os.path.join(profile_root, "cache")
    cookies_file = os.path.join(profile_root, "cookies.sqlite")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)

    data_manager = WebKit2.WebsiteDataManager(
        base_data_directory=data_dir,
        base_cache_directory=cache_dir,
    )
    web_context = WebKit2.WebContext.new_with_website_data_manager(data_manager)
    cookie_manager = web_context.get_cookie_manager()
    cookie_manager.set_persistent_storage(
        cookies_file,
        WebKit2.CookiePersistentStorage.SQLITE,
    )
    cookie_manager.set_accept_policy(WebKit2.CookieAcceptPolicy.ALWAYS)

    window = Gtk.Window(title=f"{initial_title} - {app_title}" if initial_title else app_title)
    window.set_default_size(1280, 840)
    window.set_position(Gtk.WindowPosition.CENTER)
    try:
        window.set_wmclass(wm_class, app_title)
    except Exception:
        pass
    if os.path.exists(icon_path):
        try:
            window.set_icon(GdkPixbuf.Pixbuf.new_from_file(icon_path))
        except Exception:
            pass

    # HeaderBar with native controls
    header = Gtk.HeaderBar()
    header.set_show_close_button(True)
    header.set_title(initial_title if initial_title else app_title)
    header.set_subtitle(app_title)

    if os.path.exists(icon_path):
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_path, 24, 24, True)
            header.pack_start(Gtk.Image.new_from_pixbuf(pb))
        except Exception:
            pass

    btn_back = Gtk.Button.new_from_icon_name("go-previous-symbolic", Gtk.IconSize.BUTTON)
    btn_back.set_tooltip_text("Voltar")
    header.pack_start(btn_back)

    btn_fwd = Gtk.Button.new_from_icon_name("go-next-symbolic", Gtk.IconSize.BUTTON)
    btn_fwd.set_tooltip_text("Avançar")
    header.pack_start(btn_fwd)

    btn_reload = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
    btn_reload.set_tooltip_text("Recarregar documento")
    header.pack_start(btn_reload)

    btn_home = Gtk.Button(label=f"🏠 Início ({app_title.split()[-1]})")
    btn_home.set_tooltip_text(f"Ir para a tela inicial do {app_title}")
    header.pack_start(btn_home)

    btn_ext = Gtk.Button(label="🌐 Navegador")
    btn_ext.set_tooltip_text("Abrir este documento no navegador externo")
    header.pack_end(btn_ext)

    btn_new = Gtk.Button(label="➕ Novo")
    btn_new.set_tooltip_text(f"Criar novo arquivo no {app_title}")
    header.pack_end(btn_new)

    window.set_titlebar(header)

    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

    # Progress bar + download notification bar
    progress_bar = Gtk.ProgressBar()
    progress_bar.set_no_show_all(True)
    vbox.pack_start(progress_bar, False, False, 0)

    info_bar = Gtk.InfoBar()
    info_bar.set_no_show_all(True)
    info_bar.set_show_close_button(True)
    info_lbl = Gtk.Label(label="")
    info_bar.get_content_area().add(info_lbl)
    btn_open_dl = info_bar.add_button("📂 Abrir Pasta Downloads", Gtk.ResponseType.OK)
    info_bar.connect(
        "response",
        lambda bar, resp: (
            subprocess.Popen(["xdg-open", os.path.expanduser("~/Downloads")])
            if resp == Gtk.ResponseType.OK
            else None,
            bar.hide(),
        ),
    )
    vbox.pack_start(info_bar, False, False, 0)

    webview = WebKit2.WebView.new_with_context(web_context)
    settings = webview.get_settings()
    settings.set_enable_javascript(True)
    settings.set_enable_webgl(True)
    settings.set_enable_Smooth_scrolling(True) if hasattr(settings, "set_enable_Smooth_scrolling") else None
    if hasattr(settings, "set_enable_smooth_scrolling"):
        settings.set_enable_smooth_scrolling(True)
    if hasattr(settings, "set_javascript_can_access_clipboard"):
        settings.set_javascript_can_access_clipboard(True)
    if hasattr(settings, "set_javascript_can_open_windows_automatically"):
        settings.set_javascript_can_open_windows_automatically(True)
    if hasattr(settings, "set_enable_developer_extras"):
        settings.set_enable_developer_extras(False)
    # Modern Linux Chrome User-Agent so Google Docs/Sheets/Slides and Google Sign-In enable 100% of desktop features
    settings.set_user_agent(
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
    )

    vbox.pack_start(webview, True, True, 0)
    window.add(vbox)

    # Wire navigation buttons
    btn_back.connect("clicked", lambda _: webview.go_back() if webview.can_go_back() else None)
    btn_fwd.connect("clicked", lambda _: webview.go_forward() if webview.can_go_forward() else None)
    btn_reload.connect("clicked", lambda _: webview.reload())
    btn_home.connect("clicked", lambda _: webview.load_uri(get_doc_url(doc_type, doc_type)))
    btn_new.connect("clicked", lambda _: webview.load_uri(new_url))
    btn_ext.connect("clicked", lambda _: subprocess.Popen(["xdg-open", webview.get_uri() or url]))

    def _on_title_changed(wv, _param):
        t = wv.get_title()
        if t:
            header.set_title(t)
            window.set_title(f"{t} - {app_title}")

    def _on_progress_changed(wv, _param):
        prog = wv.get_estimated_load_progress()
        if 0.0 < prog < 1.0:
            progress_bar.set_fraction(prog)
            progress_bar.show()
        else:
            progress_bar.hide()

    # Handle new window requests (e.g., clicking a link to another Doc or Google login popup)
    def _on_create_new_web_view(wv, navigation_action):
        req = navigation_action.get_request()
        target_uri = req.get_uri() if req else ""
        if target_uri:
            if any(h in target_uri for h in ("docs.google.com", "accounts.google.com", "drive.google.com")):
                wv.load_uri(target_uri)
            else:
                subprocess.Popen(["xdg-open", target_uri])
        return None

    # Handle file exports/downloads (e.g., Arquivo -> Fazer download -> Excel / PDF / Word)
    def _on_download_started(_ctx, download):
        def _on_decide_destination(dl, suggested_filename):
            dl_dir = os.path.expanduser("~/Downloads")
            os.makedirs(dl_dir, exist_ok=True)
            fname = suggested_filename or "documento_exportado"
            dest_path = os.path.join(dl_dir, fname)
            base, ext = os.path.splitext(dest_path)
            counter = 1
            while os.path.exists(dest_path):
                dest_path = f"{base} ({counter}){ext}"
                counter += 1
            dl.set_destination(f"file://{urllib.parse.quote(dest_path)}")
            dl._saved_path = dest_path
            return True

        def _on_finished(dl):
            saved = getattr(dl, "_saved_path", os.path.expanduser("~/Downloads"))
            info_lbl.set_markup(f"<b>✅ Download concluído:</b> {GLib.markup_escape_text(os.path.basename(saved))}")
            info_lbl.show()
            info_bar.show()

        download.connect("decide-destination", _on_decide_destination)
        download.connect("finished", _on_finished)

    def _on_load_changed(wv, load_event):
        if load_event != WebKit2.LoadEvent.FINISHED:
            return
        uri = wv.get_uri() or ""
        if "accounts.google.com" in uri:
            email = _get_primary_account_email()
            if email:
                js = (
                    f"(function(){{"
                    f"var el=document.getElementById('identifierId')||document.querySelector('input[type=email]');"
                    f"if(el && !el.value){{el.value={json.dumps(email)};el.dispatchEvent(new Event('input',{{bubbles:true}}));}}"
                    f"}})();"
                )
                if hasattr(wv, "evaluate_javascript"):
                    wv.evaluate_javascript(js, -1, None, None, None, None, None)
                elif hasattr(wv, "run_javascript"):
                    wv.run_javascript(js, None, None, None)

    web_context.connect("download-started", _on_download_started)
    webview.connect("notify::title", _on_title_changed)
    webview.connect("notify::estimated-load-progress", _on_progress_changed)
    webview.connect("load-changed", _on_load_changed)
    webview.connect("create", _on_create_new_web_view)

    # Pre-fill Google account email hint on sign-in screen if available
    primary_email = _get_primary_account_email()
    if primary_email and "accounts.google.com" in url and "Email=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}Email={urllib.parse.quote(primary_email)}"

    webview.load_uri(url)
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    Gtk.main()
    return True


def launch_doc_as_app(identifier: str = "doc", doc_type: str = "doc", background: bool = False, initial_title: str = ""):
    """
    Launch Google Docs, Sheets, or Slides (or a local .gdoc/.gsheet/.gslides file)
    as an independent native Linux desktop application.
    """
    raw_target = (identifier or "").strip()
    if raw_target.startswith("file://"):
        parsed = urllib.parse.urlparse(raw_target)
        raw_target = urllib.parse.unquote(parsed.path)

    title = initial_title
    if (
        os.path.isfile(raw_target)
        or raw_target.endswith((".gdoc", ".gsheet", ".gslides", ".gform", ".desktop"))
    ):
        raw_target, doc_type, parsed_title = _parse_local_doc_file(raw_target, fallback_type=doc_type)
        if not title:
            title = parsed_title
    elif raw_target in ("doc", "document", "--home-doc", "--new-doc"):
        doc_type = "doc"
    elif raw_target in ("sheet", "spreadsheet", "--home-sheet", "--new-sheet"):
        doc_type = "sheet"
    elif raw_target in ("slide", "presentation", "--home-slide", "--new-slide"):
        doc_type = "slide"

    url = get_doc_url(raw_target, doc_type)

    # When called from inside the running Google Drive GUI, spawn a separate process so each app has its own WM_CLASS
    if background:
        launcher_bin = "/usr/local/bin/gdrive-doc-launcher"
        if not os.path.exists(launcher_bin):
            launcher_bin = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "gdrive-doc-launcher",
            )
        subprocess.Popen(
            [sys.executable, launcher_bin, f"--type={doc_type}", url, title],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return

    try:
        _run_native_webkit_app(url, doc_type, initial_title=title)
    except Exception as e:
        print(f"[DocLauncher] Aviso ao iniciar janela nativa WebKit2 ({e}), abrindo via xdg-open.")
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        launch_doc_as_app("doc", "doc")
        sys.exit(0)

    arg1 = sys.argv[1]
    if arg1.startswith("--type="):
        d_type = arg1.split("=", 1)[1]
        target = sys.argv[2] if len(sys.argv) > 2 else d_type
        title_arg = sys.argv[3] if len(sys.argv) > 3 else ""
        launch_doc_as_app(target, d_type, initial_title=title_arg)
    else:
        d_type = sys.argv[2] if len(sys.argv) > 2 else "doc"
        title_arg = sys.argv[3] if len(sys.argv) > 3 else ""
        launch_doc_as_app(arg1, d_type, initial_title=title_arg)
