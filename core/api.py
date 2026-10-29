"""
Google Drive for Linux - Google Drive API v3 Client
Handles metadata fetching, chunked range downloads (for FUSE streaming), and resumable uploads.
"""
import os
import json
import time
import urllib.parse
import urllib.request
import urllib.error
import io
from typing import Optional, Dict, Any, List, Generator

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
UPLOAD_API_BASE = "https://www.googleapis.com/upload/drive/v3"


class DriveAPI:
    def __init__(self, auth_manager, account_id: str):
        self.auth_manager = auth_manager
        self.account_id = account_id

    def _get_headers(self, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        token = self.auth_manager.get_valid_token(self.account_id)
        if not token:
            token = self.auth_manager.refresh_access_token(self.account_id)
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "GoogleDriveLinuxClient/133.0"
        }
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _request_json(self, url: str, timeout: int = 15) -> Optional[Dict[str, Any]]:
        for attempt in range(4):
            req = urllib.request.Request(url, headers=self._get_headers())
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 0:
                    self.auth_manager.refresh_access_token(self.account_id)
                    continue
                if e.code in (403, 429, 500, 502, 503) and attempt < 3:
                    time.sleep(1.5 * (2 ** attempt))
                    continue
                print(f"[API] HTTP {e.code} em {url.split('?')[0]}: {e}")
                return None
            except Exception as e:
                print(f"[API] Erro em {url.split('?')[0]}: {e}")
                return None
        return None

    def get_about(self) -> Optional[Dict[str, Any]]:
        url = f"{DRIVE_API_BASE}/about?fields=storageQuota,user"
        return self._request_json(url, timeout=10)

    def list_files(self, q: str = "trashed = false", page_size: int = 100, page_token: Optional[str] = None) -> Dict[str, Any]:
        fields = "nextPageToken,files(id,name,mimeType,parents,size,md5Checksum,modifiedTime,createdTime,trashed,capabilities)"
        params = {
            "q": q,
            "pageSize": page_size,
            "fields": fields,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true"
        }
        if page_token:
            params["pageToken"] = page_token

        url = f"{DRIVE_API_BASE}/files?{urllib.parse.urlencode(params)}"
        res = self._request_json(url, timeout=15)
        return res if res is not None else {"files": []}

    def get_file_metadata(self, file_id: str) -> Optional[Dict[str, Any]]:
        fields = "id,name,mimeType,parents,size,md5Checksum,modifiedTime,createdTime,trashed,webViewLink"
        params = {"fields": fields, "supportsAllDrives": "true"}
        url = f"{DRIVE_API_BASE}/files/{urllib.parse.quote(file_id)}?{urllib.parse.urlencode(params)}"
        return self._request_json(url, timeout=10)

    def read_range(self, file_id: str, offset: int, length: int) -> bytes:
        """Read a specific byte range of a file (Crucial for FUSE streaming)."""
        end_offset = offset + length - 1
        headers = self._get_headers({"Range": f"bytes={offset}-{end_offset}"})
        url = f"{DRIVE_API_BASE}/files/{file_id}?alt=media&supportsAllDrives=true"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 416: # Range Not Satisfiable (e.g. empty file or EOF)
                return b""
            print(f"[API] HTTP Error read_range {file_id} [{offset}-{end_offset}]: {e}")
            return b""
        except Exception as e:
            print(f"[API] Error read_range {file_id}: {e}")
            return b""

    def download_full_file(self, file_id: str, dest_path: str) -> bool:
        """Download entire file to local cache with progress."""
        url = f"{DRIVE_API_BASE}/files/{file_id}?alt=media&supportsAllDrives=true"
        req = urllib.request.Request(url, headers=self._get_headers())
        tmp_dest = f"{dest_path}.tmp"
        try:
            with urllib.request.urlopen(req, timeout=60) as resp, open(tmp_dest, "wb") as f_out:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f_out.write(chunk)
            if os.path.exists(dest_path):
                os.remove(dest_path)
            os.rename(tmp_dest, dest_path)
            return True
        except Exception as e:
            if os.path.exists(tmp_dest):
                os.remove(tmp_dest)
            print(f"[API] Error downloading file {file_id}: {e}")
            return False

    def upload_new_file(self, local_path: str, name: str, parent_id: Optional[str] = "root") -> Optional[Dict[str, Any]]:
        """Upload a newly created local file using resumable upload protocol."""
        metadata = {
            "name": name,
            "parents": [parent_id] if parent_id else ["root"]
        }
        file_size = os.path.getsize(local_path)

        # 1. Initiate resumable upload session
        init_headers = self._get_headers({
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "application/octet-stream",
            "X-Upload-Content-Length": str(file_size)
        })
        init_url = f"{UPLOAD_API_BASE}/files?uploadType=resumable&supportsAllDrives=true"
        req = urllib.request.Request(init_url, data=json.dumps(metadata).encode("utf-8"), headers=init_headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                session_url = resp.headers.get("Location")

            if not session_url:
                return None

            # 2. Upload file content in chunks
            with open(local_path, "rb") as f:
                data = f.read()

            upload_headers = {
                "Content-Length": str(file_size),
                "Content-Type": "application/octet-stream"
            }
            put_req = urllib.request.Request(session_url, data=data, headers=upload_headers, method="PUT")
            with urllib.request.urlopen(put_req, timeout=60) as put_resp:
                return json.loads(put_resp.read().decode("utf-8"))
        except Exception as e:
            print(f"[API] Error uploading file {name}: {e}")
            return None

    def update_file_content(self, file_id: str, local_path: str) -> Optional[Dict[str, Any]]:
        """Update existing file content on Google Drive."""
        file_size = os.path.getsize(local_path)
        init_headers = self._get_headers({
            "X-Upload-Content-Type": "application/octet-stream",
            "X-Upload-Content-Length": str(file_size)
        })
        init_url = f"{UPLOAD_API_BASE}/files/{file_id}?uploadType=resumable&supportsAllDrives=true"
        req = urllib.request.Request(init_url, data=b"", headers=init_headers, method="PATCH")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                session_url = resp.headers.get("Location")

            if not session_url:
                return None

            with open(local_path, "rb") as f:
                data = f.read()

            upload_headers = {
                "Content-Length": str(file_size),
                "Content-Type": "application/octet-stream"
            }
            put_req = urllib.request.Request(session_url, data=data, headers=upload_headers, method="PUT")
            with urllib.request.urlopen(put_req, timeout=60) as put_resp:
                return json.loads(put_resp.read().decode("utf-8"))
        except Exception as e:
            print(f"[API] Error updating content for {file_id}: {e}")
            return None

    def delete_file(self, file_id: str) -> bool:
        url = f"{DRIVE_API_BASE}/files/{file_id}?supportsAllDrives=true"
        req = urllib.request.Request(url, headers=self._get_headers(), method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            print(f"[API] Error deleting {file_id}: {e}")
            return False

    def rename_file(self, file_id: str, new_name: str) -> bool:
        url = f"{DRIVE_API_BASE}/files/{file_id}?supportsAllDrives=true"
        req = urllib.request.Request(
            url,
            data=json.dumps({"name": new_name}).encode("utf-8"),
            headers=self._get_headers({"Content-Type": "application/json"}),
            method="PATCH"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception as e:
            print(f"[API] Error renaming {file_id}: {e}")
            return False
