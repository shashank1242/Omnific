"""Licence policy for reused models: CC0 and CC-BY only (attribution recorded); NC, ND, SA and unknown rejected.

SA is rejected too: a share-alike model would force the whole exported scene under CC-BY-SA.
"""

from __future__ import annotations

import re


def normalise(raw: str | None) -> str | None:
    """A display licence ("CC0 1.0", "CC-BY 4.0", ...) if allowed, else None."""
    s = " ".join((raw or "").lower().replace("_", " ").replace("-", " ").split())
    if not s:
        return None
    if re.search(r"\b(nc|nd|sa|noncommercial|non commercial|noderivatives|noderivs|no derivatives|sharealike|"
                 r"share alike)\b", s):
        return None
    version = re.search(r"\b(\d\.\d)\b", s)
    if s in ("cc0", "cc 0", "cc0 1.0", "cc 0 1.0", "cc zero") or "public domain dedication" in s \
            or s.startswith("cc0") or "creative commons zero" in s:
        return "CC0 1.0"
    if s in ("by", "cc by", "attribution") or s.startswith("cc by") or "creative commons attribution" in s:
        return f"CC-BY {version.group(1) if version else '4.0'}"
    return None


def credit(title: str, author: str, licence: str, url: str, source: str) -> str:
    return f'"{title}" by {author or "unknown"} ({source}), {licence}: {url}'
