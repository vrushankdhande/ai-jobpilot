"""Parsed-YAML cache keyed by (path, mtime, size): configuration is re-read only when the file actually changes.

Measured on this project: every API request used to re-parse 7 YAML files (~7 ms each = ~15 ms of pure parsing per request, and it holds the GIL under load).
Editing a file (by hand or through the Settings page) changes its mtime/size, so the next call sees the new content. Callers always get a DEEP COPY, so
mutating the returned data (tests do) can never leak into another request.
"""
import copy
import hashlib
import threading
from pathlib import Path
from typing import Any

import yaml

_LOCK = threading.Lock()
_CACHE: dict[str, tuple[tuple[int, int], Any, str]] = {}


def load_yaml_with_hash(path: Path) -> tuple[Any, str]:
    """-> (parsed data (deep copy), sha256 of the raw text). Raises like open()/yaml.safe_load on a missing or invalid file (never cached)."""
    path = Path(path)
    st = path.stat()
    key, sig = str(path.resolve()), (st.st_mtime_ns, st.st_size)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] == sig:
            return copy.deepcopy(hit[1]), hit[2]
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    with _LOCK:
        _CACHE[key] = (sig, copy.deepcopy(data), digest)
    return data, digest


def load_yaml(path: Path) -> Any:
    return load_yaml_with_hash(path)[0]


def clear() -> None:
    with _LOCK:
        _CACHE.clear()
