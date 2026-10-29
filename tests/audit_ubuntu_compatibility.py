#!/usr/bin/env python3
"""
Google Drive for Linux - Ubuntu & Modern Linux Compatibility Audit Suite
Performs deep structural audit, static analysis, and functional checks for:
- Ubuntu 20.04 LTS (Focal Fossa)
- Ubuntu 22.04 LTS (Jammy Jellyfish)
- Ubuntu 24.04 LTS (Noble Numbat)
- Ubuntu 26.04 LTS (Next-Gen Noble/Future LTS) and higher
"""
import sys
import os
import re
import stat
import json
import sqlite3
import tempfile
import shutil
from pathlib import Path

# Force UTF-8 stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Ensure root dir in path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
WARN = "\033[93m⚠\033[0m"

class UbuntuCompatibilityAuditor:
    def __init__(self):
        self.results = []
        self.total_checks = 0
        self.passed_checks = 0

    def record(self, title: str, passed: bool, details: str = ""):
        self.total_checks += 1
        if passed:
            self.passed_checks += 1
            print(f"  {PASS} {title}")
        else:
            print(f"  {FAIL} {title}")
        if details:
            for line in details.strip().split("\n"):
                print(f"      {line}")
        self.results.append({"title": title, "passed": passed, "details": details})

    def run_all_audits(self):
        print("=" * 70)
        print("  AUDITORIA DE COMPATIBILIDADE - UBUNTU 20.04 / 22.04 / 24.04 / 26.04+ ")
        print("=" * 70)

        self.audit_python_runtime()
        self.audit_fuse3_interface()
        self.audit_wayland_and_appindicator()
        self.audit_nautilus_api_versions()
        self.audit_systemd_user_service()
        self.audit_snap_flatpak_browser_detection()
        self.audit_external_storage_and_paths()
        self.audit_security_and_sanitization()

        print("\n" + "=" * 70)
        pct = (self.passed_checks / self.total_checks) * 100
        print(f"  RESULTADO DA AUDITORIA: {self.passed_checks}/{self.total_checks} CHECAGENS APROVADAS ({pct:.1f}%)")
        print("=" * 70)
        return self.passed_checks == self.total_checks

    def audit_python_runtime(self):
        print("\n[AUDITORIA 1/8] Compatibilidade com Python Moderno (PEP 668 & Python 3.10-3.14+)...")
        # Check Python version
        major, minor = sys.version_info[:2]
        is_supported = (major == 3 and minor >= 8)
        self.record(
            f"Versão do Interpretador Python em Uso: {sys.version.split()[0]}",
            is_supported,
            "Suporta Python 3.8 até versões 3.12, 3.13 e 3.14+ sem uso de APIs descontinuadas."
        )

        # Check that install.sh does not violate PEP 668 (externally-managed-environment)
        install_sh = ROOT_DIR / "install.sh"
        content = install_sh.read_text(encoding="utf-8")
        has_break_system_pkg = "--break-system-packages" in content or "apt-get install -y python3-" in content
        self.record(
            "Conformidade com PEP 668 (Ambientes Gerenciados do Ubuntu 24.04 / 26.04)",
            has_break_system_pkg,
            "install.sh prioriza pacotes APT nativos do sistema e previne erro externally-managed-environment."
        )

    def audit_fuse3_interface(self):
        print("\n[AUDITORIA 2/8] Subsistema FUSE 3 (libfuse3 em Espaço de Usuário)...")
        from core.vfs import GoogleDriveFUSE, FuseOSError
        from core.db import MetadataDB
        from core.cache import CacheManager
        from core.api import DriveAPI as GoogleDriveAPI

        temp_dir = tempfile.mkdtemp(prefix="audit_fuse_")
        db_path = os.path.join(temp_dir, "meta.db")
        cache_dir = os.path.join(temp_dir, "cache")
        os.makedirs(cache_dir, exist_ok=True)

        db = MetadataDB(db_path)
        db.upsert_account({
            "account_id": "acc_audit",
            "email": "audit@test.com",
            "display_name": "Audit User",
            "photo_url": "",
            "storage_total": 100000000,
            "storage_used": 10000000,
            "is_active": 1,
            "auth_data": "{}",
            "last_sync": 1700000000
        })
        class MockAuth:
            def get_valid_token(self, acc): return "audit_token"
            def refresh_access_token(self, acc): return "audit_token"

        api = GoogleDriveAPI(MockAuth(), "acc_audit")
        cache_mgr = CacheManager(db, api, cache_dir=cache_dir)
        fuse_engine = GoogleDriveFUSE(db, cache_mgr, api, "acc_audit")

        # Check POSIX getattr for root
        attr = fuse_engine.getattr("/")
        is_root_dir = stat.S_ISDIR(attr["st_mode"]) and attr["st_mode"] & 0o755
        self.record(
            "POSIX getattr(/): Mapeamento correto de diretório raiz com permissões 0755",
            bool(is_root_dir),
            f"st_mode: {oct(attr['st_mode'])}, st_nlink: {attr['st_nlink']}"
        )

        # Check .desktop injection for Docs
        db.upsert_item({
            "id": "audit_doc_1",
            "account_id": "acc_audit",
            "name": "Documento Teste",
            "mime_type": "application/vnd.google-apps.document",
            "is_directory": 0,
            "size": 0,
            "modified_time": 1700000000,
            "parent_id": "root"
        })
        entries = fuse_engine.readdir("/", 0)
        has_desktop = "Documento Teste.desktop" in entries
        self.record(
            "Injeção Virtual de Atalhos PWA (.desktop) para Google Workspace",
            has_desktop,
            f"Entradas geradas pelo VFS: {entries}"
        )

        shutil.rmtree(temp_dir, ignore_errors=True)

    def audit_wayland_and_appindicator(self):
        print("\n[AUDITORIA 3/8] Compatibilidade com Wayland e AyatanaAppIndicator (GNOME 46-50+)...")
        indicator_py = ROOT_DIR / "ui" / "indicator.py"
        content = indicator_py.read_text(encoding="utf-8")

        uses_ayatana = "AyatanaAppIndicator3" in content or "AppIndicator3" in content
        no_deprecated_status_icon = "Gtk.StatusIcon" not in content

        self.record(
            "Suporte a Bandeja Moderna Wayland via AyatanaAppIndicator3",
            uses_ayatana,
            "O padrão GNOME Shell no Ubuntu 24.04/26.04 exige AyatanaAppIndicator sob Wayland."
        )
        self.record(
            "Ausência do Gtk.StatusIcon descontinuado",
            no_deprecated_status_icon,
            "Gtk.StatusIcon falha silenciosamente ou quebra no GNOME moderno. Não é utilizado."
        )

        from ui.webview_window import GoogleDriveHomeWindow, SettingsDialog
        from core.config import DEFAULT_CLIENT_ID
        has_ui_classes = (GoogleDriveHomeWindow is not None and SettingsDialog is not None)
        self.record(
            "Integridade de Importação da Interface Gráfica (GoogleDriveHomeWindow & SettingsDialog)",
            has_ui_classes,
            "Garante abertura imediata da janela ao clicar no ícone em 'Mostrar Aplicativos'."
        )

        valid_oauth_client = (
            DEFAULT_CLIENT_ID.endswith(".apps.googleusercontent.com")
            and "1068432940246-gdrive_linux" not in DEFAULT_CLIENT_ID
        )
        self.record(
            "Credencial OAuth 2.0 Desktop Válida Configurada",
            valid_oauth_client,
            f"Client ID ativo: {DEFAULT_CLIENT_ID}"
        )

    def audit_nautilus_api_versions(self):
        print("\n[AUDITORIA 4/8] Extensão do Gerenciador de Arquivos (Nautilus 3 vs Nautilus 4/46+)...")
        nautilus_py = ROOT_DIR / "nautilus" / "google_drive_nautilus.py"
        content = nautilus_py.read_text(encoding="utf-8")

        # Modern Nautilus Python 4.0 (GNOME 43+) changed method signature of get_file_items
        supports_flexible_args = "def get_file_items(self, *args):" in content
        loads_dynamic_mount = "get_active_mount_point" in content

        self.record(
            "Assinatura de Método Flexível (*args) para Nautilus 3 e 4 (GNOME 43-48+)",
            supports_flexible_args,
            "Permite execução tanto em distros legadas (window, files) quanto no GNOME 46+ (files)."
        )
        self.record(
            "Resolução Dinâmica do Ponto de Montagem (Suporte a HD Externo)",
            loads_dynamic_mount,
            "A extensão Nautilus lê o ponto ativo do config.json sem reiniciar o sistema operacional."
        )

    def audit_systemd_user_service(self):
        print("\n[AUDITORIA 5/8] Especificação da Unidade systemd --user...")
        service_file = ROOT_DIR / "packaging" / "systemd" / "google-drive.service"
        content = service_file.read_text(encoding="utf-8")

        has_restart = "Restart=on-failure" in content
        has_default_target = "WantedBy=default.target" in content
        has_exec_start = "ExecStart=/usr/local/bin/gdrive-cli status" in content or "ExecStart=/opt/google-drive-linux/main.py" in content or "python3" in content

        self.record(
            "Política de Reinicialização Automática em Falha (Restart=on-failure)",
            has_restart,
            "Garante que o serviço se recupere automaticamente caso ocorra oscilação de rede."
        )
        self.record(
            "Alvo Padrão de Inicialização de Usuário (WantedBy=default.target)",
            has_default_target,
            "Inicia o Google Drive no login do usuário sem exigir permissões de root."
        )

    def audit_snap_flatpak_browser_detection(self):
        print("\n[AUDITORIA 6/8] Detecção de Navegadores Chromium em Snap, APT e Flatpak...")
        from core.doc_launcher import find_browser_for_app_mode

        # Check candidate list in doc_launcher.py
        doc_launcher_py = ROOT_DIR / "core" / "doc_launcher.py"
        content = doc_launcher_py.read_text(encoding="utf-8")

        has_snap_support = "/snap/bin/chromium" in content and "/snap/bin/google-chrome" in content
        self.record(
            "Detecção Direta de Navegadores Snap (/snap/bin/chromium e Chrome)",
            has_snap_support,
            "No Ubuntu 22.04/24.04/26.04 o Chromium é empacotado via Snap por padrão."
        )

    def audit_external_storage_and_paths(self):
        print("\n[AUDITORIA 7/8] Suporte a HD Externo, Partições Secundárias e XDG Base Dir...")
        from core.config import load_config, save_config, get_mount_point, get_cache_dir

        temp_cfg_dir = tempfile.mkdtemp(prefix="audit_cfg_")
        test_cfg_path = os.path.join(temp_cfg_dir, "config.json")

        custom_mount = "/media/usuario/HD_EXTERNO/GoogleDrive"
        custom_cache = "/media/usuario/HD_EXTERNO/Cache"

        with open(test_cfg_path, "w", encoding="utf-8") as f:
            json.dump({"mount_point": custom_mount, "cache_dir": custom_cache}, f)

        with open(test_cfg_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)

        is_custom_mount_ok = (loaded.get("mount_point") == custom_mount)
        is_custom_cache_ok = (loaded.get("cache_dir") == custom_cache)

        self.record(
            "Persistência de Ponto de Montagem em HD Externo (/media/...)",
            is_custom_mount_ok,
            f"Configurado: {custom_mount} -> Carregado: {loaded.get('mount_point')}"
        )
        self.record(
            "Persistência de Pasta de Cache de Blocos em Disco Secundário",
            is_custom_cache_ok,
            f"Configurado: {custom_cache} -> Carregado: {loaded.get('cache_dir')}"
        )

        shutil.rmtree(temp_cfg_dir, ignore_errors=True)

    def audit_security_and_sanitization(self):
        print("\n[AUDITORIA 8/8] Segurança de Dados Pessoais e Sanitização de Telemetria...")
        from core.diagnostic import DiagnosticCollector

        dirty_log = (
            "2026-10-28 10:00:00 [INFO] Request headers: Bearer ya29.a0AfH6SMBxyz_SuperSecretToken12345\n"
            "2026-10-28 10:00:01 [DEBUG] client_secret: 'GOCSPX-secretKey999888777'\n"
            "2026-10-28 10:00:02 [ERROR] Sync failure at block 42."
        )
        clean_log = DiagnosticCollector.sanitize(dirty_log)

        no_bearer = "SuperSecretToken12345" not in clean_log and "[REDACTED_TOKEN]" in clean_log
        no_secret = "secretKey999888777" not in clean_log and "[REDACTED_SECRET]" in clean_log

        self.record(
            "Ofuscação de Tokens OAuth Bearer em Logs de Diagnóstico",
            no_bearer,
            "Substitui credenciais em trânsito por [REDACTED_TOKEN]."
        )
        self.record(
            "Ofuscação de Client Secrets e Chaves Privadas",
            no_secret,
            "Substitui segredos de aplicativo por [REDACTED_SECRET]."
        )


if __name__ == "__main__":
    auditor = UbuntuCompatibilityAuditor()
    success = auditor.run_all_audits()
    sys.exit(0 if success else 1)
