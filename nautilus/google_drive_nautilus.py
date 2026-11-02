"""
Google Drive for Linux - GNOME Nautilus File Manager Extension
Adds context menu actions ("Copiar link", "Ver na Web", "Tornar disponível offline")
to Nautilus (GNOME Files) without UI redraw loops.
"""
import os
import urllib.parse
import sqlite3
import subprocess

try:
    from gi.repository import Nautilus, GObject
    HAS_NAUTILUS = True
except (ValueError, ImportError):
    HAS_NAUTILUS = False
    # Mock for testing in non-GNOME environments
    class GObject:
        class GObject:
            pass
    class Nautilus:
        class MenuProvider:
            pass
        class MenuItem:
            def __init__(self, **kwargs):
                pass
        class Menu:
            def append_item(self, item):
                pass

METADATA_DB_PATH = os.path.expanduser("~/.config/google-drive-linux/metadata.db")
CONFIG_PATH = os.path.expanduser("~/.config/google-drive-linux/config.json")
DEFAULT_MOUNT_POINT = os.path.expanduser("~/GoogleDrive")


def get_active_mount_points() -> list:
    """Read all active account mount points, supporting custom paths, multi-accounts, and external HDs."""
    mounts = set()
    if os.path.exists(CONFIG_PATH):
        try:
            import json
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                custom_mount = data.get("mount_point")
                if custom_mount:
                    mounts.add(os.path.abspath(os.path.expanduser(custom_mount)))
                acc_mounts = data.get("account_mounts") or {}
                for mp in acc_mounts.values():
                    if mp:
                        mounts.add(os.path.abspath(os.path.expanduser(mp)))
        except Exception:
            pass
    if not mounts:
        mounts.add(os.path.abspath(DEFAULT_MOUNT_POINT))
    return list(mounts)


class GoogleDriveExtension(GObject.GObject, Nautilus.MenuProvider):
    def __init__(self):
        super().__init__()

    def _is_gdrive_path(self, local_path: str) -> bool:
        mounts = get_active_mount_points()
        for mp in mounts:
            clean_mp = mp.rstrip("/")
            if local_path == clean_mp or local_path.startswith(clean_mp + "/"):
                return True
        return False

    def _get_item_status(self, file_path: str):
        if not os.path.exists(METADATA_DB_PATH):
            return None
        try:
            conn = sqlite3.connect(f"file:{METADATA_DB_PATH}?mode=ro", uri=True, timeout=1.0)
            c = conn.cursor()
            filename = os.path.basename(file_path)
            # Strip synthetic virtual workspace extensions (.gdoc, .gsheet, .gslides, .gform) if present
            for ext in (".gdoc", ".gsheet", ".gslides", ".gform"):
                if filename.endswith(ext):
                    filename = filename[:-len(ext)]
                    break
            c.execute("SELECT id, sync_status, is_pinned FROM items WHERE name = ? LIMIT 1", (filename,))
            row = c.fetchone()
            conn.close()
            return row
        except Exception:
            return None

    # --- MenuProvider: Right Click Context Menu ---
    def get_file_items(self, *args):
        # Support both nautilus 3 and nautilus 4 calling signatures
        files = args[-1]
        if not files or len(files) == 0:
            return []

        first_file = files[0]
        if first_file.get_uri_scheme() != "file":
            return []

        local_path = urllib.parse.unquote(first_file.get_uri()[7:])
        if not self._is_gdrive_path(local_path):
            return []

        top_menu_item = Nautilus.MenuItem(
            name="GoogleDrive::TopMenu",
            label="Google Drive",
            tip="Opções do Google Drive"
        )

        sub_menu = Nautilus.Menu()
        top_menu_item.set_submenu(sub_menu)

        # 1. Copy Link
        item_copy = Nautilus.MenuItem(
            name="GoogleDrive::CopyLink",
            label="Copiar link do Google Drive",
            tip="Copia o link para compartilhar"
        )
        item_copy.connect("activate", self._menu_copy_link, local_path)
        sub_menu.append_item(item_copy)

        # 2. View on Web
        item_web = Nautilus.MenuItem(
            name="GoogleDrive::ViewWeb",
            label="Ver no Google Drive na Web",
            tip="Abre o arquivo no navegador"
        )
        item_web.connect("activate", self._menu_open_web, local_path)
        sub_menu.append_item(item_web)

        # 3. Offline Pin
        item_pin = Nautilus.MenuItem(
            name="GoogleDrive::MakeOffline",
            label="Tornar disponível offline",
            tip="Mantém uma cópia física baixada"
        )
        item_pin.connect("activate", self._menu_pin_offline, local_path)
        sub_menu.append_item(item_pin)

        return [top_menu_item]

    def _menu_copy_link(self, menu, path):
        row = self._get_item_status(path)
        if row:
            item_id = row[0]
            link = f"https://drive.google.com/file/d/{item_id}/view?usp=sharing"
            try:
                subprocess.run(["xclip", "-selection", "clipboard"], input=link.encode("utf-8"), check=False)
            except Exception:
                pass
            try:
                subprocess.run(["wl-copy"], input=link.encode("utf-8"), check=False)
            except Exception:
                pass

    def _menu_open_web(self, menu, path):
        row = self._get_item_status(path)
        if row:
            item_id = row[0]
            link = f"https://drive.google.com/file/d/{item_id}/view"
            subprocess.Popen(["xdg-open", link])

    def _menu_pin_offline(self, menu, path):
        row = self._get_item_status(path)
        if row:
            item_id = row[0]
            if os.path.exists(METADATA_DB_PATH):
                try:
                    conn = sqlite3.connect(METADATA_DB_PATH, timeout=2.0)
                    conn.execute("UPDATE items SET is_pinned = 1 WHERE id = ?", (item_id,))
                    conn.commit()
                    conn.close()
                except Exception:
                    pass
