"""
Google Drive for Linux - Advanced GTK3 UI Pages
Provides native graphical screens for:
1. Local Selective Sync Rules (TWO_WAY, UPLOAD_ONLY, DOWNLOAD_ONLY + Docs offline export)
2. Visual .gitignore Ignore Rules Editor with live path tester
3. One-Click Conflict Resolution Center & Conflict Policy Matrix
"""
import os
import shutil
import subprocess
import time
from typing import Dict, Any, Optional, List, Tuple

from core.ignore import IgnoreRulesEngine, DEFAULT_IGNORE_PATTERNS
from core.conflicts import (
    DEFAULT_CONFLICT_POLICIES,
    DEFAULT_MISSING_FILE_BEHAVIOR,
    make_conflict_copy_name,
    move_to_os_trash,
)
from core.config import save_config

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, GLib, Pango
    HAS_GTK = True
except Exception:
    HAS_GTK = False


CONFLICT_TYPE_LABELS: Dict[str, str] = {
    "LOCAL_EDIT_CLOUD_EDIT": "Editado localmente e na nuvem ao mesmo tempo",
    "LOCAL_EDIT_CLOUD_DELETE": "Editado localmente, mas excluído na nuvem",
    "LOCAL_EDIT_UNSYNC": "Editado localmente enquanto dessincronizado",
    "LOCAL_DELETE_CLOUD_EDIT": "Excluído localmente, mas editado na nuvem",
}

SYNC_MODE_LABELS: Dict[str, str] = {
    "TWO_WAY": "🔄 Bidirecional (Local ↔ Nuvem)",
    "UPLOAD_ONLY": "⬆️ Somente Upload (Local → Nuvem)",
    "DOWNLOAD_ONLY": "⬇️ Somente Download (Nuvem → Local)",
}

EXPORT_MODE_LABELS: Dict[str, str] = {
    "LINK": "🔗 Atalho Web (.gdoc / .gsheet / .gslides)",
    "OPEN_DOCUMENT": "📄 LibreOffice (.odt / .ods / .odp)",
    "MS_OFFICE": "📘 Microsoft Office (.docx / .xlsx / .pptx)",
}

POLICY_ACTION_LABELS: Dict[str, str] = {
    "KEEP_BOTH": "Manter ambos (criar cópia de conflito)",
    "KEEP_LOCAL": "Manter versão local",
    "KEEP_CLOUD": "Manter versão da nuvem",
    "KEEP_UNSYNC": "Manter dessincronizado",
    "ALWAYS_ASK": "Sempre perguntar",
}


def apply_conflict_resolution(
    db,
    conflict: Dict[str, Any],
    action: str,
    cache_manager=None,
) -> Tuple[bool, str]:
    """
    Execute a 1-click conflict resolution action ('KEEP_LOCAL', 'KEEP_CLOUD', 'KEEP_BOTH').
    Pure business logic safe to run both from GTK UI and unit tests.
    """
    action = (action or "").upper().strip()
    if action not in ("KEEP_LOCAL", "KEEP_CLOUD", "KEEP_BOTH"):
        return False, f"Ação de resolução inválida: {action}"

    conflict_id = conflict.get("id")
    account_id = conflict.get("account_id") or ""
    item_id = conflict.get("item_id") or ""
    local_path = conflict.get("local_path") or ""
    cloud_name = conflict.get("cloud_name") or os.path.basename(local_path) or item_id

    if action == "KEEP_LOCAL":
        if local_path and os.path.isfile(local_path):
            if item_id:
                db.queue_operation(account_id, "upload_modify", item_id=item_id, source_path=local_path)
            else:
                db.queue_operation(account_id, "upload_new", item_id=f"local_{int(time.time())}", source_path=local_path)
        db.resolve_conflict(conflict_id, "KEEP_LOCAL")
        db.log_activity(
            account_id,
            "conflict_resolved",
            item_id=item_id,
            item_name=cloud_name,
            details="Resolvido manualmente: Manter versão local",
        )
        return True, f"Versão local mantida e enfileirada para envio: {cloud_name}"

    if action == "KEEP_CLOUD":
        if cache_manager and item_id:
            try:
                cache_manager.invalidate_item_cache(item_id)
            except Exception:
                pass
        if local_path and os.path.exists(local_path):
            # Move conflicting local copy to OS Trash (never permanent unlink)
            move_to_os_trash(local_path)
        db.resolve_conflict(conflict_id, "KEEP_CLOUD")
        db.log_activity(
            account_id,
            "conflict_resolved",
            item_id=item_id,
            item_name=cloud_name,
            details="Resolvido manualmente: Manter versão da nuvem (cópia local enviada para a Lixeira)",
        )
        return True, f"Versão da nuvem mantida para: {cloud_name}"

    # KEEP_BOTH
    copy_path = ""
    if local_path and os.path.isfile(local_path):
        parent_dir = os.path.dirname(local_path)
        copy_name = make_conflict_copy_name(os.path.basename(local_path))
        copy_path = os.path.join(parent_dir, copy_name)
        try:
            shutil.copy2(local_path, copy_path)
            db.queue_operation(account_id, "upload_new", item_id=f"conflict_{int(time.time())}", source_path=copy_path)
        except Exception as e:
            return False, f"Falha ao criar cópia de conflito: {e}"
    if cache_manager and item_id:
        try:
            cache_manager.invalidate_item_cache(item_id)
        except Exception:
            pass
    db.resolve_conflict(conflict_id, "KEEP_BOTH")
    db.log_activity(
        account_id,
        "conflict_resolved",
        item_id=item_id,
        item_name=cloud_name,
        details=f"Resolvido manualmente: Manter ambos ({os.path.basename(copy_path) if copy_path else 'cópia'})",
    )
    msg = f"Ambas as versões preservadas: {os.path.basename(copy_path)}" if copy_path else f"Conflito marcado como 'Manter Ambos': {cloud_name}"
    return True, msg


def test_ignore_rules_in_memory(rules_text: str, rel_path: str, is_dir: bool = False) -> bool:
    """Evaluate a path against arbitrary gitignore text without touching disk."""
    engine = IgnoreRulesEngine.__new__(IgnoreRulesEngine)
    engine.rules_path = ""
    engine._compiled = []
    engine._mtime = 0.0
    engine._extra_patterns = []
    engine._compile_lines((rules_text or "").splitlines())
    # Bypass disk reload in is_ignored by calling the matcher directly via temp reload stub
    engine.reload = lambda force=False: None
    return engine.is_ignored(rel_path, is_dir=is_dir)


if HAS_GTK:

    class LocalSyncPage(Gtk.Box):
        """Manage hybrid real-folder sync rules (TWO_WAY, UPLOAD_ONLY, DOWNLOAD_ONLY)."""

        def __init__(self, db, config: Dict[str, Any], parent_window_getter=None):
            super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            self.db = db
            self.config = config
            self._get_parent = parent_window_getter or (lambda: None)

            self.set_margin_top(22)
            self.set_margin_bottom(22)
            self.set_margin_start(28)
            self.set_margin_end(28)

            # Header
            hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            lbl_title = Gtk.Label()
            lbl_title.set_markup("<big><big><b>📁 Pastas Locais (Sincronização Híbrida)</b></big></big>")
            lbl_title.set_alignment(0, 0.5)
            title_box.pack_start(lbl_title, False, False, 0)

            lbl_sub = Gtk.Label(
                label="Sincronize pastas reais do seu computador ou HD externo em paralelo com o Drive FUSE (Bidirecional, Somente Upload ou Somente Download)."
            )
            lbl_sub.set_alignment(0, 0.5)
            lbl_sub.set_line_wrap(True)
            lbl_sub.set_opacity(0.75)
            title_box.pack_start(lbl_sub, False, False, 0)
            hdr.pack_start(title_box, True, True, 0)

            btn_refresh = Gtk.Button(label="🔄 Atualizar")
            btn_refresh.set_valign(Gtk.Align.CENTER)
            btn_refresh.connect("clicked", lambda _: self.refresh())
            hdr.pack_start(btn_refresh, False, False, 0)

            btn_add = Gtk.Button(label="➕ Adicionar Pasta")
            btn_add.set_valign(Gtk.Align.CENTER)
            btn_add.connect("clicked", lambda _: self._on_add_rule())
            hdr.pack_start(btn_add, False, False, 0)
            self.pack_start(hdr, False, False, 0)

            self.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 2)

            # Scrolled list of rules
            self.scroll = Gtk.ScrolledWindow()
            self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            self.scroll.set_vexpand(True)

            self.list_box = Gtk.ListBox()
            self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
            self.scroll.add(self.list_box)
            self.pack_start(self.scroll, True, True, 0)

            # Empty state
            self.empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.empty_box.set_valign(Gtk.Align.CENTER)
            self.empty_box.set_vexpand(True)
            lbl_e1 = Gtk.Label()
            lbl_e1.set_markup("<big><b>Nenhuma pasta local configurada ainda</b></big>")
            lbl_e2 = Gtk.Label(
                label="Clique em '➕ Adicionar Pasta' para sincronizar projetos, documentos ou pastas de HD externo\n"
                      "com deduplicação MD5, filtro .gitignore e proteção contra desconexão."
            )
            lbl_e2.set_justify(Gtk.Justification.CENTER)
            lbl_e2.set_opacity(0.75)
            self.empty_box.pack_start(lbl_e1, False, False, 0)
            self.empty_box.pack_start(lbl_e2, False, False, 0)
            self.pack_start(self.empty_box, True, True, 0)

            self.refresh()

        def refresh(self):
            for child in self.list_box.get_children():
                self.list_box.remove(child)

            rules = self.db.get_local_sync_rules(enabled_only=False)
            accounts = {a.get("account_id"): a.get("email", a.get("account_id")) for a in self.db.get_accounts()}

            if not rules:
                self.scroll.hide()
                self.empty_box.show_all()
                return

            self.empty_box.hide()
            self.scroll.show()

            for r in rules:
                frame = Gtk.Frame()
                frame.set_margin_top(4)
                frame.set_margin_bottom(4)

                card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                card.set_margin_top(10)
                card.set_margin_bottom(10)
                card.set_margin_start(14)
                card.set_margin_end(14)

                info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                acc_email = accounts.get(r.get("account_id"), r.get("account_id", ""))
                local_path = r.get("local_path", "")
                cloud_id = r.get("cloud_folder_id", "root")
                cloud_name = "Meu Drive (Raiz)"
                if cloud_id and cloud_id != "root":
                    item = self.db.get_item(cloud_id)
                    cloud_name = item.get("name", cloud_id) if item else cloud_id

                mode_lbl = SYNC_MODE_LABELS.get(r.get("sync_mode", "TWO_WAY"), r.get("sync_mode", "TWO_WAY"))
                exp_lbl = EXPORT_MODE_LABELS.get(r.get("export_mode", "LINK"), r.get("export_mode", "LINK"))
                exists_ok = os.path.isdir(local_path)
                status_badge = "✅ Pasta ativa" if exists_ok else "⚠️ Pasta desconectada/ausente (protegida)"

                lbl_path = Gtk.Label()
                lbl_path.set_markup(f"<b>📂 {GLib.markup_escape_text(local_path)}</b>")
                lbl_path.set_alignment(0, 0.5)
                lbl_path.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                info.pack_start(lbl_path, False, False, 0)

                lbl_meta = Gtk.Label()
                lbl_meta.set_markup(
                    f"<small>👤 {GLib.markup_escape_text(acc_email)}  •  ☁️ Destino: <b>{GLib.markup_escape_text(cloud_name)}</b>  •  {GLib.markup_escape_text(status_badge)}</small>"
                )
                lbl_meta.set_alignment(0, 0.5)
                lbl_meta.set_opacity(0.85)
                info.pack_start(lbl_meta, False, False, 0)

                lbl_modes = Gtk.Label()
                lbl_modes.set_markup(
                    f"<small>{GLib.markup_escape_text(mode_lbl)}  •  Docs offline: {GLib.markup_escape_text(exp_lbl)}</small>"
                )
                lbl_modes.set_alignment(0, 0.5)
                lbl_modes.set_opacity(0.75)
                info.pack_start(lbl_modes, False, False, 0)

                card.pack_start(info, True, True, 0)

                # Right controls
                ctrl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                ctrl.set_valign(Gtk.Align.CENTER)

                sw = Gtk.Switch()
                sw.set_active(bool(r.get("enabled", 1)))
                sw.set_tooltip_text("Ativar ou pausar esta regra de sincronização")
                sw.connect("state-set", lambda _w, state, rule=r: self._on_toggle_rule(rule, state))
                ctrl.pack_start(sw, False, False, 0)

                btn_open = Gtk.Button(label="📂")
                btn_open.set_tooltip_text("Abrir pasta local")
                btn_open.connect("clicked", lambda _, p=local_path: subprocess.Popen(["xdg-open", p]) if os.path.exists(p) else None)
                ctrl.pack_start(btn_open, False, False, 0)

                btn_del = Gtk.Button(label="🗑️")
                btn_del.set_tooltip_text("Remover regra (não apaga seus arquivos)")
                btn_del.connect("clicked", lambda _, p=local_path: self._on_delete_rule(p))
                ctrl.pack_start(btn_del, False, False, 0)

                card.pack_start(ctrl, False, False, 0)
                frame.add(card)
                self.list_box.add(frame)

            self.list_box.show_all()

        def _on_toggle_rule(self, rule: Dict[str, Any], state: bool):
            self.db.upsert_local_sync_rule(
                account_id=rule["account_id"],
                local_path=rule["local_path"],
                cloud_folder_id=rule.get("cloud_folder_id", "root"),
                sync_mode=rule.get("sync_mode", "TWO_WAY"),
                export_mode=rule.get("export_mode", "LINK"),
                enabled=bool(state),
            )
            return False

        def _on_delete_rule(self, local_path: str):
            dlg = Gtk.MessageDialog(
                transient_for=self._get_parent(),
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.YES_NO,
                text="Remover regra de sincronização local?",
            )
            dlg.format_secondary_text(
                f"Pasta: {local_path}\n\nSeus arquivos locais e da nuvem NÃO serão apagados."
            )
            if dlg.run() == Gtk.ResponseType.YES:
                self.db.delete_local_sync_rule(local_path)
                self.refresh()
            dlg.destroy()

        def _on_add_rule(self):
            accounts = self.db.get_accounts()
            if not accounts:
                err = Gtk.MessageDialog(
                    transient_for=self._get_parent(),
                    message_type=Gtk.MessageType.WARNING,
                    buttons=Gtk.ButtonsType.OK,
                    text="Conecte uma Conta Google primeiro antes de adicionar pastas locais.",
                )
                err.run()
                err.destroy()
                return

            dlg = Gtk.Dialog(
                title="Adicionar Pasta Local (Sincronização Híbrida)",
                transient_for=self._get_parent(),
                flags=0,
            )
            dlg.add_buttons("Cancelar", Gtk.ResponseType.CANCEL, "Salvar Regra", Gtk.ResponseType.OK)
            dlg.set_default_size(540, 320)
            area = dlg.get_content_area()
            area.set_spacing(10)
            area.set_margin_top(16)
            area.set_margin_bottom(16)
            area.set_margin_start(18)
            area.set_margin_end(18)

            grid = Gtk.Grid(column_spacing=10, row_spacing=10)
            area.pack_start(grid, True, True, 0)

            # 1. Account selector
            lbl_acc = Gtk.Label(label="Conta Google:")
            lbl_acc.set_alignment(0, 0.5)
            cb_acc = Gtk.ComboBoxText()
            for a in accounts:
                cb_acc.append(a["account_id"], a.get("email", a["account_id"]))
            cb_acc.set_active(0)
            grid.attach(lbl_acc, 0, 0, 1, 1)
            grid.attach(cb_acc, 1, 0, 2, 1)

            # 2. Local folder picker
            lbl_loc = Gtk.Label(label="Pasta Local:")
            lbl_loc.set_alignment(0, 0.5)
            ent_loc = Gtk.Entry()
            ent_loc.set_hexpand(True)
            ent_loc.set_placeholder_text("/home/usuario/Documentos/Projeto")
            btn_browse = Gtk.Button(label="Procurar...")

            def _browse(_):
                fc = Gtk.FileChooserDialog(
                    title="Selecionar Pasta Local",
                    transient_for=dlg,
                    action=Gtk.FileChooserAction.SELECT_FOLDER,
                )
                fc.add_buttons("Cancelar", Gtk.ResponseType.CANCEL, "Selecionar", Gtk.ResponseType.OK)
                if fc.run() == Gtk.ResponseType.OK:
                    chosen = fc.get_filename()
                    if chosen:
                        ent_loc.set_text(chosen)
                fc.destroy()

            btn_browse.connect("clicked", _browse)
            grid.attach(lbl_loc, 0, 1, 1, 1)
            grid.attach(ent_loc, 1, 1, 1, 1)
            grid.attach(btn_browse, 2, 1, 1, 1)

            # 3. Cloud destination folder
            lbl_cloud = Gtk.Label(label="Pasta no Drive:")
            lbl_cloud.set_alignment(0, 0.5)
            cb_cloud = Gtk.ComboBoxText()

            def _populate_cloud_folders(*_):
                cb_cloud.remove_all()
                cb_cloud.append("root", "☁️ Meu Drive (Raiz)")
                aid = cb_acc.get_active_id()
                if aid:
                    try:
                        with self.db._get_connection() as conn:
                            rows = conn.execute(
                                """
                                SELECT id, name FROM items
                                WHERE account_id = ? AND is_directory = 1 AND is_trashed = 0
                                ORDER BY name ASC LIMIT 120
                                """,
                                (aid,),
                            ).fetchall()
                            for row in rows:
                                cb_cloud.append(row["id"], f"📁 {row['name']}")
                    except Exception:
                        pass
                cb_cloud.set_active(0)

            _populate_cloud_folders()
            cb_acc.connect("changed", _populate_cloud_folders)
            grid.attach(lbl_cloud, 0, 2, 1, 1)
            grid.attach(cb_cloud, 1, 2, 2, 1)

            # 4. Sync mode
            lbl_mode = Gtk.Label(label="Modo de Sincronia:")
            lbl_mode.set_alignment(0, 0.5)
            cb_mode = Gtk.ComboBoxText()
            for k, v in SYNC_MODE_LABELS.items():
                cb_mode.append(k, v)
            cb_mode.set_active(0)
            grid.attach(lbl_mode, 0, 3, 1, 1)
            grid.attach(cb_mode, 1, 3, 2, 1)

            # 5. Offline Docs Export Mode
            lbl_exp = Gtk.Label(label="Google Docs Offline:")
            lbl_exp.set_alignment(0, 0.5)
            cb_exp = Gtk.ComboBoxText()
            for k, v in EXPORT_MODE_LABELS.items():
                cb_exp.append(k, v)
            default_exp = self.config.get("docs_export_mode", "LINK")
            cb_exp.set_active_id(default_exp if default_exp in EXPORT_MODE_LABELS else "LINK")
            grid.attach(lbl_exp, 0, 4, 1, 1)
            grid.attach(cb_exp, 1, 4, 2, 1)

            dlg.show_all()
            resp = dlg.run()
            if resp == Gtk.ResponseType.OK:
                local_path = ent_loc.get_text().strip()
                acc_id = cb_acc.get_active_id() or accounts[0]["account_id"]
                cloud_id = cb_cloud.get_active_id() or "root"
                mode = cb_mode.get_active_id() or "TWO_WAY"
                exp_mode = cb_exp.get_active_id() or "LINK"
                if local_path:
                    os.makedirs(os.path.expanduser(local_path), exist_ok=True)
                    self.db.upsert_local_sync_rule(
                        account_id=acc_id,
                        local_path=local_path,
                        cloud_folder_id=cloud_id,
                        sync_mode=mode,
                        export_mode=exp_mode,
                        enabled=True,
                    )
                    self.refresh()
            dlg.destroy()


    class IgnoreRulesPage(Gtk.Box):
        """Visual .gitignore editor with quick presets and real-time path tester."""

        def __init__(self, parent_window_getter=None):
            super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            self._get_parent = parent_window_getter or (lambda: None)
            self.engine = IgnoreRulesEngine()

            self.set_margin_top(22)
            self.set_margin_bottom(22)
            self.set_margin_start(28)
            self.set_margin_end(28)

            # Header
            hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            lbl_title = Gtk.Label()
            lbl_title.set_markup("<big><big><b>🚫 Regras de Exclusão (.gitignore)</b></big></big>")
            lbl_title.set_alignment(0, 0.5)
            title_box.pack_start(lbl_title, False, False, 0)

            lbl_sub = Gtk.Label(
                label=f"Arquivo ativo: {self.engine.rules_path}  •  Sintaxe compatível com .gitignore (*, **, /, !negação)."
            )
            lbl_sub.set_alignment(0, 0.5)
            lbl_sub.set_opacity(0.75)
            lbl_sub.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            title_box.pack_start(lbl_sub, False, False, 0)
            hdr.pack_start(title_box, True, True, 0)

            btn_save = Gtk.Button(label="💾 Salvar Regras")
            btn_save.set_valign(Gtk.Align.CENTER)
            btn_save.connect("clicked", lambda _: self._on_save())
            hdr.pack_start(btn_save, False, False, 0)
            self.pack_start(hdr, False, False, 0)

            # Quick presets bar
            preset_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            lbl_p = Gtk.Label(label="Atalhos rápidos:")
            lbl_p.set_opacity(0.8)
            preset_bar.pack_start(lbl_p, False, False, 0)

            for label, snippet in [
                ("+ node_modules/", "node_modules/"),
                ("+ .git/", ".git/"),
                ("+ *.log", "*.log"),
                ("+ build/ & dist/", "build/\ndist/"),
                ("+ .venv/", ".venv/"),
            ]:
                b = Gtk.Button(label=label)
                b.connect("clicked", lambda _, s=snippet: self._append_pattern(s))
                preset_bar.pack_start(b, False, False, 0)

            btn_reset = Gtk.Button(label="↺ Restaurar Padrões")
            btn_reset.connect("clicked", lambda _: self._on_reset_defaults())
            preset_bar.pack_end(btn_reset, False, False, 0)
            self.pack_start(preset_bar, False, False, 0)

            # TextView editor
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scroll.set_shadow_type(Gtk.ShadowType.IN)
            scroll.set_vexpand(True)

            self.text_view = Gtk.TextView()
            self.text_view.set_monospace(True)
            self.text_view.set_left_margin(10)
            self.text_view.set_right_margin(10)
            self.text_view.set_top_margin(8)
            self.text_view.set_bottom_margin(8)
            self.buffer = self.text_view.get_buffer()
            self.buffer.set_text(self.engine.get_rules_text())
            self.buffer.connect("changed", lambda _: self._update_live_test())
            scroll.add(self.text_view)
            self.pack_start(scroll, True, True, 0)

            # Status label
            self.status_lbl = Gtk.Label(label="")
            self.status_lbl.set_alignment(0, 0.5)
            self.pack_start(self.status_lbl, False, False, 0)

            # Live Tester Frame
            test_frame = Gtk.Frame(label="🧪 Testador de Regras em Tempo Real")
            test_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            test_box.set_margin_top(8)
            test_box.set_margin_bottom(8)
            test_box.set_margin_start(10)
            test_box.set_margin_end(10)

            self.test_entry = Gtk.Entry()
            self.test_entry.set_placeholder_text("Digite um caminho relativo para testar (ex: app/node_modules/index.js ou nota.tmp)")
            self.test_entry.set_hexpand(True)
            self.test_entry.connect("changed", lambda _: self._update_live_test())
            test_box.pack_start(self.test_entry, True, True, 0)

            self.test_isdir_cb = Gtk.CheckButton(label="É pasta")
            self.test_isdir_cb.connect("toggled", lambda _: self._update_live_test())
            test_box.pack_start(self.test_isdir_cb, False, False, 0)

            self.test_result_lbl = Gtk.Label(label="Digite um caminho acima")
            self.test_result_lbl.set_size_request(190, -1)
            self.test_result_lbl.set_alignment(0, 0.5)
            test_box.pack_start(self.test_result_lbl, False, False, 0)

            test_frame.add(test_box)
            self.pack_start(test_frame, False, False, 0)

        def _get_editor_text(self) -> str:
            start, end = self.buffer.get_bounds()
            return self.buffer.get_text(start, end, True)

        def _append_pattern(self, snippet: str):
            text = self._get_editor_text()
            existing = {line.strip() for line in text.splitlines()}
            to_add = [line for line in snippet.splitlines() if line.strip() not in existing]
            if not to_add:
                self.status_lbl.set_markup("<small>ℹ️ Regra já presente no editor.</small>")
                return
            prefix = "" if (not text or text.endswith("\n")) else "\n"
            self.buffer.set_text(text + prefix + "\n".join(to_add) + "\n")
            self.status_lbl.set_markup("<small>✏️ Adicionado ao editor. Clique em <b>Salvar Regras</b> para aplicar.</small>")

        def _on_reset_defaults(self):
            header = (
                "# Google Drive for Linux - Regras de Exclusão (sintaxe .gitignore)\n"
                "# Linhas iniciadas com # são comentários. Use '!' para negar uma regra.\n\n"
            )
            self.buffer.set_text(header + "\n".join(DEFAULT_IGNORE_PATTERNS) + "\n")
            self.status_lbl.set_markup("<small>↺ Padrões restaurados no editor. Clique em <b>Salvar Regras</b> para aplicar.</small>")

        def _on_save(self):
            text = self._get_editor_text()
            if self.engine.save_rules_text(text):
                self.status_lbl.set_markup("<span foreground='#2e7d32'><b>✅ Regras salvas e aplicadas imediatamente!</b></span>")
            else:
                self.status_lbl.set_markup("<span foreground='#c62828'><b>❌ Erro ao salvar arquivo de regras.</b></span>")

        def _update_live_test(self):
            sample = self.test_entry.get_text().strip()
            if not sample:
                self.test_result_lbl.set_markup("<small>Digite um caminho acima</small>")
                return
            ignored = test_ignore_rules_in_memory(
                self._get_editor_text(),
                sample,
                is_dir=self.test_isdir_cb.get_active(),
            )
            if ignored:
                self.test_result_lbl.set_markup("<span foreground='#e65100'><b>🚫 SERÁ IGNORADO</b></span>")
            else:
                self.test_result_lbl.set_markup("<span foreground='#2e7d32'><b>✅ SERÁ SINCRONIZADO</b></span>")


    class ConflictsPage(Gtk.Box):
        """One-click Conflict Resolution Center + automatic conflict policy matrix."""

        def __init__(self, db, config: Dict[str, Any], cache_manager=None, parent_window_getter=None, on_resolved=None):
            super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            self.db = db
            self.config = config
            self.cache_manager = cache_manager
            self._get_parent = parent_window_getter or (lambda: None)
            self.on_resolved = on_resolved

            self.set_margin_top(22)
            self.set_margin_bottom(22)
            self.set_margin_start(28)
            self.set_margin_end(28)

            # Header
            hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            lbl_title = Gtk.Label()
            lbl_title.set_markup("<big><big><b>⚠️ Central de Conflitos</b></big></big>")
            lbl_title.set_alignment(0, 0.5)
            title_box.pack_start(lbl_title, False, False, 0)

            lbl_sub = Gtk.Label(
                label="Resolva conflitos de sincronização com 1 clique ou defina as políticas automáticas para os 4 cenários."
            )
            lbl_sub.set_alignment(0, 0.5)
            lbl_sub.set_opacity(0.75)
            title_box.pack_start(lbl_sub, False, False, 0)
            hdr.pack_start(title_box, True, True, 0)

            btn_refresh = Gtk.Button(label="🔄 Atualizar")
            btn_refresh.set_valign(Gtk.Align.CENTER)
            btn_refresh.connect("clicked", lambda _: self.refresh())
            hdr.pack_start(btn_refresh, False, False, 0)
            self.pack_start(hdr, False, False, 0)

            self.feedback_lbl = Gtk.Label(label="")
            self.feedback_lbl.set_alignment(0, 0.5)
            self.pack_start(self.feedback_lbl, False, False, 0)

            # Pending Conflicts List
            self.scroll = Gtk.ScrolledWindow()
            self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            self.scroll.set_vexpand(True)

            self.list_box = Gtk.ListBox()
            self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
            self.scroll.add(self.list_box)
            self.pack_start(self.scroll, True, True, 0)

            # Empty state
            self.empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            self.empty_box.set_valign(Gtk.Align.CENTER)
            self.empty_box.set_vexpand(True)
            lbl_ok = Gtk.Label()
            lbl_ok.set_markup("<big><b>✅ Nenhum conflito pendente!</b></big>")
            lbl_ok2 = Gtk.Label(label="Todos os seus arquivos locais e da nuvem estão em harmonia.")
            lbl_ok2.set_opacity(0.75)
            self.empty_box.pack_start(lbl_ok, False, False, 0)
            self.empty_box.pack_start(lbl_ok2, False, False, 0)
            self.pack_start(self.empty_box, True, True, 0)

            # Policies Matrix Frame
            pol_frame = Gtk.Frame(label="⚙️ Políticas Automáticas de Conflito e Proteção")
            pol_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            pol_vbox.set_margin_top(10)
            pol_vbox.set_margin_bottom(10)
            pol_vbox.set_margin_start(12)
            pol_vbox.set_margin_end(12)

            grid = Gtk.Grid(column_spacing=12, row_spacing=6)
            saved_policies = dict(DEFAULT_CONFLICT_POLICIES)
            saved_policies.update(self.config.get("conflict_policies") or {})

            self.policy_combos: Dict[str, Gtk.ComboBoxText] = {}
            policy_options = {
                "LOCAL_EDIT_CLOUD_EDIT": ["KEEP_BOTH", "KEEP_LOCAL", "KEEP_CLOUD", "ALWAYS_ASK"],
                "LOCAL_EDIT_CLOUD_DELETE": ["KEEP_LOCAL", "KEEP_CLOUD", "ALWAYS_ASK"],
                "LOCAL_EDIT_UNSYNC": ["KEEP_LOCAL", "KEEP_UNSYNC", "ALWAYS_ASK"],
                "LOCAL_DELETE_CLOUD_EDIT": ["KEEP_CLOUD", "KEEP_LOCAL", "ALWAYS_ASK"],
            }
            for row_idx, (ctype, opts) in enumerate(policy_options.items()):
                lbl = Gtk.Label(label=CONFLICT_TYPE_LABELS[ctype] + ":")
                lbl.set_alignment(0, 0.5)
                cb = Gtk.ComboBoxText()
                for opt in opts:
                    cb.append(opt, POLICY_ACTION_LABELS.get(opt, opt))
                cur = saved_policies.get(ctype, opts[0])
                cb.set_active_id(cur if cur in opts else opts[0])
                cb.set_hexpand(True)
                grid.attach(lbl, 0, row_idx, 1, 1)
                grid.attach(cb, 1, row_idx, 1, 1)
                self.policy_combos[ctype] = cb

            # Missing file behavior (External HD protection)
            lbl_miss = Gtk.Label(label="HD externo / pasta desconectada:")
            lbl_miss.set_alignment(0, 0.5)
            self.cb_missing = Gtk.ComboBoxText()
            self.cb_missing.append("UNSYNC", "🛡️ Seguro — Apenas dessincronizar (nunca apagar da nuvem)")
            self.cb_missing.append("DELETE", "⚠️ Excluir da nuvem (somente se souber o que faz)")
            cur_miss = self.config.get("missing_file_behavior", DEFAULT_MISSING_FILE_BEHAVIOR)
            self.cb_missing.set_active_id(cur_miss if cur_miss in ("UNSYNC", "DELETE") else "UNSYNC")
            grid.attach(lbl_miss, 0, 4, 1, 1)
            grid.attach(self.cb_missing, 1, 4, 1, 1)

            pol_vbox.pack_start(grid, False, False, 0)

            btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            btn_save_pol = Gtk.Button(label="💾 Salvar Políticas")
            btn_save_pol.connect("clicked", lambda _: self._on_save_policies())
            btn_row.pack_end(btn_save_pol, False, False, 0)
            pol_vbox.pack_start(btn_row, False, False, 2)

            pol_frame.add(pol_vbox)
            self.pack_start(pol_frame, False, False, 0)

            self.refresh()

        def refresh(self):
            for child in self.list_box.get_children():
                self.list_box.remove(child)

            conflicts = self.db.get_pending_conflicts()
            if not conflicts:
                self.scroll.hide()
                self.empty_box.show_all()
                return

            self.empty_box.hide()
            self.scroll.show()

            for c in conflicts:
                frame = Gtk.Frame()
                frame.set_margin_top(4)
                frame.set_margin_bottom(4)

                vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                vbox.set_margin_top(10)
                vbox.set_margin_bottom(10)
                vbox.set_margin_start(14)
                vbox.set_margin_end(14)

                ctype = c.get("conflict_type", "")
                ctype_desc = CONFLICT_TYPE_LABELS.get(ctype, ctype)
                name = c.get("cloud_name") or os.path.basename(c.get("local_path") or "") or c.get("item_id", "")
                created = time.strftime("%d/%m/%Y %H:%M", time.localtime(c.get("created_at", time.time())))

                lbl_head = Gtk.Label()
                lbl_head.set_markup(
                    f"<b>⚠️ {GLib.markup_escape_text(name)}</b>  —  <small>{GLib.markup_escape_text(ctype_desc)} ({created})</small>"
                )
                lbl_head.set_alignment(0, 0.5)
                lbl_head.set_line_wrap(True)
                vbox.pack_start(lbl_head, False, False, 0)

                if c.get("local_path"):
                    lbl_p = Gtk.Label()
                    lbl_p.set_markup(f"<small>📍 {GLib.markup_escape_text(c['local_path'])}</small>")
                    lbl_p.set_alignment(0, 0.5)
                    lbl_p.set_opacity(0.8)
                    lbl_p.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                    vbox.pack_start(lbl_p, False, False, 0)

                btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                for lbl_btn, act in [
                    ("💻 Manter Local", "KEEP_LOCAL"),
                    ("☁️ Manter Nuvem", "KEEP_CLOUD"),
                    ("📑 Manter Ambos", "KEEP_BOTH"),
                ]:
                    b = Gtk.Button(label=lbl_btn)
                    b.connect("clicked", lambda _, cf=c, a=act: self._on_resolve_click(cf, a))
                    btn_bar.pack_start(b, False, False, 0)
                vbox.pack_start(btn_bar, False, False, 2)

                frame.add(vbox)
                self.list_box.add(frame)

            self.list_box.show_all()

        def _on_resolve_click(self, conflict: Dict[str, Any], action: str):
            ok, msg = apply_conflict_resolution(self.db, conflict, action, cache_manager=self.cache_manager)
            color = "#2e7d32" if ok else "#c62828"
            prefix = "✅" if ok else "❌"
            self.feedback_lbl.set_markup(f"<span foreground='{color}'><b>{prefix} {GLib.markup_escape_text(msg)}</b></span>")
            self.refresh()
            if self.on_resolved:
                try:
                    self.on_resolved()
                except Exception:
                    pass

        def _on_save_policies(self):
            new_policies = {}
            for ctype, cb in self.policy_combos.items():
                new_policies[ctype] = cb.get_active_id() or DEFAULT_CONFLICT_POLICIES[ctype]
            self.config["conflict_policies"] = new_policies
            self.config["missing_file_behavior"] = self.cb_missing.get_active_id() or DEFAULT_MISSING_FILE_BEHAVIOR
            save_config(self.config)
            self.feedback_lbl.set_markup("<span foreground='#2e7d32'><b>✅ Políticas de conflito e proteção salvas!</b></span>")
