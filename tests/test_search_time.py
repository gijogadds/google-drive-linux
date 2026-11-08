import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui import webview_window


class SearchTimeTest(unittest.TestCase):
    def test_live_and_submitted_search_start_cloud_worker(self):
        for live_typing in (True, False):
            with self.subTest(live_typing=live_typing):
                db = MagicMock()
                db.get_accounts.return_value = []
                db.search_items.return_value = []
                window = webview_window.GoogleDriveHomeWindow({}, db)
                window.window = object()
                window.stack = MagicMock()
                window.search_title_lbl = MagicMock()
                window.search_local_list = MagicMock()
                window.search_cloud_list = MagicMock()
                window.search_local_list.get_children.return_value = []
                window.search_cloud_list.get_children.return_value = []
                window.lbl_search_local_hdr = MagicMock()
                window.lbl_search_cloud_hdr = MagicMock()

                glib = MagicMock()
                glib.markup_escape_text.side_effect = lambda text: text
                glib.idle_add.side_effect = lambda callback: callback()
                with patch.object(webview_window, "GLib", glib, create=True), \
                     patch.object(webview_window, "Gtk", MagicMock(), create=True), \
                     patch.object(webview_window.threading, "Thread") as thread:
                    if live_typing:
                        entry = MagicMock()
                        entry.get_text.return_value = " report "
                        window._on_search_changed(entry)
                    else:
                        window._on_search(" report ")

                    self.assertIsInstance(window._last_search_token, float)
                    db.search_items.assert_called_once_with("report", limit=60)
                    thread.return_value.start.assert_called_once_with()
                    thread.call_args.kwargs["target"]()
                    glib.idle_add.assert_called_once()


if __name__ == "__main__":
    unittest.main()
