"""
Google Drive for Linux - Google Omaha Auto-Updater
Queries official Google Update (Omaha v2/v3) servers to track, verify and apply Google Drive releases.
"""
import urllib.request
import json
import os
import io
import zipfile
import hashlib
import threading
import time
from typing import Optional, Dict, Any, Callable

OMAHA_ENDPOINT = "https://update.googleapis.com/service/update2/json"
GOOGLE_DRIVE_APP_ID = "{6BBAE539-2232-434A-A4E5-9A33560C6283}"


class OmahaUpdater(threading.Thread):
    def __init__(self, current_version: str = "132.0.0.0", check_interval_hours: int = 12, on_update_found: Optional[Callable] = None):
        super().__init__(daemon=True, name="OmahaUpdaterThread")
        self.current_version = current_version
        self.interval_sec = check_interval_hours * 3600
        self.on_update_found = on_update_found
        self.running = True
        self.last_check_result: Optional[Dict[str, Any]] = None

    def run(self):
        print(f"[OmahaUpdater] Monitor de atualizações iniciado (versão atual: {self.current_version})")
        while self.running:
            try:
                result = self.check_update()
                self.last_check_result = result
                if result and result.get("has_update"):
                    print(f"[OmahaUpdater] Nova versão detectada pela Google: {result['latest_version']}!")
                    if self.on_update_found:
                        self.on_update_found(result)
            except Exception as e:
                print(f"[OmahaUpdater] Falha na verificação de atualização: {e}")

            # Sleep in small slices to allow quick thread termination
            for _ in range(int(self.interval_sec // 10)):
                if not self.running:
                    break
                time.sleep(10)

    def stop(self):
        self.running = False

    def check_update(self) -> Optional[Dict[str, Any]]:
        """Query Google's production update servers for Google Drive."""
        payload = {
            "request": {
                "@os": "win",
                "@updater": "Omaha",
                "acceptformat": "crx3,file",
                "os": {
                    "platform": "win",
                    "version": "10.0.22631",
                    "arch": "x64"
                },
                "app": [
                    {
                        "appid": GOOGLE_DRIVE_APP_ID,
                        "version": self.current_version,
                        "updatecheck": {}
                    }
                ]
            }
        }

        req = urllib.request.Request(
            OMAHA_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw_text = resp.read().decode("utf-8")
                if raw_text.startswith(")]}'\n"):
                    raw_text = raw_text[5:]

                data = json.loads(raw_text)
                app_resp = data.get("gupdate", {}).get("app", [{}])[0]
                update_info = app_resp.get("updatecheck", {})

                if update_info.get("status") == "ok":
                    codebase = update_info.get("codebase", "")
                    # Extract version number from official Google URL
                    # Example: https://dl.google.com/release2/drive-file-stream/..._133.0.2.0/-6bbae539...
                    version = self.current_version
                    if "_13" in codebase:
                        part = codebase.split("_13")[-1]
                        version = "13" + part.split("/")[0]

                    return {
                        "has_update": True,
                        "latest_version": version,
                        "download_url": codebase,
                        "sha256": update_info.get("hash_sha256"),
                        "size_bytes": update_info.get("size")
                    }
                else:
                    return {"has_update": False, "latest_version": self.current_version}
        except Exception as e:
            print(f"[OmahaUpdater] Erro ao consultar endpoint Omaha: {e}")
            return None

    def download_and_extract_assets(self, download_url: str, expected_sha256: str, target_assets_dir: str) -> bool:
        """Download update package from Google CDN, verify SHA-256, and extract HTML/UI assets."""
        print(f"[OmahaUpdater] Baixando atualização do Google CDN: {download_url}")
        try:
            req = urllib.request.Request(download_url, headers={"User-Agent": "GoogleUpdate/1.3.36.372"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload_bytes = resp.read()

            # Verify integrity
            h = hashlib.sha256(payload_bytes).hexdigest()
            if h.lower() != expected_sha256.lower():
                print(f"[OmahaUpdater] Falha no hash SHA-256. Esperado: {expected_sha256}, Obtido: {h}")
                return False

            # Extract zip payload from CRX3 archive
            # CRX3 format starts with 'Cr24' (0x34327243)
            # The ZIP payload is contained inside
            zip_offset = payload_bytes.find(b"PK\x03\x04")
            if zip_offset == -1:
                print("[OmahaUpdater] Arquivo ZIP não encontrado no pacote recebido")
                return False

            with zipfile.ZipFile(io.BytesIO(payload_bytes[zip_offset:])) as zf:
                for file_info in zf.infolist():
                    # Extract only web dialogs and icons
                    if file_info.filename.startswith("html/") or file_info.filename.endswith((".ico", ".svg", ".png", ".html", ".css")):
                        zf.extract(file_info, target_assets_dir)

            print(f"[OmahaUpdater] Recursos da nova versão extraídos com sucesso em: {target_assets_dir}")
            return True
        except Exception as e:
            print(f"[OmahaUpdater] Erro ao processar download da atualização: {e}")
            return False
