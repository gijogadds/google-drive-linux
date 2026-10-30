"""
Google Drive for Linux - Standalone Application Launcher for Google Docs, Sheets & Slides
Automatically detects which account owns the clicked document (e.g. Giovanni Augusto vs Store Caneca Barata)
and opens it directly in a dedicated app window using that account's active session without requiring login.
"""
import sys
import os
import json
import shutil
import urllib.parse
import subprocess
from typing import Optional, Tuple, Dict, Any


def find_browser_for_app_mode() -> Optional[str]:
    """Find a Chromium-based browser supporting the --app flag in Linux (Flatpak, APT, or Snap)."""
    candidates = [
        "google-chrome",
        "google-chrome-stable",
        "chromium-browser",
        "chromium",
        "brave-browser",
        "microsoft-edge-stable",
        "microsoft-edge",
    ]
    for b in candidates:
        path = shutil.which(b)
        if path:
            return path

    extra_paths = [
        "/var/lib/flatpak/exports/bin/com.google.Chrome",
        os.path.expanduser("~/.local/share/flatpak/exports/bin/com.google.Chrome"),
        "/var/lib/flatpak/exports/bin/org.chromium.Chromium",
        os.path.expanduser("~/.local/share/flatpak/exports/bin/org.chromium.Chromium"),
        "/var/lib/flatpak/exports/bin/com.brave.Browser",
        "/var/lib/flatpak/exports/bin/com.microsoft.Edge",
        "/snap/bin/chromium",
        "/snap/bin/google-chrome",
        "/snap/bin/brave",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
    ]
    for sp in extra_paths:
        if os.path.exists(sp) and os.access(sp, os.X_OK):
            return sp

    return None


def find_chrome_profile(target_email: str = "", target_gaia_id: str = "") -> Tuple[Optional[str], Optional[str]]:
    """
    Search Chromium / Google Chrome profiles (Flatpak and native) in 'Local State'
    to find the profile directory (e.g. 'Default', 'Profile 1') matching target email or gaia_id.
    """
    state_paths = [
        os.path.expanduser("~/.var/app/com.google.Chrome/config/google-chrome/Local State"),
        os.path.expanduser("~/.config/google-chrome/Local State"),
        os.path.expanduser("~/.config/chromium/Local State"),
        os.path.expanduser("~/.var/app/org.chromium.Chromium/config/chromium/Local State"),
    ]
    target_email_clean = (target_email or "").strip().lower()
    target_gaia_clean = str(target_gaia_id or "").strip()

    for p in state_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                info_cache = data.get("profile", {}).get("info_cache", {})
                for prof_dir, info in info_cache.items():
                    u_email = str(info.get("user_name") or "").strip().lower()
                    u_gaia = str(info.get("gaia_id") or "").strip()
                    if target_gaia_clean and u_gaia == target_gaia_clean:
                        return prof_dir, u_email
                    if target_email_clean and u_email == target_email_clean:
                        return prof_dir, u_email
            except Exception:
                pass

    return None, None


def _get_accounts_dict() -> Dict[str, Any]:
    """Load connected accounts from accounts.json."""
    try:
        from core.config import ACCOUNTS_FILE_PATH
        if os.path.exists(ACCOUNTS_FILE_PATH):
            with open(ACCOUNTS_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _resolve_account_for_file_path(file_path: str) -> Tuple[str, str]:
    """Given a file path, resolve (account_id, email) using configured account mounts."""
    if not file_path:
        return "", ""
    try:
        from core.config import load_config
        cfg = load_config()
        mounts = cfg.get("account_mounts") or {}
        accounts = _get_accounts_dict()

        # Check which account folder contains this file
        clean_path = os.path.abspath(file_path)
        for acc_id, mp in mounts.items():
            if mp and (clean_path == mp or clean_path.startswith(mp.rstrip("/") + "/")):
                email = (accounts.get(acc_id) or {}).get("email", "")
                return acc_id, email

        # Fallback to active account or first registered account
        active_id = cfg.get("active_account_id")
        if active_id and active_id in accounts:
            return active_id, (accounts[active_id].get("email") or "")
        if accounts:
            first_id = list(accounts.keys())[0]
            return first_id, (accounts[first_id].get("email") or "")
    except Exception:
        pass
    return "", ""


def _parse_local_doc_file(file_path: str, fallback_type: str = "doc") -> Tuple[str, str, str, str, str]:
    """
    Parse a local .gdoc, .gsheet, .gslides, .gform, or legacy .desktop file
    and return (identifier_or_url, doc_type, title, account_id, email).
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
    acc_id, email = _resolve_account_for_file_path(file_path)

    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read().strip()
        if raw.startswith("{"):
            data = json.loads(raw)
            doc_type = data.get("gd_type") or data.get("doc_type") or inferred_type
            url = data.get("url") or data.get("doc_id") or ""
            title = data.get("title") or title
            acc_id = data.get("account_id") or acc_id
            email = data.get("email") or email
            if url:
                return url, doc_type, title, acc_id, email
        elif "[Desktop Entry]" in raw:
            for line in raw.splitlines():
                if line.startswith("Exec="):
                    parts = line.split()
                    if len(parts) >= 2:
                        doc_id = parts[1]
                        d_type = parts[2] if len(parts) >= 3 else inferred_type
                        return doc_id, d_type, title, acc_id, email
    except Exception as e:
        print(f"[DocLauncher] Aviso ao ler arquivo {file_path}: {e}")

    return file_path, inferred_type, title, acc_id, email


def get_doc_url(identifier: str, doc_type: str = "doc", email: str = "") -> str:
    """Resolve an app home command, file ID, or URL into the target Google Workspace URL, with authuser hint."""
    clean = (identifier or "").strip()

    if clean in ("", "doc", "document", "--home-doc"):
        base_url = "https://docs.google.com/document/u/0/"
    elif clean == "--new-doc":
        base_url = "https://docs.google.com/document/create"
    elif clean in ("sheet", "spreadsheet", "--home-sheet"):
        base_url = "https://docs.google.com/spreadsheets/u/0/"
    elif clean == "--new-sheet":
        base_url = "https://docs.google.com/spreadsheets/create"
    elif clean in ("slide", "presentation", "--home-slide"):
        base_url = "https://docs.google.com/presentation/u/0/"
    elif clean == "--new-slide":
        base_url = "https://docs.google.com/presentation/create"
    elif clean in ("form", "--home-form"):
        base_url = "https://docs.google.com/forms/u/0/"
    elif clean.startswith("http://") or clean.startswith("https://"):
        base_url = clean
    else:
        # Treat as Google Drive file ID
        if doc_type in ("doc", "document"):
            base_url = f"https://docs.google.com/document/d/{clean}/edit"
        elif doc_type in ("sheet", "spreadsheet"):
            base_url = f"https://docs.google.com/spreadsheets/d/{clean}/edit"
        elif doc_type in ("slide", "presentation"):
            base_url = f"https://docs.google.com/presentation/d/{clean}/edit"
        elif doc_type in ("form",):
            base_url = f"https://docs.google.com/forms/d/{clean}/edit"
        else:
            base_url = f"https://drive.google.com/open?id={clean}"

    if email and "authuser=" not in base_url and ("docs.google.com" in base_url or "drive.google.com" in base_url):
        sep = "&" if "?" in base_url else "?"
        base_url = f"{base_url}{sep}authuser={urllib.parse.quote(email)}"

    return base_url


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


def _run_native_webkit_app(url: str, doc_type: str, initial_title: str = "", account_id: str = "", email: str = "") -> bool:
    """
    Run Google Docs, Sheets, or Slides inside an independent native GTK3 + WebKit2 desktop window
    with an isolated session per account so multiple Google accounts never conflict.
    """
    wm_class, app_title, icon_file, new_url = _resolve_type_metadata(doc_type)

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

    # Dedicated WebKit profile per Google Account (e.g. Giovanni Augusto vs Store Caneca Barata)
    safe_acc_id = account_id if account_id else "default"
    profile_root = os.path.expanduser(f"~/.config/google-drive-linux/workspace_webkit/acc_{safe_acc_id}")
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

    header = Gtk.HeaderBar()
    header.set_show_close_button(True)
    header.set_title(initial_title if initial_title else app_title)

    if os.path.exists(icon_path):
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_path, 20, 20, True)
            header.pack_start(Gtk.Image.new_from_pixbuf(pb))
        except Exception:
            pass

    window.set_titlebar(header)

    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

    progress_bar = Gtk.ProgressBar()
    progress_bar.set_no_show_all(True)
    vbox.pack_start(progress_bar, False, False, 0)

    info_bar = Gtk.InfoBar()
    info_bar.set_no_show_all(True)
    info_bar.set_show_close_button(True)
    info_lbl = Gtk.Label(label="")
    info_bar.get_content_area().add(info_lbl)
    info_bar.add_button("📂 Abrir Pasta Downloads", Gtk.ResponseType.OK)
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
    if hasattr(settings, "set_enable_smooth_scrolling"):
        settings.set_enable_smooth_scrolling(True)
    if hasattr(settings, "set_javascript_can_access_clipboard"):
        settings.set_javascript_can_access_clipboard(True)
    if hasattr(settings, "set_javascript_can_open_windows_automatically"):
        settings.set_javascript_can_open_windows_automatically(True)
    settings.set_user_agent(
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
    )

    vbox.pack_start(webview, True, True, 0)
    window.add(vbox)

    # Native application keyboard shortcuts
    def _on_key_press(win, event):
        # F5 -> Reload
        if event.keyval == Gdk.KEY_F5:
            webview.reload()
            return True
        # F11 -> Toggle Fullscreen
        if event.keyval == Gdk.KEY_F11:
            if getattr(win, "_is_fullscreen", False):
                win.unfullscreen()
                win._is_fullscreen = False
            else:
                win.fullscreen()
                win._is_fullscreen = True
            return True
        # Ctrl+R -> Reload
        if (event.state & Gdk.ModifierType.CONTROL_MASK) and event.keyval in (Gdk.KEY_r, Gdk.KEY_R):
            if event.state & Gdk.ModifierType.SHIFT_MASK:
                webview.reload_bypass_cache()
            else:
                webview.reload()
            return True
        # Ctrl+W or Ctrl+Q -> Close Window
        if (event.state & Gdk.ModifierType.CONTROL_MASK) and event.keyval in (Gdk.KEY_w, Gdk.KEY_W, Gdk.KEY_q, Gdk.KEY_Q):
            win.close()
            return True
        return False

    window._is_fullscreen = False
    window.connect("key-press-event", _on_key_press)

    # Allow Google Docs/Sheets native context menus (cell options, insert, cut/copy/paste)
    def _on_context_menu(wv, context_menu, event, hit_test_result):
        return True

    webview.connect("context-menu", _on_context_menu)

    # Open external links in default browser instead of navigating the editor away
    def _on_decide_policy(wv, decision, decision_type):
        if decision_type == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            action = decision.get_navigation_action()
            req = action.get_request()
            target_uri = req.get_uri() if req else ""
            if action.get_navigation_type() == WebKit2.NavigationType.LINK_CLICKED and target_uri:
                if not any(h in target_uri for h in ("docs.google.com", "accounts.google.com", "drive.google.com")):
                    subprocess.Popen(["xdg-open", target_uri])
                    decision.ignore()
                    return True
        return False

    webview.connect("decide-policy", _on_decide_policy)

    def _on_title_changed(wv, _param):
        t = wv.get_title()
        if t:
            clean = t.strip()
            for suffix in (" - Google Sheets", " - Google Docs", " - Google Slides"):
                if clean.endswith(suffix):
                    clean = clean[:-len(suffix)].strip()
            header.set_title(clean)
            window.set_title(clean)

    def _on_progress_changed(wv, _param):
        prog = wv.get_estimated_load_progress()
        if 0.0 < prog < 1.0:
            progress_bar.set_fraction(prog)
            progress_bar.show()
        else:
            progress_bar.hide()

    def _on_create_new_web_view(wv, navigation_action):
        req = navigation_action.get_request()
        target_uri = req.get_uri() if req else ""
        if target_uri:
            if any(h in target_uri for h in ("docs.google.com", "accounts.google.com", "drive.google.com")):
                wv.load_uri(target_uri)
            else:
                subprocess.Popen(["xdg-open", target_uri])
        return None

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
        if "accounts.google.com" in uri and email:
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

    webview.load_uri(url)
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    Gtk.main()
    return True


def launch_doc_as_app(identifier: str = "doc", doc_type: str = "doc", background: bool = False, initial_title: str = "", force_account_id: str = ""):
    """
    Launch Google Docs, Sheets, or Slides (or a local .gdoc/.gsheet/.gslides file)
    routed directly to the corresponding connected account's active session.
    """
    raw_target = (identifier or "").strip()
    if raw_target.startswith("file://"):
        parsed = urllib.parse.urlparse(raw_target)
        raw_target = urllib.parse.unquote(parsed.path)

    title = initial_title
    acc_id = force_account_id
    email = ""

    if (
        os.path.isfile(raw_target)
        or raw_target.endswith((".gdoc", ".gsheet", ".gslides", ".gform", ".desktop"))
    ):
        raw_target, doc_type, parsed_title, parsed_acc_id, parsed_email = _parse_local_doc_file(raw_target, fallback_type=doc_type)
        if not title:
            title = parsed_title
        if parsed_acc_id and not acc_id:
            acc_id = parsed_acc_id
        if parsed_email and not email:
            email = parsed_email
    elif raw_target in ("doc", "document", "--home-doc", "--new-doc"):
        doc_type = "doc"
    elif raw_target in ("sheet", "spreadsheet", "--home-sheet", "--new-sheet"):
        doc_type = "sheet"
    elif raw_target in ("slide", "presentation", "--home-slide", "--new-slide"):
        doc_type = "slide"

    if not email and acc_id:
        accounts = _get_accounts_dict()
        email = (accounts.get(acc_id) or {}).get("email", "")

    url = get_doc_url(raw_target, doc_type, email=email)

    # Launch in background process if requested
    if background:
        launcher_bin = "/usr/local/bin/gdrive-doc-launcher"
        if not os.path.exists(launcher_bin):
            launcher_bin = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "gdrive-doc-launcher",
            )
        subprocess.Popen(
            [sys.executable, launcher_bin, f"--type={doc_type}", raw_target, title],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return

    # Run as native standalone application window (Google Planilhas / Docs / Slides)
    try:
        _run_native_webkit_app(url, doc_type, initial_title=title, account_id=acc_id, email=email)
    except Exception as e:
        print(f"[DocLauncher] Erro ao iniciar janela nativa WebKit2 ({e}), abrindo via xdg-open.")
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
