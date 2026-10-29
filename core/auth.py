"""
Google Drive for Linux - OAuth 2.0 PKCE Authentication Manager
Handles interactive browser login, token refresh, and credential storage via Linux Keyring.
"""
import os
import sys
import json
import time
import base64
import hashlib
import secrets
import threading
import urllib.parse
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
import webbrowser
from typing import Optional, Dict, Any

from core.config import (
    DEFAULT_CLIENT_ID, DEFAULT_CLIENT_SECRET, ACCOUNTS_FILE_PATH,
    CONFIG_DIR, ensure_directories, load_config
)
from core.db import MetadataDB

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/userinfo.email"
]

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_ABOUT_URL = "https://www.googleapis.com/drive/v3/about?fields=storageQuota,user"


def _generate_code_verifier() -> str:
    token = secrets.token_urlsafe(64)
    return token[:128]


def _generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").replace("=", "")


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    auth_code: Optional[str] = None
    error: Optional[str] = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            OAuthCallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            
            # Read official Google Drive success template and format %s placeholders
            root_dir = os.path.dirname(os.path.dirname(__file__))
            success_template_path = os.path.join(root_dir, "assets", "html", "browserauth_result_template.html")
            icon_drive_path = os.path.join(root_dir, "assets", "icons", "drive_fs.png")
            icon_google_path = os.path.join(root_dir, "assets", "icons", "google_g.png")

            def _b64_img(p):
                if os.path.exists(p):
                    with open(p, "rb") as img_f:
                        return base64.b64encode(img_f.read()).decode("ascii")
                return ""

            drive_b64 = _b64_img(icon_drive_path)
            google_b64 = _b64_img(icon_google_path) or drive_b64

            if os.path.exists(success_template_path):
                with open(success_template_path, "r", encoding="utf-8") as f:
                    raw_tpl = f.read()
                try:
                    content = raw_tpl % (
                        "ltr",
                        "pt-BR",
                        "Google Drive - Conectado",
                        drive_b64,
                        google_b64,
                        "Sucesso!",
                        drive_b64,
                        "Conta conectada ao Google Drive para Linux.",
                        "Você já pode fechar esta guia e retornar ao aplicativo."
                    )
                except Exception:
                    content = raw_tpl
            else:
                content = """
                <html>
                <head><title>Google Drive - Conectado</title></head>
                <body style="font-family:sans-serif; text-align:center; padding-top:50px;">
                    <h2 style="color:#1a73e8;">Conectado com sucesso!</h2>
                    <p>Você pode fechar esta aba e retornar ao aplicativo.</p>
                </body>
                </html>
                """
            self.wfile.write(content.encode("utf-8"))
        elif "error" in params:
            OAuthCallbackHandler.error = params.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Falha na autentica\xc3\xa7\xc3\xa3o. Tente novamente.")
        else:
            self.send_response(204)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress default HTTP logging to stdout


class AuthManager:
    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None):
        ensure_directories()
        cfg = load_config()
        self.client_id = client_id or cfg.get("client_id") or DEFAULT_CLIENT_ID
        self.client_secret = client_secret if client_secret is not None else (cfg.get("client_secret") or DEFAULT_CLIENT_SECRET)
        self.accounts = self._load_accounts_from_disk()
        self.last_error: Optional[str] = None

    def _load_accounts_from_disk(self) -> Dict[str, Any]:
        if os.path.exists(ACCOUNTS_FILE_PATH):
            try:
                with open(ACCOUNTS_FILE_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_accounts_to_disk(self):
        with open(ACCOUNTS_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(self.accounts, f, indent=2)
        try:
            os.chmod(ACCOUNTS_FILE_PATH, 0o600)
        except Exception:
            pass

    def _open_oauth_browser(self, auth_url: str, isolated: bool = False):
        """Open the OAuth URL in the user's browser, using an isolated/incognito window when adding a second account to prevent Google's authuser=1 HTTP 400 error."""
        import shutil
        import subprocess

        if isolated:
            # 1. Check native Chromium/Chrome/Brave/Edge binaries
            for bin_name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser", "microsoft-edge"):
                path = shutil.which(bin_name)
                if path:
                    try:
                        subprocess.Popen([path, "--incognito", auth_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return
                    except Exception:
                        pass

            # 2. Check Flatpak Chrome/Chromium/Brave/Edge
            if shutil.which("flatpak"):
                for app_id in ("com.google.Chrome", "org.chromium.Chromium", "com.brave.Browser", "com.microsoft.Edge"):
                    try:
                        chk = subprocess.run(["flatpak", "info", app_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        if chk.returncode == 0:
                            subprocess.Popen(["flatpak", "run", app_id, "--incognito", auth_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            return
                    except Exception:
                        pass

            # 3. Check Firefox
            ff_path = shutil.which("firefox")
            if ff_path:
                try:
                    subprocess.Popen([ff_path, "--private-window", auth_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
                except Exception:
                    pass

        webbrowser.open(auth_url)

    def login_interactive(self, login_hint: Optional[str] = None, isolated_session: Optional[bool] = None) -> Optional[Dict[str, Any]]:
        """Start local HTTP callback server and open browser for Google OAuth."""
        self.last_error = None
        OAuthCallbackHandler.auth_code = None
        OAuthCallbackHandler.error = None

        server = HTTPServer(("127.0.0.1", 0), OAuthCallbackHandler)
        server.timeout = 180
        server.timed_out = False
        server.handle_timeout = lambda: setattr(server, "timed_out", True)
        port = server.server_port
        redirect_uri = f"http://127.0.0.1:{port}"

        verifier = _generate_code_verifier()
        challenge = _generate_code_challenge(verifier)

        auth_params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "select_account consent",
            "state": secrets.token_urlsafe(16),
        }
        if login_hint and login_hint.strip():
            auth_params["login_hint"] = login_hint.strip()

        auth_url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(auth_params)}"
        use_isolated = isolated_session if isolated_session is not None else (len(self.accounts) > 0)
        print(f"[Auth] Abrindo navegador para login (isolado={use_isolated}): {auth_url}")
        self._open_oauth_browser(auth_url, isolated=use_isolated)

        # Wait for OAuth callback (ignoring incidental requests like /favicon.ico)
        deadline = time.time() + 180
        while (
            OAuthCallbackHandler.auth_code is None
            and OAuthCallbackHandler.error is None
            and not getattr(server, "timed_out", False)
            and time.time() < deadline
        ):
            server.handle_request()
        server.server_close()

        code = OAuthCallbackHandler.auth_code
        if not code:
            err_code = OAuthCallbackHandler.error
            if err_code == "access_denied":
                self.last_error = "O acesso foi recusado ou cancelado na tela de autorização do Google."
            elif err_code:
                self.last_error = f"O servidor do Google retornou um erro de autorização ({err_code})."
            else:
                self.last_error = (
                    "O tempo limite de 3 minutos para concluir o login no navegador expirou "
                    "ou a página de autorização foi fechada antes da conclusão.\n\n"
                    "Dica: Ao adicionar uma segunda conta no Chrome, informe o e-mail da conta "
                    "no campo opcional para abrir a autorização diretamente nela e evitar o Erro 400 de múltiplas sessões."
                )
            print(f"[Auth] Erro no login: {self.last_error}")
            return None

        # Exchange code for tokens
        token_data = self._exchange_code_for_tokens(code, verifier, redirect_uri)
        if not token_data or "access_token" not in token_data:
            self.last_error = "Falha ao validar o código de autorização junto aos servidores do Google OAuth."
            return None

        # Fetch profile
        profile = self.get_user_profile(token_data["access_token"])
        if not profile:
            self.last_error = "Não foi possível obter os dados de perfil da Conta Google autenticada."
            return None

        # Fetch storage quota if available
        storage_total, storage_used = self.get_storage_quota(token_data["access_token"])

        account_id = profile.get("id")
        account_info = {
            "account_id": account_id,
            "email": profile.get("email"),
            "display_name": profile.get("name", ""),
            "photo_url": profile.get("picture", ""),
            "storage_total": storage_total,
            "storage_used": storage_used,
            "tokens": token_data,
            "is_active": True
        }

        self.accounts[account_id] = account_info
        self._save_accounts_to_disk()

        # Persist to SQLite metadata DB so both CLI and GUI share state immediately
        try:
            db = MetadataDB()
            db.upsert_account({
                "account_id": account_id,
                "email": account_info["email"],
                "display_name": account_info["display_name"],
                "photo_url": account_info["photo_url"],
                "storage_total": storage_total,
                "storage_used": storage_used,
                "is_active": 1,
                "auth_data": json.dumps(token_data),
                "last_sync": int(time.time())
            })
        except Exception as e:
            print(f"[Auth] Aviso ao sincronizar conta no banco SQLite: {e}")

        return account_info

    def _exchange_code_for_tokens(self, code: str, verifier: str, redirect_uri: str) -> Optional[Dict[str, Any]]:
        token_payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri
        }

        req = urllib.request.Request(
            GOOGLE_TOKEN_URL,
            data=urllib.parse.urlencode(token_payload).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"[Auth] Erro ao trocar authorization code: {e}")
            return None

    def refresh_access_token(self, account_id: str) -> Optional[str]:
        account = self.accounts.get(account_id)
        if not account or "tokens" not in account:
            return None

        refresh_token = account["tokens"].get("refresh_token")
        if not refresh_token:
            return None

        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
        }

        req = urllib.request.Request(
            GOOGLE_TOKEN_URL,
            data=urllib.parse.urlencode(payload).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                new_token = data.get("access_token")
                account["tokens"]["access_token"] = new_token
                self._save_accounts_to_disk()
                return new_token
        except Exception as e:
            print(f"[Auth] Erro ao renovar token da conta {account_id}: {e}")
            return None

    def get_valid_token(self, account_id: str) -> Optional[str]:
        account = self.accounts.get(account_id)
        if not account:
            return None
        # Always verify or refresh
        return account["tokens"].get("access_token") or self.refresh_access_token(account_id)

    def get_user_profile(self, access_token: str) -> Optional[Dict[str, Any]]:
        req = urllib.request.Request(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"[Auth] Erro ao buscar perfil: {e}")
            return None

    def get_storage_quota(self, access_token: str):
        req = urllib.request.Request(
            GOOGLE_ABOUT_URL,
            headers={"Authorization": f"Bearer {access_token}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                quota = data.get("storageQuota", {})
                total = int(quota.get("limit", 0))
                used = int(quota.get("usage", 0))
                return total, used
        except Exception:
            return 0, 0
