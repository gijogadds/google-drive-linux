"""
Google Drive for Linux - Standalone App Window Launcher for Google Docs, Sheets & Slides
Opens Google Docs, Sheets, and Slides in dedicated standalone app windows (PWA mode),
without browser URL bars or tabs, appearing on the Ubuntu dock as native desktop programs.
"""
import sys
import os
import shutil
import subprocess
from typing import Optional, List


def find_browser_for_app_mode() -> Optional[str]:
    """Find a Chromium-based browser supporting the --app flag in Linux."""
    candidates = [
        "google-chrome",
        "google-chrome-stable",
        "chromium-browser",
        "chromium",
        "brave-browser",
        "microsoft-edge-stable",
        "microsoft-edge"
    ]
    for b in candidates:
        path = shutil.which(b)
        if path:
            return path
    return None


def get_doc_url(identifier: str, doc_type: str = "doc") -> str:
    """Ensure identifier is turned into an edit URL."""
    if identifier.startswith("http://") or identifier.startswith("https://"):
        return identifier

    # Treat as file ID
    if doc_type in ("doc", "document"):
        return f"https://docs.google.com/document/d/{identifier}/edit"
    elif doc_type in ("sheet", "spreadsheet"):
        return f"https://docs.google.com/spreadsheets/d/{identifier}/edit"
    elif doc_type in ("slide", "presentation"):
        return f"https://docs.google.com/presentation/d/{identifier}/edit"
    elif doc_type in ("form",):
        return f"https://docs.google.com/forms/d/{identifier}/edit"
    else:
        return f"https://drive.google.com/open?id={identifier}"


def launch_doc_as_app(identifier: str, doc_type: str = "doc"):
    """Launch the Google Doc/Sheet/Slide in a dedicated standalone desktop window."""
    url = get_doc_url(identifier, doc_type)
    browser = find_browser_for_app_mode()

    wm_class = f"google-{doc_type}s" if not doc_type.endswith("s") else f"google-{doc_type}"

    if browser:
        # Launch in dedicated window without address bar, tabs or bookmarks
        cmd = [
            browser,
            f"--app={url}",
            f"--class={wm_class}",
            "--enable-features=RunAllTasksEnqueuedInWebEngineBeforeDestruction"
        ]
        print(f"[DocLauncher] Abrindo documento como aplicativo via {os.path.basename(browser)}:")
        print(f"              URL: {url}")
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        # Fallback to standard xdg-open if no Chromium-based browser is installed
        print(f"[DocLauncher] Navegador com suporte a modo app não encontrado. Abrindo com xdg-open.")
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: gdrive-doc-launcher <ID_DO_ARQUIVO_OU_URL> [doc|sheet|slide|form]")
        sys.exit(1)

    doc_id = sys.argv[1]
    d_type = sys.argv[2] if len(sys.argv) > 2 else "doc"
    launch_doc_as_app(doc_id, d_type)
