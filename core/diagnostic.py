"""
Google Drive for Linux - Automated Diagnostic & Crash Reporting System
Replicates Google Drive's diagnostic_tool.exe & Crashpad for automated issue tracking and resolution.
Collects sanitized system diagnostics, logs, and creates GitHub issues automatically.
"""
import sys
import os
import platform
import traceback
import subprocess
import json
import urllib.request
import urllib.parse
import shutil
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

from core.config import CONFIG_DIR, LOG_DIR, METADATA_DB_PATH, ensure_directories
from core.db import MetadataDB

GITHUB_REPO = "gijogadds/google-drive-linux"
GITHUB_ISSUES_API = f"https://api.github.com/repos/{GITHUB_REPO}/issues"
CRASH_REPORT_DIR = os.path.join(CONFIG_DIR, "crash_reports")


class DiagnosticCollector:
    @staticmethod
    def collect_system_info() -> Dict[str, Any]:
        """Collect environment, OS, kernel, desktop environment and dependency status."""
        info = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "os_name": platform.system(),
            "os_release": platform.release(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
            "desktop_env": os.environ.get("XDG_CURRENT_DESKTOP", "Desconhecido"),
            "session_type": os.environ.get("XDG_SESSION_TYPE", "Desconhecido"),
        }

        # Read /etc/os-release if on Linux
        if os.path.exists("/etc/os-release"):
            try:
                with open("/etc/os-release", "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("PRETTY_NAME="):
                            info["linux_distro"] = line.split("=", 1)[1].strip().strip('"')
            except Exception:
                pass

        # Check installed dependencies
        for tool in ["fuse3", "nautilus", "google-chrome", "chromium", "google-chrome-stable"]:
            info[f"tool_{tool}"] = "Instalado" if shutil.which(tool) else "Não encontrado"

        return info

    @staticmethod
    def collect_app_state() -> Dict[str, Any]:
        """Collect sanitized database and sync state."""
        state = {
            "has_db": os.path.exists(METADATA_DB_PATH),
            "accounts_count": 0,
            "pending_operations": 0
        }
        if os.path.exists(METADATA_DB_PATH):
            try:
                db = MetadataDB()
                accs = db.get_accounts()
                state["accounts_count"] = len(accs)
                ops = db.get_pending_operations(limit=100)
                state["pending_operations"] = len(ops)
            except Exception as e:
                state["db_error"] = str(e)
        return state

    @staticmethod
    def get_recent_logs(max_lines: int = 50) -> str:
        """Fetch last lines of application logs."""
        main_log = os.path.join(LOG_DIR, "google-drive.log")
        if os.path.exists(main_log):
            try:
                with open(main_log, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                    return "".join(lines[-max_lines:])
            except Exception as e:
                return f"Falha ao ler log: {e}"
        return "Nenhum arquivo de log anterior registrado."

    @staticmethod
    def sanitize(text: str) -> str:
        """Sanitize sensitive OAuth tokens, emails, and passwords from report."""
        import re
        # Obfuscate Bearer tokens
        text = re.sub(r'Bearer\s+[A-Za-z0-9_\-\.]+', 'Bearer [REDACTED_TOKEN]', text)
        # Obfuscate Client secrets
        text = re.sub(r'client_secret["\']?\s*:\s*["\']?[A-Za-z0-9_\-]+', 'client_secret: [REDACTED_SECRET]', text)
        return text


class CrashReporter:
    @staticmethod
    def generate_report(exc_type=None, exc_value=None, exc_tb=None, custom_notes: str = "") -> Dict[str, Any]:
        """Generate a complete diagnostic bundle."""
        ensure_directories()
        os.makedirs(CRASH_REPORT_DIR, exist_ok=True)

        tb_str = ""
        if exc_type and exc_value and exc_tb:
            tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))

        sys_info = DiagnosticCollector.collect_system_info()
        app_state = DiagnosticCollector.collect_app_state()
        logs = DiagnosticCollector.sanitize(DiagnosticCollector.get_recent_logs())

        title_str = "Relatório de Diagnóstico sob Demanda"
        if exc_value:
            title_str = f"Crash: {exc_value}"
        elif custom_notes and custom_notes.strip():
            title_str = f"Diagnóstico: {custom_notes.strip()[:60]}"

        report = {
            "title": title_str,
            "system_info": sys_info,
            "app_state": app_state,
            "traceback": DiagnosticCollector.sanitize(tb_str),
            "recent_logs": logs,
            "custom_notes": custom_notes,
            "generated_at": datetime.utcnow().isoformat() + "Z"
        }

        # Save to local disk
        filename = f"report_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = os.path.join(CRASH_REPORT_DIR, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        return report

    @staticmethod
    def format_github_issue(report: Dict[str, Any]) -> Tuple[str, str]:
        """Format the report into clean GitHub Flavored Markdown."""
        sys_info = report["system_info"]
        app_state = report["app_state"]

        title = f"[Diagnóstico] {report['title'][:80]}"
        
        notes_section = ""
        if report.get("custom_notes") and report["custom_notes"].strip():
            notes_section = f"""#### 📝 Observações / Mensagem do Usuário
{report['custom_notes'].strip()}

"""

        body = f"""### 🚨 Relatório de Diagnóstico do Google Drive Linux

{notes_section}#### 🖥️ Informações do Sistema
* **Distribuição:** `{sys_info.get('linux_distro', sys_info.get('os_name'))}`
* **Kernel:** `{sys_info.get('os_release')}` ({sys_info.get('architecture')})
* **Interface Gráfica (DE):** `{sys_info.get('desktop_env')}` ({sys_info.get('session_type')})
* **Python:** `{sys_info.get('python_version')}`
* **Data/Hora:** `{report.get('generated_at')}`

#### 📊 Estado do Aplicativo
* **Banco de Metadados Conectado:** `{app_state.get('has_db')}`
* **Contas Conectadas:** `{app_state.get('accounts_count')}`
* **Operações de Sincronização Pendentes:** `{app_state.get('pending_operations')}`

#### 💥 Stack Trace / Exceção
```python
{report.get('traceback') or 'Nenhum traceback de exceção (Diagnóstico sob demanda)'}
```

#### 📋 Últimos Logs Sanitizados
```
{report.get('recent_logs')}
```

---
*Relatório gerado pelo módulo `core/diagnostic.py` (Automático ou Manual sob demanda).*
"""
        return title, body

    @staticmethod
    def _resolve_github_token(explicit_token: Optional[str] = None) -> Optional[str]:
        """Resolve GitHub token from argument, environment, or local 'gh auth token' keyring."""
        if explicit_token:
            return explicit_token
        env_tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if env_tok:
            return env_tok
        if shutil.which("gh"):
            try:
                res = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=4)
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
            except Exception:
                pass
        return None

    @staticmethod
    def report_issue(report: Dict[str, Any], github_token: Optional[str] = None, silent_background: bool = True):
        """Submit the diagnostic report silently in the background via GitHub API / gh CLI without opening browser tabs."""
        title, body = CrashReporter.format_github_issue(report)

        def _send_in_background():
            token = CrashReporter._resolve_github_token(github_token)
            if token:
                try:
                    headers = {
                        "Authorization": f"token {token}",
                        "Accept": "application/vnd.github.v3+json",
                        "Content-Type": "application/json"
                    }
                    payload = json.dumps({"title": title, "body": body, "labels": ["bug", "automated-crash-report"]}).encode("utf-8")
                    req = urllib.request.Request(GITHUB_ISSUES_API, data=payload, headers=headers)
                    with urllib.request.urlopen(req, timeout=12) as resp:
                        res = json.loads(resp.read().decode("utf-8"))
                        url = res.get("html_url", "")
                        print(f"[CrashReporter] Diagnóstico enviado silenciosamente em segundo plano: {url}")
                        return url
                except Exception as e:
                    print(f"[CrashReporter] Aviso ao enviar via API GitHub: {e}")

            if shutil.which("gh"):
                try:
                    res = subprocess.run(
                        ["gh", "issue", "create", "--repo", GITHUB_REPO, "--title", title, "--body", body],
                        capture_output=True,
                        text=True,
                        timeout=15
                    )
                    if res.returncode == 0:
                        url = res.stdout.strip()
                        print(f"[CrashReporter] Diagnóstico enviado silenciosamente via gh CLI: {url}")
                        return url
                except Exception as e:
                    print(f"[CrashReporter] Aviso ao enviar via gh CLI: {e}")

            print(f"[CrashReporter] Relatório salvo localmente em {CRASH_REPORT_DIR} (sem abrir navegador).")
            return None

        if silent_background:
            import threading
            threading.Thread(target=_send_in_background, daemon=True).start()
            return None
        return _send_in_background()

    @staticmethod
    def notify_desktop(title: str, message: str):
        """Send native Ubuntu / GNOME desktop notification."""
        icon_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icons", "drive_fs.png")
        try:
            subprocess.Popen([
                "notify-send",
                "-a", "Google Drive",
                "-i", icon_path if os.path.exists(icon_path) else "google-drive",
                "-h", "string:desktop-entry:google-drive",
                title,
                message
            ])
        except Exception:
            pass


def install_global_crash_hook():
    """Hook sys.excepthook to automatically capture any uncaught exception silently in the background without crashing UI callbacks."""
    original_hook = sys.excepthook

    def custom_hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            original_hook(exc_type, exc_value, exc_tb)
            return
        print(f"\n[Diagnóstico] Exceção capturada ({exc_value}); registrando log em segundo plano...")
        try:
            report = CrashReporter.generate_report(exc_type, exc_value, exc_tb)
            CrashReporter.report_issue(report, silent_background=True)
        except Exception as e:
            print(f"[Diagnóstico] Erro interno ao salvar relatório: {e}")

    sys.excepthook = custom_hook
