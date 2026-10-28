"""
Google Drive for Linux - GNOME Nautilus File Manager Extension
Adds sync status emblems (badges) and context menu actions to Nautilus (GNOME Files).
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
        class InfoProvider:
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


def get_active_mount_point() -> str:
    """Read the user's active mount point, supporting custom paths and external HDs."""
    if os.path.exists(CONFIG_PATH):
        try:
            import json
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                custom_mount = data.get("mount_point")
                if custom_mount:
                    return os.path.abspath(os.path.expanduser(custom_mount))
        except Exception:
            pass
    return DEFAULT_MOUNT_POINT


class GoogleDriveExtension(GObject.GObject, Nautilus.MenuProvider, Nautilus.InfoProvider):
    def __init__(self):
        super().__init__()

    @property
    def mount_point(self) -> str:
        return get_active_mount_point()

    def _get_item_status(self, file_path: str):
        if not os.path.exists(METADATA_DB_PATH):
            return None
        try:
            conn = sqlite3.connect(f"file:{METADATA_DB_PATH}?mode=ro", uri=True, timeout=2.0)
            c = conn.cursor()
            filename = os.path.basename(file_path)
            c.execute("SELECT id, sync_status, is_pinned FROM items WHERE name = ? LIMIT 1", (filename,))
            row = c.fetchone()
            conn.close()
            return row
        except Exception:
            return None

    # --- InfoProvider: Emblems / Badges ---
    def update_file_info(self, file):
        if not file.is_gone() and file.get_uri_scheme() == "file":
            local_path = urllib.parse.unquote(file.get_uri()[7:])
            if local_path.startswith(self.mount_point):
                row = self._get_item_status(local_path)
                if row:
                    status = row[1]
                    is_pinned = row[2]
                    if is_pinned or status == "pinned":
                        file.add_emblem("emblem-default") # Green Checkmark
                    elif status == "syncing":
                        file.add_emblem("emblem-synchronizing") # Blue sync arrows
                    elif status == "online_only":
                        file.add_emblem("emblem-web") # Cloud / Web icon

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
        if not local_path.startswith(self.mount_point):
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
            subprocess.run(["xclip", "-selection", "clipboard"], input=link.encode("utf-8"), check=False)

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
                conn = sqlite3.connect(METADATA_DB_PATH)
                conn.execute("UPDATE items SET is_pinned = 1 WHERE id = ?", (item_id,))
                conn.commit()
                conn.close()
