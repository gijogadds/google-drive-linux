#!/usr/bin/env python3
"""
Google Drive for Linux - Standalone Native OAuth Authentication Window
Provides a dedicated GTK3 + WebKit2 window for Google Sign-In, completely isolated
from the user's personal Google Chrome / default browser.
"""
import sys
import os
import urllib.parse

def run_auth_window(auth_url: str, redirect_uri: str, session_dir: str):
    import gi
    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    try:
        gi.require_version("WebKit2", "4.1")
    except ValueError:
        gi.require_version("WebKit2", "4.0")
    from gi.repository import GLib, Gdk, Gtk, GdkPixbuf, WebKit2

    GLib.set_prgname("google-drive")
    GLib.set_application_name("Google Drive")
    Gdk.set_program_class("google-drive")

    window = Gtk.Window(title="Google Drive - Conectar Conta")
    window.set_default_size(580, 720)
    window.set_position(Gtk.WindowPosition.CENTER)

    icon_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", "icons", "drive_fs.png"
    )
    if os.path.exists(icon_path):
        try:
            window.set_icon(GdkPixbuf.Pixbuf.new_from_file(icon_path))
        except Exception:
            pass

    # HeaderBar
    header = Gtk.HeaderBar()
    header.set_show_close_button(True)
    header.set_title("Conectar Conta Google")
    header.set_subtitle("Google Drive para Linux")
    if os.path.exists(icon_path):
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_path, 24, 24, True)
            header.pack_start(Gtk.Image.new_from_pixbuf(pb))
        except Exception:
            pass
    window.set_titlebar(header)

    # Isolated WebKit session directory
    os.makedirs(session_dir, exist_ok=True)
    data_dir = os.path.join(session_dir, "data")
    cache_dir = os.path.join(session_dir, "cache")
    cookies_file = os.path.join(session_dir, "cookies.sqlite")
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

    webview = WebKit2.WebView.new_with_context(web_context)
    settings = webview.get_settings()
    settings.set_enable_javascript(True)
    settings.set_enable_webgl(True)
    settings.set_user_agent(
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
    )

    clean_redirect = redirect_uri.rstrip("/")

    closed = False

    def _close_app():
        nonlocal closed
        if not closed:
            closed = True
            try:
                window.destroy()
            except Exception:
                pass
            Gtk.main_quit()
        return False

    def _on_load_changed(wv, load_event):
        uri = wv.get_uri() or ""
        # Check if Google redirected back to our local OAuth callback server
        if uri.startswith(clean_redirect) or "127.0.0.1" in uri:
            if load_event == WebKit2.LoadEvent.FINISHED:
                GLib.timeout_add(800, _close_app)

    def _on_decide_policy(wv, decision, decision_type):
        if decision_type == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            action = decision.get_navigation_action()
            req = action.get_request()
            uri = req.get_uri() if req else ""
            if uri.startswith(clean_redirect) or "127.0.0.1" in uri:
                GLib.timeout_add(1500, _close_app)
        return False

    webview.connect("load-changed", _on_load_changed)
    webview.connect("decide-policy", _on_decide_policy)

    window.add(webview)
    webview.load_uri(auth_url)
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    Gtk.main()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: auth_window.py <AUTH_URL> <REDIRECT_URI> [SESSION_DIR]")
        sys.exit(1)

    url_arg = sys.argv[1]
    redirect_arg = sys.argv[2]
    sess_dir = sys.argv[3] if len(sys.argv) > 3 else os.path.expanduser("~/.config/google-drive-linux/workspace_webkit/oauth_temp")
    run_auth_window(url_arg, redirect_arg, sess_dir)
