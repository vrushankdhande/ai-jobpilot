"""Keeps personal data out of logs and audit events: only ids, states, counts and short non-personal text may pass."""
import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d")
_URL_CREDS = re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@")
FORBIDDEN_KEYS = re.compile(r"email|phone|name|address|value|answer|password|token|cookie|secret|salary|resume_text|content|text", re.I)
ALLOWED_VALUE_KEYS = {"company_name_present", "field_count", "counts"}  # reserved: explicit exceptions


def mask(text: str | None) -> str:
    t = _URL_CREDS.sub(r"\1***@", text or "")
    t = _EMAIL.sub("[email]", t)
    return _PHONE.sub("[phone]", t)


def safe_detail(detail: dict | None, max_str: int = 200) -> dict:
    """Allow-list for audit events: scalars and small lists of scalars; keys that look personal are dropped; strings are masked."""
    out: dict = {}
    for k, v in (detail or {}).items():
        if FORBIDDEN_KEYS.search(str(k)) and k not in ALLOWED_VALUE_KEYS:
            continue
        if isinstance(v, (bool, int, float)) or v is None:
            out[k] = v
        elif isinstance(v, str):
            out[k] = mask(v)[:max_str]
        elif isinstance(v, (list, tuple)) and all(isinstance(x, (str, int, float, bool)) for x in v):
            out[k] = [mask(x)[:max_str] if isinstance(x, str) else x for x in list(v)[:25]]
        elif isinstance(v, dict):
            out[k] = safe_detail(v, max_str)
    return out
