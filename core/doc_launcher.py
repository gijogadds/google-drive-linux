"""
Google Drive for Linux - Standalone App Window Launcher for Google Docs, Sheets & Slides
Opens Google Docs, Sheets, and Slides (and .gdoc / .gsheet / .gslides files) in dedicated
standalone app windows (PWA mode), without browser URL bars or tabs, appearing on the Ubuntu
dock and application launcher as native desktop programs.
"""
import sys
import os
import json
import shutil
import urllib.parse
import subprocess
from typing import Optional, Tuple


def find_browser_for_app_mode() -> Optional[str]:
    """Find a Chromium-based browser supporting the --app flag in Linux (APT, Flatpak, or Snap)."""
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

    # Home / New Document shortcuts when launching the installed app directly from Ubuntu menu
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

    # Treat as Google Drive file ID
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


def _resolve_type_metadata(doc_type: str) -> Tuple[str, str, str]:
    """Return (wm_class, app_title, icon_filename) for doc_type."""
    if doc_type in ("sheet", "spreadsheet"):
        return "google-sheets", "Google Sheets", "sheets.png"
    if doc_type in ("slide", "presentation"):
        return "google-slides", "Google Slides", "slides.png"
    return "google-docs", "Google Docs", "docs.png"


def launch_doc_as_app(identifier: str = "doc", doc_type: str = "doc"):
    """Launch Google Docs/Sheets/Slides (or a local .gdoc/.gsheet/.gslides file) in a standalone desktop window."""
    raw_target = (identifier or "").strip()
    if raw_target.startswith("file://"):
        parsed = urllib.parse.urlparse(raw_target)
        raw_target = urllib.parse.unquote(parsed.path)

    title = ""
    # Check if raw_target is a local file path (.gdoc, .gsheet, .gslides, .gform, .desktop)
    if (
        os.path.isfile(raw_target)
        or raw_target.endswith((".gdoc", ".gsheet", ".gslides", ".gform", ".desktop"))
    ):
        raw_target, doc_type, title = _parse_local_doc_file(raw_target, fallback_type=doc_type)
    elif raw_target in ("doc", "document", "--home-doc", "--new-doc"):
        doc_type = "doc"
    elif raw_target in ("sheet", "spreadsheet", "--home-sheet", "--new-sheet"):
        doc_type = "sheet"
    elif raw_target in ("slide", "presentation", "--home-slide", "--new-slide"):
        doc_type = "slide"

    url = get_doc_url(raw_target, doc_type)
    wm_class, app_title, icon_file = _resolve_type_metadata(doc_type)
    browser = find_browser_for_app_mode()

    if browser:
        cmd = [
            browser,
            f"--app={url}",
            f"--class={wm_class}",
        ]
        print(f"[DocLauncher] Abrindo {app_title} em janela independente via {os.path.basename(browser)}: {url}")
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    # Fallback: Native GTK3 + WebKit2 standalone window if no Chromium browser is present
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        try:
            gi.require_version("WebKit2", "4.1")
        except ValueError:
            gi.require_version("WebKit2", "4.0")
        from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, WebKit2

        GLib.set_prgname(wm_class)
        GLib.set_application_name(app_title)
        Gdk.set_program_class(wm_class)

        win = Gtk.Window(title=f"{title} - {app_title}" if title else app_title)
        win.set_default_size(1200, 820)
        win.set_position(Gtk.WindowPosition.CENTER)
        icon_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icons", icon_file)
        if os.path.exists(icon_path):
            try:
                win.set_icon(GdkPixbuf.Pixbuf.new_from_file(icon_path))
            except Exception:
                pass
        webview = WebKit2.WebView()
        webview.load_uri(url)
        win.add(webview)
        win.connect("destroy", Gtk.main_quit)
        win.show_all()
        Gtk.main()
        return
    except Exception:
        pass

    subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        launch_doc_as_app("doc", "doc")
        sys.exit(0)

    arg1 = sys.argv[1]
    arg2 = sys.argv[2] if len(sys.argv) > 2 else "doc"
    # Allow calling `gdrive-doc-launcher --type=sheet /path/to/file.gsheet`
    if arg1.startswith("--type="):
        d_type = arg1.split("=", 1)[1]
        target = sys.argv[2] if len(sys.argv) > 2 else d_type
        launch_doc_as_app(target, d_type)
    else:
        launch_doc_as_app(arg1, arg2)
