"""
Google Drive for Linux - Google Drive API v3 Client
Handles incremental delta sync (changes.list), chunked range downloads (FUSE streaming),
8 MB chunked resumable uploads, offline Google Docs export, safe trash, and proxy support.
"""
import os
import json
import time
import random
import urllib.parse
import urllib.request
import urllib.error
from typing import Optional, Dict, Any, List, Callable

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
UPLOAD_API_BASE = "https://www.googleapis.com/upload/drive/v3"

# 8 MB chunk size for resumable uploads (must be a multiple of 256 KiB per Google Drive spec)
RESUMABLE_CHUNK_SIZE = 8 * 1024 * 1024


class DriveAPI:
    def __init__(self, auth_manager, account_id: str, proxy_config: Optional[Dict[str, Any]] = None):
        self.auth_manager = auth_manager
        self.account_id = account_id
        self.proxy_config = proxy_config or {}
        self._opener = self._build_opener()

    def _build_opener(self) -> urllib.request.OpenerDirector:
        """Build URL opener with optional manual/system proxy support."""
        mode = (self.proxy_config.get("mode") or "SYSTEM").upper()
        if mode == "NONE":
            return urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if mode == "MANUAL" and self.proxy_config.get("host") and self.proxy_config.get("port"):
            ptype = (self.proxy_config.get("type") or "HTTP").lower()
            host = self.proxy_config["host"]
            port = self.proxy_config["port"]
            user = self.proxy_config.get("username")
            pwd = self.proxy_config.get("password")
            auth_part = f"{urllib.parse.quote(user)}:{urllib.parse.quote(pwd or '')}@" if user else ""
            proxy_url = f"{ptype}://{auth_part}{host}:{port}"
            return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
        return urllib.request.build_opener()

    def _urlopen(self, req: urllib.request.Request, timeout: int = 20):
        return self._opener.open(req, timeout=timeout)

    def _get_headers(self, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        token = self.auth_manager.get_valid_token(self.account_id)
        if not token:
            token = self.auth_manager.refresh_access_token(self.account_id)
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "GoogleDriveLinuxClient/133.0",
            "Connection": "keep-alive",
        }
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _sleep_backoff(self, attempt: int, retry_after: Optional[str] = None):
        if retry_after:
            try:
                delay = min(float(retry_after), 60.0)
                if delay > 0:
                    time.sleep(delay)
                    return
            except ValueError:
                pass
        # Exponential backoff with jitter (2s, 4s, 8s, 16s + random jitter)
        base = min(2.0 * (2 ** attempt), 32.0)
        time.sleep(base + random.uniform(0.1, 1.0))

    def _request_json(
        self,
        url: str,
        method: str = "GET",
        payload: Optional[Dict[str, Any]] = None,
        timeout: int = 15,
    ) -> Optional[Dict[str, Any]]:
        data_bytes = json.dumps(payload).encode("utf-8") if payload is not None else None
        extra_hdrs = {"Content-Type": "application/json; charset=UTF-8"} if payload is not None else None

        for attempt in range(4):
            req = urllib.request.Request(url, data=data_bytes, headers=self._get_headers(extra_hdrs), method=method)
            try:
                with self._urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw.strip() else {}
            except urllib.error.HTTPError as e:
                err_body = ""
                try:
                    err_body = e.read().decode("utf-8", errors="ignore")
                except Exception:
                    pass

                is_auth_error = (e.code == 401) or (
                    e.code == 403 and any(k in err_body for k in ("authError", "invalidCredentials", "tokenExpired", "Invalid Credentials"))
                )
                if is_auth_error and attempt == 0:
                    self.auth_manager.refresh_access_token(self.account_id)
                    continue

                is_retryable = (
                    e.code in (429, 500, 502, 503, 504)
                    or (e.code == 403 and ("rateLimit" in err_body or "userRateLimitExceeded" in err_body))
                )
                if is_retryable and attempt < 3:
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                    self._sleep_backoff(attempt, retry_after=retry_after)
                    continue

                if "rateLimit" in err_body:
                    print(f"[API] Google Rate Limit atingido ({e.code}), aguardando janela de requisições...")
                else:
                    print(f"[API] HTTP {e.code} em {url.split('?')[0]}: {e}")
                return None
            except Exception as e:
                if attempt < 2:
                    self._sleep_backoff(attempt)
                    continue
                print(f"[API] Erro em {url.split('?')[0]}: {e}")
                return None
        return None

    # --- Account & Quota ---
    def get_about(self) -> Optional[Dict[str, Any]]:
        url = f"{DRIVE_API_BASE}/about?fields=storageQuota,user"
        return self._request_json(url, timeout=10)

    # --- Incremental Delta Sync (Changes API v3) ---
    def get_start_page_token(self) -> Optional[str]:
        """Obtain the initial startPageToken for incremental changes.list polling."""
        params = {"supportsAllDrives": "true"}
        url = f"{DRIVE_API_BASE}/changes/startPageToken?{urllib.parse.urlencode(params)}"
        res = self._request_json(url, timeout=10)
        return res.get("startPageToken") if res else None

    def list_changes(self, page_token: str, page_size: int = 500) -> Dict[str, Any]:
        """Fetch incremental changes since page_token (reduces API usage by >95%)."""
        fields = (
            "nextPageToken,newStartPageToken,"
            "changes(fileId,removed,time,driveId,"
            "file(id,name,mimeType,parents,size,md5Checksum,modifiedTime,createdTime,trashed,starred,shared,driveId,webViewLink))"
        )
        params = {
            "pageToken": page_token,
            "pageSize": page_size,
            "fields": fields,
            "includeRemoved": "true",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        url = f"{DRIVE_API_BASE}/changes?{urllib.parse.urlencode(params)}"
        res = self._request_json(url, timeout=20)
        return res if res is not None else {"changes": []}

    # --- Listing & Search ---
    def list_files(self, q: str = "trashed = false", page_size: int = 100, page_token: Optional[str] = None) -> Dict[str, Any]:
        fields = "nextPageToken,files(id,name,mimeType,parents,size,md5Checksum,modifiedTime,createdTime,trashed,starred,shared,driveId,webViewLink,capabilities)"
        params = {
            "q": q,
            "pageSize": page_size,
            "fields": fields,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if page_token:
            params["pageToken"] = page_token

        url = f"{DRIVE_API_BASE}/files?{urllib.parse.urlencode(params)}"
        res = self._request_json(url, timeout=15)
        return res if res is not None else {"files": []}

    def list_shared_with_me(self, page_size: int = 200, page_token: Optional[str] = None) -> Dict[str, Any]:
        """List files and folders explicitly shared with the user."""
        return self.list_files(q="sharedWithMe = true and trashed = false", page_size=page_size, page_token=page_token)

    def list_shared_drives(self, page_size: int = 100, page_token: Optional[str] = None) -> Dict[str, Any]:
        """List Team Drives / Shared Drives accessible by the account."""
        params = {"pageSize": page_size, "fields": "nextPageToken,drives(id,name)"}
        if page_token:
            params["pageToken"] = page_token
        url = f"{DRIVE_API_BASE}/drives?{urllib.parse.urlencode(params)}"
        res = self._request_json(url, timeout=15)
        return res if res is not None else {"drives": []}

    def search_files_online(self, query: str, page_size: int = 60) -> List[Dict[str, Any]]:
        """Search files directly on Google Drive API v3 by name."""
        q_escaped = (query or "").strip().replace("\\", "\\\\").replace("'", "\\'")
        if not q_escaped:
            return []
        q_str = f"name contains '{q_escaped}' and trashed = false"
        res = self.list_files(q=q_str, page_size=page_size)
        return res.get("files", [])

    def get_file_metadata(self, file_id: str) -> Optional[Dict[str, Any]]:
        fields = "id,name,mimeType,parents,size,md5Checksum,modifiedTime,createdTime,trashed,starred,shared,driveId,webViewLink"
        params = {"fields": fields, "supportsAllDrives": "true"}
        url = f"{DRIVE_API_BASE}/files/{urllib.parse.quote(file_id)}?{urllib.parse.urlencode(params)}"
        return self._request_json(url, timeout=10)

    # --- Downloads & Exports ---
    def read_range(self, file_id: str, offset: int, length: int) -> bytes:
        """Read a specific byte range of a file (Crucial for FUSE streaming)."""
        end_offset = offset + length - 1
        url = f"{DRIVE_API_BASE}/files/{file_id}?alt=media&supportsAllDrives=true"
        for attempt in range(3):
            headers = self._get_headers({"Range": f"bytes={offset}-{end_offset}"})
            req = urllib.request.Request(url, headers=headers)
            try:
                with self._urlopen(req, timeout=20) as resp:
                    return resp.read()
            except urllib.error.HTTPError as e:
                if e.code == 416:
                    return b""
                if e.code == 401 and attempt == 0:
                    self.auth_manager.refresh_access_token(self.account_id)
                    continue
                if e.code in (429, 500, 502, 503) and attempt < 2:
                    self._sleep_backoff(attempt)
                    continue
                print(f"[API] HTTP Error read_range {file_id} [{offset}-{end_offset}]: {e}")
                return b""
            except Exception as e:
                if attempt < 2:
                    self._sleep_backoff(attempt)
                    continue
                print(f"[API] Error read_range {file_id}: {e}")
                return b""
        return b""

    def download_full_file(self, file_id: str, dest_path: str, progress_cb: Optional[Callable[[int, int], None]] = None) -> bool:
        """Download entire file to local cache atomically with bounded memory (64 KiB chunks)."""
        url = f"{DRIVE_API_BASE}/files/{file_id}?alt=media&supportsAllDrives=true"
        tmp_dest = f"{dest_path}.tmp"
        for attempt in range(3):
            req = urllib.request.Request(url, headers=self._get_headers())
            try:
                os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
                downloaded = 0
                with self._urlopen(req, timeout=60) as resp, open(tmp_dest, "wb") as f_out:
                    total = int(resp.headers.get("Content-Length") or 0)
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        f_out.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb:
                            progress_cb(downloaded, total)
                if os.path.exists(dest_path):
                    os.remove(dest_path)
                os.rename(tmp_dest, dest_path)
                return True
            except urllib.error.HTTPError as e:
                if os.path.exists(tmp_dest):
                    os.remove(tmp_dest)
                if e.code == 401 and attempt == 0:
                    self.auth_manager.refresh_access_token(self.account_id)
                    continue
                if e.code in (429, 500, 502, 503) and attempt < 2:
                    self._sleep_backoff(attempt)
                    continue
                print(f"[API] Error downloading file {file_id}: {e}")
                return False
            except Exception as e:
                if os.path.exists(tmp_dest):
                    os.remove(tmp_dest)
                if attempt < 2:
                    self._sleep_backoff(attempt)
                    continue
                print(f"[API] Error downloading file {file_id}: {e}")
                return False
        return False

    def export_google_doc(self, file_id: str, export_mime: str, dest_path: str) -> bool:
        """Export a native Google Workspace document (Docs/Sheets/Slides) to an offline format (.odt/.docx/etc.)."""
        params = {"mimeType": export_mime}
        url = f"{DRIVE_API_BASE}/files/{urllib.parse.quote(file_id)}/export?{urllib.parse.urlencode(params)}"
        tmp_dest = f"{dest_path}.tmp"
        for attempt in range(3):
            req = urllib.request.Request(url, headers=self._get_headers())
            try:
                os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
                with self._urlopen(req, timeout=60) as resp, open(tmp_dest, "wb") as f_out:
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        f_out.write(chunk)
                if os.path.exists(dest_path):
                    os.remove(dest_path)
                os.rename(tmp_dest, dest_path)
                return True
            except urllib.error.HTTPError as e:
                if os.path.exists(tmp_dest):
                    os.remove(tmp_dest)
                if e.code == 401 and attempt == 0:
                    self.auth_manager.refresh_access_token(self.account_id)
                    continue
                if e.code in (429, 500, 502, 503) and attempt < 2:
                    self._sleep_backoff(attempt)
                    continue
                print(f"[API] Error exporting Google Doc {file_id} as {export_mime}: {e}")
                return False
            except Exception as e:
                if os.path.exists(tmp_dest):
                    os.remove(tmp_dest)
                print(f"[API] Error exporting Google Doc {file_id}: {e}")
                return False
        return False

    # --- Chunked Resumable Uploads (8 MB chunks, constant RAM) ---
    def _upload_resumable_chunks(
        self,
        session_url: str,
        local_path: str,
        file_size: int,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Stream local_path to a Google resumable upload session in 8 MB chunks."""
        if file_size == 0:
            put_req = urllib.request.Request(
                session_url,
                data=b"",
                headers={"Content-Length": "0", "Content-Type": "application/octet-stream"},
                method="PUT",
            )
            with self._urlopen(put_req, timeout=30) as put_resp:
                return json.loads(put_resp.read().decode("utf-8"))

        offset = 0
        with open(local_path, "rb") as f:
            while offset < file_size:
                f.seek(offset)
                chunk = f.read(RESUMABLE_CHUNK_SIZE)
                if not chunk:
                    break
                end = offset + len(chunk) - 1
                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Type": "application/octet-stream",
                    "Content-Range": f"bytes {offset}-{end}/{file_size}",
                }

                chunk_ok = False
                for attempt in range(4):
                    put_req = urllib.request.Request(session_url, data=chunk, headers=headers, method="PUT")
                    try:
                        with self._urlopen(put_req, timeout=90) as put_resp:
                            raw = put_resp.read().decode("utf-8")
                            if progress_cb:
                                progress_cb(end + 1, file_size)
                            return json.loads(raw) if raw.strip() else {}
                    except urllib.error.HTTPError as e:
                        if e.code == 308:
                            # Resume Incomplete -> advance offset based on Range header if present
                            rng = e.headers.get("Range") if e.headers else None
                            if rng and "-" in rng:
                                try:
                                    offset = int(rng.split("-")[-1]) + 1
                                except ValueError:
                                    offset = end + 1
                            else:
                                offset = end + 1
                            if progress_cb:
                                progress_cb(offset, file_size)
                            chunk_ok = True
                            break
                        if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                            self._sleep_backoff(attempt)
                            continue
                        raise
                    except Exception:
                        if attempt < 3:
                            self._sleep_backoff(attempt)
                            continue
                        raise

                if not chunk_ok:
                    return None
        return None

    def upload_new_file(
        self,
        local_path: str,
        name: str,
        parent_id: Optional[str] = "root",
        mime_type: Optional[str] = None,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Upload a newly created local file using chunked resumable upload protocol."""
        metadata: Dict[str, Any] = {
            "name": name,
            "parents": [parent_id] if parent_id else ["root"],
        }
        if mime_type:
            metadata["mimeType"] = mime_type
        file_size = os.path.getsize(local_path)

        init_headers = self._get_headers({
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "application/octet-stream",
            "X-Upload-Content-Length": str(file_size),
        })
        init_url = f"{UPLOAD_API_BASE}/files?uploadType=resumable&supportsAllDrives=true&fields=id,name,mimeType,parents,size,md5Checksum,modifiedTime,createdTime"
        req = urllib.request.Request(init_url, data=json.dumps(metadata).encode("utf-8"), headers=init_headers, method="POST")

        try:
            with self._urlopen(req, timeout=20) as resp:
                session_url = resp.headers.get("Location")
            if not session_url:
                return None
            return self._upload_resumable_chunks(session_url, local_path, file_size, progress_cb=progress_cb)
        except Exception as e:
            print(f"[API] Error uploading file {name}: {e}")
            return None

    def update_file_content(
        self,
        file_id: str,
        local_path: str,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update existing file content on Google Drive via chunked resumable upload."""
        file_size = os.path.getsize(local_path)
        init_headers = self._get_headers({
            "X-Upload-Content-Type": "application/octet-stream",
            "X-Upload-Content-Length": str(file_size),
        })
        init_url = f"{UPLOAD_API_BASE}/files/{urllib.parse.quote(file_id)}?uploadType=resumable&supportsAllDrives=true&fields=id,name,mimeType,parents,size,md5Checksum,modifiedTime"
        req = urllib.request.Request(init_url, data=b"", headers=init_headers, method="PATCH")

        try:
            with self._urlopen(req, timeout=20) as resp:
                session_url = resp.headers.get("Location")
            if not session_url:
                return None
            return self._upload_resumable_chunks(session_url, local_path, file_size, progress_cb=progress_cb)
        except Exception as e:
            print(f"[API] Error updating content for {file_id}: {e}")
            return None

    # --- Folders, Safe Trash & Rename ---
    def create_folder(self, name: str, parent_id: Optional[str] = "root") -> Optional[Dict[str, Any]]:
        """Create a folder on Google Drive."""
        url = f"{DRIVE_API_BASE}/files?supportsAllDrives=true&fields=id,name,mimeType,parents,modifiedTime,createdTime"
        payload = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id] if parent_id else ["root"],
        }
        return self._request_json(url, method="POST", payload=payload, timeout=15)

    def trash_file(self, file_id: str) -> bool:
        """Safely move a file or folder to Google Drive Trash (reversible)."""
        url = f"{DRIVE_API_BASE}/files/{urllib.parse.quote(file_id)}?supportsAllDrives=true"
        res = self._request_json(url, method="PATCH", payload={"trashed": True}, timeout=12)
        return res is not None

    def delete_file(self, file_id: str, permanent: bool = False) -> bool:
        """Delete a file; defaults to safe Trash unless permanent=True."""
        if not permanent:
            return self.trash_file(file_id)
        url = f"{DRIVE_API_BASE}/files/{urllib.parse.quote(file_id)}?supportsAllDrives=true"
        req = urllib.request.Request(url, headers=self._get_headers(), method="DELETE")
        try:
            with self._urlopen(req, timeout=10) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            print(f"[API] Error permanently deleting {file_id}: {e}")
            return False

    def rename_file(self, file_id: str, new_name: str) -> bool:
        url = f"{DRIVE_API_BASE}/files/{urllib.parse.quote(file_id)}?supportsAllDrives=true"
        res = self._request_json(url, method="PATCH", payload={"name": new_name}, timeout=12)
        return res is not None
