"""
Google Drive for Linux - Offline Google Docs / Sheets / Slides Conversion Engine
Supports 3 modes:
  - LINK: Lightweight .gdoc/.gsheet/.gslides shortcuts (opens in WebKit2 pt-BR app)
  - OPEN_DOCUMENT: Exports to .odt/.ods/.odp (native LibreOffice offline editing)
  - MS_OFFICE: Exports to .docx/.xlsx/.pptx (Microsoft Office format)
"""
import os
from typing import Optional, Tuple, Dict

GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
GOOGLE_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
GOOGLE_SLIDE_MIME = "application/vnd.google-apps.presentation"
GOOGLE_DRAW_MIME = "application/vnd.google-apps.drawing"

EXPORT_PROFILES: Dict[str, Dict[str, Tuple[str, str]]] = {
    "OPEN_DOCUMENT": {
        GOOGLE_DOC_MIME: ("application/vnd.oasis.opendocument.text", ".odt"),
        GOOGLE_SHEET_MIME: ("application/vnd.oasis.opendocument.spreadsheet", ".ods"),
        GOOGLE_SLIDE_MIME: ("application/vnd.oasis.opendocument.presentation", ".odp"),
        GOOGLE_DRAW_MIME: ("image/svg+xml", ".svg"),
    },
    "MS_OFFICE": {
        GOOGLE_DOC_MIME: ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
        GOOGLE_SHEET_MIME: ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
        GOOGLE_SLIDE_MIME: ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
        GOOGLE_DRAW_MIME: ("image/png", ".png"),
    },
}

LINK_EXTENSIONS: Dict[str, str] = {
    GOOGLE_DOC_MIME: ".gdoc",
    GOOGLE_SHEET_MIME: ".gsheet",
    GOOGLE_SLIDE_MIME: ".gslides",
    "application/vnd.google-apps.form": ".gform",
    GOOGLE_DRAW_MIME: ".gdraw",
}


def is_google_workspace_doc(mime_type: str) -> bool:
    return bool(mime_type and mime_type.startswith("application/vnd.google-apps.") and mime_type != "application/vnd.google-apps.folder")


def get_export_target(mime_type: str, mode: str = "LINK") -> Optional[Tuple[str, str]]:
    """
    Return (export_mime_type, file_extension) for a Google Workspace document under the given mode.
    Returns None if mode is LINK or if the MIME type is not exportable.
    """
    mode_norm = (mode or "LINK").upper()
    if mode_norm == "LINK":
        return None
    profile = EXPORT_PROFILES.get(mode_norm)
    if not profile:
        return None
    return profile.get(mime_type)


def get_local_extension(mime_type: str, mode: str = "LINK") -> str:
    """Return the local filename extension for a Google Workspace document."""
    target = get_export_target(mime_type, mode)
    if target:
        return target[1]
    return LINK_EXTENSIONS.get(mime_type, ".gdoc")


def export_document_offline(api, item: Dict, dest_dir: str, mode: str = "OPEN_DOCUMENT") -> Optional[str]:
    """
    Export a Google Workspace document to an editable offline file (.odt/.ods/.odp or .docx/.xlsx/.pptx).
    Returns the exported local file path on success, or None.
    """
    mime_type = item.get("mime_type", "")
    target = get_export_target(mime_type, mode)
    if not target:
        return None

    export_mime, ext = target
    safe_name = (item.get("name") or item.get("id") or "document").replace("/", "／")
    if not safe_name.endswith(ext):
        safe_name = f"{safe_name}{ext}"

    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, safe_name)

    if api.export_google_doc(item["id"], export_mime, dest_path):
        return dest_path
    return None
