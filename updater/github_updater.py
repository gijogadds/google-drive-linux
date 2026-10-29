"""
Google Drive for Linux - GitHub Release & Hotfix Auto-Updater
Closes the loop between issue reporting and automated fix delivery:
1. Issue created on GitHub when an error occurs.
2. Developer / AI fixes the issue and publishes a commit / release.
3. This module automatically detects the fix, downloads the update, applies it,
   restarts the background service, and notifies the user.
"""
import urllib.request
import json
import os
import sys
import io
import zipfile
import shutil
import subprocess
import threading
import time
from typing import Optional, Dict, Any, Callable

from core.version import __version__, __github_repo__

GITHUB_API_BASE = f"https://api.github.com/repos/{__github_repo__}"
INSTALL_DIR = "/opt/google-drive-linux"


class GitHubUpdater(threading.Thread):
    def __init__(self, current_version: str = __version__, check_interval_hours: int = 4, on_update_applied: Optional[Callable] = None):
        super().__init__(daemon=True, name="GitHubUpdaterThread")
        self.current_version = current_version
        self.interval_sec = check_interval_hours * 3600
        self.on_update_applied = on_update_applied
        self.running = True

    def run(self):
        print(f"[GitHubUpdater] Monitor de correções do GitHub ativo (versão atual do cliente: {self.current_version})")
        # Initial check after 30 seconds of uptime
        time.sleep(30)
        while self.running:
            try:
                update_info = self.check_update()
                if update_info and update_info.get("has_update"):
                    print(f"[GitHubUpdater] Nova correção detectada no GitHub: {update_info['new_version']}!")
                    # Check if auto-apply is enabled
                    applied = self.apply_update(update_info)
                    if applied:
                        if self.on_update_applied:
                            self.on_update_applied(update_info)
                        self.notify_user_and_restart(update_info)
            except Exception as e:
                print(f"[GitHubUpdater] Erro ao verificar correções no GitHub: {e}")

            # Sleep in intervals
            for _ in range(int(self.interval_sec // 10)):
                if not self.running:
                    break
                time.sleep(10)

    def stop(self):
        self.running = False

    def check_update(self) -> Optional[Dict[str, Any]]:
        """Check GitHub for either a newer Release Tag or latest commit on main branch."""
        # 1. First check official Releases API
        req = urllib.request.Request(
            f"{GITHUB_API_BASE}/releases/latest",
            headers={"User-Agent": "GoogleDriveLinuxClient/AutoUpdater", "Accept": "application/vnd.github.v3+json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                tag_name = data.get("tag_name", "").lstrip("v")
                if tag_name and tag_name != self.current_version:
                    zip_url = data.get("zipball_url") or f"https://github.com/{__github_repo__}/archive/refs/tags/{data['tag_name']}.zip"
                    return {
                        "has_update": True,
                        "type": "release",
                        "new_version": tag_name,
                        "title": data.get("name", f"Release {tag_name}"),
                        "body": data.get("body", ""),
                        "download_url": zip_url
                    }
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # No release tags yet, fallback to latest commit on main branch
                return self._check_latest_commit()
        except Exception as e:
            print(f"[GitHubUpdater] Falha ao consultar releases: {e}")

        # Fallback to commit check
        return self._check_latest_commit()

    def _check_latest_commit(self) -> Optional[Dict[str, Any]]:
        """Check if main branch has fresh commits (hotfixes)."""
        commit_stamp_file = os.path.expanduser("~/.config/google-drive-linux/last_commit.sha")
        last_sha = ""
        if os.path.exists(commit_stamp_file):
            with open(commit_stamp_file, "r") as f:
                last_sha = f.read().strip()

        req = urllib.request.Request(
            f"{GITHUB_API_BASE}/commits/main",
            headers={"User-Agent": "GoogleDriveLinuxClient/AutoUpdater", "Accept": "application/vnd.github.v3+json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                current_sha = data.get("sha", "")
                commit_msg = data.get("commit", {}).get("message", "")
                
                # If we have a record and the sha changed
                if last_sha and current_sha and current_sha != last_sha:
                    zip_url = f"https://github.com/{__github_repo__}/archive/refs/heads/main.zip"
                    return {
                        "has_update": True,
                        "type": "commit",
                        "new_version": current_sha[:7],
                        "title": commit_msg.split("\n")[0],
                        "download_url": zip_url,
                        "sha": current_sha
                    }
                elif not last_sha and current_sha:
                    # Save initial commit stamp
                    os.makedirs(os.path.dirname(commit_stamp_file), exist_ok=True)
                    with open(commit_stamp_file, "w") as f:
                        f.write(current_sha)
        except Exception as e:
            pass
        return {"has_update": False, "new_version": self.current_version}

    def apply_update(self, update_info: Dict[str, Any]) -> bool:
        """Download and unpack the latest code from GitHub into installation."""
        download_url = update_info.get("download_url")
        if not download_url:
            return False

        print(f"[GitHubUpdater] Baixando atualização do GitHub: {download_url}...")
        try:
            req = urllib.request.Request(download_url, headers={"User-Agent": "GoogleDriveLinuxClient/AutoUpdater"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                zip_bytes = resp.read()

            target_dir = INSTALL_DIR if os.path.exists(INSTALL_DIR) and os.access(INSTALL_DIR, os.W_OK) else os.path.dirname(os.path.dirname(__file__))
            
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                # Find root dir in zip (e.g. google-drive-linux-main/)
                root_prefix = zf.namelist()[0].split("/")[0] + "/"
                for member in zf.infolist():
                    if member.filename.startswith(root_prefix):
                        rel_path = member.filename[len(root_prefix):]
                        if not rel_path or rel_path.startswith((".git", ".github", "tests/")):
                            continue
                        dest_path = os.path.join(target_dir, rel_path)
                        if member.is_dir():
                            os.makedirs(dest_path, exist_ok=True)
                        else:
                            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                            with zf.open(member) as src, open(dest_path, "wb") as dst:
                                shutil.copyfileobj(src, dst)
                            if rel_path in ("main.py", "gdrive-cli", "gdrive-doc-launcher", "install.sh"):
                                os.chmod(dest_path, 0o755)

            # Update local commit stamp if present
            if "sha" in update_info:
                commit_stamp_file = os.path.expanduser("~/.config/google-drive-linux/last_commit.sha")
                with open(commit_stamp_file, "w") as f:
                    f.write(update_info["sha"])

            print(f"[GitHubUpdater] Atualização aplicada com sucesso em: {target_dir}")
            return True
        except Exception as e:
            print(f"[GitHubUpdater] Falha ao aplicar atualização: {e}")
            return False

    def notify_user_and_restart(self, update_info: Dict[str, Any]):
        """Notify Ubuntu desktop and safely trigger systemd user restart."""
        msg = f"A correção para os problemas reportados foi aplicada automaticamente (Versão: {update_info['new_version']})."
        try:
            subprocess.Popen([
                "notify-send",
                "-a", "Google Drive",
                "-i", "software-update-available",
                "Google Drive Atualizado!",
                msg
            ])
        except Exception:
            pass

        # If running under systemd, request restart after 3 seconds
        def _delayed_restart():
            time.sleep(3)
            subprocess.run(["systemctl", "--user", "restart", "google-drive.service"], capture_output=True)

        threading.Thread(target=_delayed_restart, daemon=True).start()
