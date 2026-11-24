"""
Google Drive for Linux - .gitignore-Compatible Ignore Rules Engine
Filters temporary files, build artifacts, and user-defined patterns before syncing.
"""
import os
import fnmatch
import re
from typing import List, Tuple
from core.config import CONFIG_DIR

IGNORE_RULES_PATH = os.path.join(CONFIG_DIR, "ignore_rules.txt")

DEFAULT_IGNORE_PATTERNS = [
    # Virtual FUSE volume metadata
    ".xdg-volume-info",
    ".volume-icon.png",
    # OS & File Manager junk
    ".Trash-*",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    # Editor & Office lock / swap files
    ".~lock.*",
    "~$*",
    "*.swp",
    "*.swo",
    "*~",
    "*.tmp",
    "*.part",
    "*.crdownload",
    # Common developer caches (uncomment or add in user rules as needed)
    "__pycache__/",
    "*.pyc",
]


class IgnoreRulesEngine:
    """
    Case-sensitive .gitignore-style matcher supporting:
      - Comments (# ...) and blank lines
      - Directory-only rules (trailing '/')
      - Relative path rules containing '/'
      - Wildcards (*, ?, **)
      - Negation rules (!pattern)
    """

    def __init__(self, rules_path: str = IGNORE_RULES_PATH, extra_patterns: List[str] = None):
        self.rules_path = rules_path
        self._compiled: List[Tuple[bool, bool, bool, re.Pattern]] = []
        self._mtime: float = 0.0
        self._extra_patterns = list(extra_patterns or [])
        self._ensure_default_file()
        self.reload(force=True)

    def _ensure_default_file(self):
        try:
            os.makedirs(os.path.dirname(self.rules_path), exist_ok=True)
            if not os.path.exists(self.rules_path):
                header = (
                    "# Google Drive for Linux - Regras de Exclusão (sintaxe .gitignore)\n"
                    "# Linhas iniciadas com # são comentários. Use '!' para negar uma regra.\n"
                    "# Exemplo para ignorar pasta: node_modules/\n"
                    "# Exemplo para ignorar caminho relativo: Projetos/Privado/\n\n"
                )
                with open(self.rules_path, "w", encoding="utf-8") as f:
                    f.write(header + "\n".join(DEFAULT_IGNORE_PATTERNS) + "\n")
        except Exception:
            pass

    @staticmethod
    def _glob_to_regex(pattern: str, anchored: bool) -> re.Pattern:
        """Convert a gitignore glob pattern (including **) into a compiled regex."""
        i = 0
        n = len(pattern)
        res = []
        while i < n:
            c = pattern[i]
            if c == "*":
                if i + 1 < n and pattern[i + 1] == "*":
                    # '**' matches across directory boundaries
                    if i + 2 < n and pattern[i + 2] == "/":
                        res.append("(?:.*/)?")
                        i += 3
                        continue
                    res.append(".*")
                    i += 2
                    continue
                res.append("[^/]*")
            elif c == "?":
                res.append("[^/]")
            elif c == "[":
                j = pattern.find("]", i + 1)
                if j == -1:
                    res.append(r"\[")
                else:
                    res.append(pattern[i:j + 1])
                    i = j
            else:
                res.append(re.escape(c))
            i += 1

        body = "".join(res)
        if anchored:
            return re.compile(f"^{body}$")
        return re.compile(f"(?:^|/){body}$")

    def _compile_lines(self, lines: List[str]):
        compiled = []
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negated = False
            if line.startswith("!"):
                negated = True
                line = line[1:].strip()
                if not line:
                    continue
            dir_only = line.endswith("/")
            if dir_only:
                line = line.rstrip("/")
            anchored = "/" in line
            if line.startswith("/"):
                line = line.lstrip("/")
            try:
                regex = self._glob_to_regex(line, anchored=anchored)
                compiled.append((negated, dir_only, anchored, regex))
            except re.error:
                continue
        self._compiled = compiled

    def reload(self, force: bool = False):
        """Reload rules from disk if the file changed."""
        lines = list(DEFAULT_IGNORE_PATTERNS) + list(self._extra_patterns)
        if os.path.exists(self.rules_path):
            try:
                mtime = os.path.getmtime(self.rules_path)
                if not force and mtime == self._mtime:
                    return
                with open(self.rules_path, "r", encoding="utf-8") as f:
                    lines = f.readlines() + list(self._extra_patterns)
                self._mtime = mtime
            except Exception:
                pass
        self._compile_lines(lines)

    def get_rules_text(self) -> str:
        self._ensure_default_file()
        try:
            with open(self.rules_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return "\n".join(DEFAULT_IGNORE_PATTERNS) + "\n"

    def save_rules_text(self, text: str) -> bool:
        try:
            os.makedirs(os.path.dirname(self.rules_path), exist_ok=True)
            with open(self.rules_path, "w", encoding="utf-8") as f:
                f.write(text)
            self.reload(force=True)
            return True
        except Exception:
            return False

    def is_ignored(self, rel_path: str, is_dir: bool = False) -> bool:
        """
        Return True if the relative path (e.g. 'Folder/sub/file.tmp' or 'node_modules')
        matches the active ignore rules.
        """
        self.reload(force=False)
        clean = (rel_path or "").replace("\\", "/").strip("/")
        if not clean:
            return False

        # Check every path prefix for directory ignores (e.g. if 'node_modules/' is ignored,
        # 'node_modules/pkg/index.js' is also ignored).
        parts = clean.split("/")
        ignored = False

        for negated, dir_only, _anchored, regex in self._compiled:
            matched = False
            if dir_only:
                # Match any parent directory segment, or the path itself if is_dir
                check_end = len(parts) if is_dir else len(parts) - 1
                for idx in range(1, check_end + 1):
                    sub = "/".join(parts[:idx])
                    if regex.search(sub):
                        matched = True
                        break
            else:
                if regex.search(clean):
                    matched = True
                else:
                    # Also check if a parent directory itself matched a non-dir-only folder rule
                    for idx in range(1, len(parts)):
                        sub = "/".join(parts[:idx])
                        if regex.search(sub):
                            matched = True
                            break

            if matched:
                ignored = not negated

        return ignored
