#!/usr/bin/env python3
"""
Google Drive for Linux - Graphical Installation Setup Window
Allows the user to optionally choose the mount point folder in a native GTK window
during installation, with the default path pre-selected by default.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import (
    load_config,
    save_config,
    unmount_fuse_path,
    ensure_apparmor_allows_mount,
    DEFAULT_MOUNT_POINT,
)


def run_install_wizard() -> int:
    # In headless/CI environments without X11/Wayland display, keep default and exit cleanly
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        cfg = load_config()
        save_config(cfg)
        return 0

    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk, GdkPixbuf, GLib
        GLib.set_prgname("google-drive")
        GLib.set_application_name("Google Drive")
    except Exception:
        cfg = load_config()
        save_config(cfg)
        return 0

    cfg = load_config()
    current_mount = cfg.get("mount_point", DEFAULT_MOUNT_POINT)

    window = Gtk.Window(title="Instalação do Google Drive — Pasta de Montagem")
    window.set_default_size(620, 400)
    window.set_position(Gtk.WindowPosition.CENTER)
    window.set_resizable(False)

    icon_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", "icons", "drive_fs.png"
    )
    if os.path.exists(icon_path):
        try:
            window.set_icon(GdkPixbuf.Pixbuf.new_from_file(icon_path))
        except Exception:
            pass

    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    vbox.set_margin_top(22)
    vbox.set_margin_bottom(22)
    vbox.set_margin_start(24)
    vbox.set_margin_end(24)
    window.add(vbox)

    # Header with Drive logo + Title
    header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
    if os.path.exists(icon_path):
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_path, 48, 48, True)
            header_box.pack_start(Gtk.Image.new_from_pixbuf(pb), False, False, 0)
        except Exception:
            pass

    title_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    lbl_title = Gtk.Label()
    lbl_title.set_markup("<big><b>Configuração da Pasta do Google Drive</b></big>")
    lbl_title.set_alignment(0, 0.5)
    title_vbox.pack_start(lbl_title, False, False, 0)

    lbl_sub = Gtk.Label(
        label="O endereço padrão já vem pré-definido abaixo. Opcionalmente, você pode "
              "escolher outra pasta ou HD Externo para montar seu Google Drive."
    )
    lbl_sub.set_line_wrap(True)
    lbl_sub.set_max_width_chars(62)
    lbl_sub.set_opacity(0.8)
    lbl_sub.set_alignment(0, 0.5)
    title_vbox.pack_start(lbl_sub, False, False, 0)
    header_box.pack_start(title_vbox, True, True, 0)
    vbox.pack_start(header_box, False, False, 0)

    vbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)

    # Frame for Mount Point Options
    mount_frame = Gtk.Frame(label="Ponto de Montagem do Google Drive (Opcional)")
    mount_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    mount_box.set_margin_top(12)
    mount_box.set_margin_bottom(12)
    mount_box.set_margin_start(14)
    mount_box.set_margin_end(14)

    rb_default = Gtk.RadioButton.new_with_label_from_widget(
        None,
        f"Usar o endereço padrão pré-definido (Recomendado):\n   {DEFAULT_MOUNT_POINT}"
    )
    rb_custom = Gtk.RadioButton.new_with_label_from_widget(
        rb_default,
        "Escolher opcionalmente outra pasta de montagem (ex: HD Externo):"
    )

    custom_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    custom_hbox.set_margin_start(24)
    custom_entry = Gtk.Entry()
    custom_entry.set_hexpand(True)
    custom_entry.set_text(current_mount if current_mount != DEFAULT_MOUNT_POINT else DEFAULT_MOUNT_POINT)

    btn_browse = Gtk.Button(label="📁 Escolher Pasta...")

    def _on_browse(_):
        rb_custom.set_active(True)
        dlg = Gtk.FileChooserDialog(
            title="Selecione onde montar a pasta GoogleDrive",
            transient_for=window,
            action=Gtk.FileChooserAction.SELECT_FOLDER
        )
        dlg.add_buttons(
            "Cancelar", Gtk.ResponseType.CANCEL,
            "Selecionar Pasta", Gtk.ResponseType.OK
        )
        if dlg.run() == Gtk.ResponseType.OK:
            chosen = dlg.get_filename()
            if chosen:
                base = os.path.basename(chosen.rstrip("/")).lower()
                if base not in ("googledrive", "google drive", "google-drive"):
                    try:
                        if os.path.isdir(chosen) and os.listdir(chosen):
                            chosen = os.path.join(chosen, "GoogleDrive")
                    except OSError:
                        chosen = os.path.join(chosen, "GoogleDrive")
                custom_entry.set_text(chosen)
        dlg.destroy()

    btn_browse.connect("clicked", _on_browse)
    custom_entry.connect("changed", lambda _: rb_custom.set_active(True))

    if current_mount != DEFAULT_MOUNT_POINT:
        rb_custom.set_active(True)
    else:
        rb_default.set_active(True)

    custom_hbox.pack_start(custom_entry, True, True, 0)
    custom_hbox.pack_start(btn_browse, False, False, 0)

    mount_box.pack_start(rb_default, False, False, 0)
    mount_box.pack_start(rb_custom, False, False, 0)
    mount_box.pack_start(custom_hbox, False, False, 0)
    mount_frame.add(mount_box)
    vbox.pack_start(mount_frame, False, False, 0)

    # Autostart Checkbox (Enabled by default)
    autostart_cb = Gtk.CheckButton(
        label="Ativar inicialização automática no login do sistema (Ativado por padrão)"
    )
    autostart_cb.set_active(bool(cfg.get("autostart", True)))
    vbox.pack_start(autostart_cb, False, False, 2)

    # Action Buttons
    btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    btn_box.set_halign(Gtk.Align.END)

    def _apply_and_close(use_default_forced: bool = False):
        if use_default_forced or rb_default.get_active():
            chosen_mount = DEFAULT_MOUNT_POINT
        else:
            chosen_mount = custom_entry.get_text().strip() or DEFAULT_MOUNT_POINT

        cfg["mount_point"] = chosen_mount
        cfg["autostart"] = autostart_cb.get_active()
        cfg["start_at_login"] = autostart_cb.get_active()

        ensure_apparmor_allows_mount(chosen_mount)
        if chosen_mount != DEFAULT_MOUNT_POINT and os.path.exists(DEFAULT_MOUNT_POINT):
            unmount_fuse_path(DEFAULT_MOUNT_POINT, remove_if_empty=True)

        try:
            os.makedirs(chosen_mount, exist_ok=True)
            save_config(cfg)
            info = Gtk.MessageDialog(
                transient_for=window,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text="Configuração de Instalação Concluída!"
            )
            info.format_secondary_text(
                f"Pasta do Google Drive definida com sucesso em:\n{chosen_mount}\n\n"
                f"Inicialização automática no login: {'Ativada' if cfg['autostart'] else 'Desativada'}"
            )
            info.run()
            info.destroy()
            window.destroy()
            Gtk.main_quit()
        except Exception as e:
            err = Gtk.MessageDialog(
                transient_for=window,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="Não foi possível criar a pasta selecionada"
            )
            err.format_secondary_text(f"Erro ao criar '{chosen_mount}':\n{e}")
            err.run()
            err.destroy()

    btn_keep_default = Gtk.Button(label="Manter Endereço Padrão")
    btn_keep_default.connect("clicked", lambda _: _apply_and_close(use_default_forced=True))
    btn_box.pack_start(btn_keep_default, False, False, 0)

    btn_confirm = Gtk.Button(label="Confirmar e Concluir Instalação")
    btn_confirm.connect("clicked", lambda _: _apply_and_close(use_default_forced=False))
    btn_box.pack_start(btn_confirm, False, False, 0)

    vbox.pack_end(btn_box, False, False, 0)

    window.connect("destroy", lambda _: Gtk.main_quit())
    window.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(run_install_wizard())
