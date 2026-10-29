"""
Google Drive for Linux - OAuth 2.0 PKCE Authentication Manager
Handles interactive browser login, token refresh, and credential storage via Linux Keyring.
"""
import os
import sys
import json
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
            
            # Read official Google Drive success template if exists
            success_template_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                "assets", "html", "browserauth_result_template.html"
            )
            if os.path.exists(success_template_path):
                with open(success_template_path, "r", encoding="utf-8") as f:
                    content = f.read().replace("$RESULT$", "Sucesso! Pode fechar esta janela.")
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

    def login_interactive(self) -> Optional[Dict[str, Any]]:
        """Start local HTTP callback server and open browser for Google OAuth."""
        OAuthCallbackHandler.auth_code = None
        OAuthCallbackHandler.error = None

        server = HTTPServer(("127.0.0.1", 0), OAuthCallbackHandler)
        server.timeout = 180
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
            "prompt": "consent"
        }

        auth_url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(auth_params)}"
        print(f"[Auth] Abrindo navegador para login: {auth_url}")
        webbrowser.open(auth_url)

        # Wait for OAuth callback (ignoring incidental requests like /favicon.ico)
        while OAuthCallbackHandler.auth_code is None and OAuthCallbackHandler.error is None:
            server.handle_request()
            if OAuthCallbackHandler.auth_code is None and OAuthCallbackHandler.error is None:
                # Timeout reached or empty request
                break
        server.server_close()

        code = OAuthCallbackHandler.auth_code
        if not code:
            print(f"[Auth] Erro no login: {OAuthCallbackHandler.error or 'tempo esgotado'}")
            return None

        # Exchange code for tokens
        token_data = self._exchange_code_for_tokens(code, verifier, redirect_uri)
        if not token_data or "access_token" not in token_data:
            return None

        # Fetch profile
        profile = self.get_user_profile(token_data["access_token"])
        if not profile:
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
                "last_sync": 0
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
