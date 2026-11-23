"""Change single values in a YAML file WITHOUT losing its comments (PyYAML would drop them). Handles top-level keys and one nested level, which is
all the config files need. Values are written in one-line flow style; a trailing ``# comment`` on the line is kept."""
import json
import re

import yaml


def dump_value(value) -> str:
    out = yaml.safe_dump(value, default_flow_style=True, width=10**6, allow_unicode=True).strip()
    out = out[:-3].strip() if out.endswith("...") else out
    if isinstance(value, str) and out.startswith("'"):          # PyYAML single-quotes strings that look like numbers; our files use double quotes
        return json.dumps(value, ensure_ascii=False)
    return out


_SPLIT = re.compile(r"^(?P<head>\s*[A-Za-z0-9_\"']+\s*:\s*)(?P<val>.*?)(?P<comment>\s+#.*)?$")


def _replace_in_line(line: str, value) -> str:
    m = _SPLIT.match(line)
    if not m:
        raise KeyError(line)
    return f"{m['head']}{dump_value(value)}{m['comment'] or ''}"


def set_value(text: str, path: tuple[str, ...], value) -> str:
    """Return ``text`` with ``path`` (1 or 2 keys) set to ``value``. KeyError if the key does not exist."""
    lines = text.splitlines()
    if len(path) == 1:
        rx = re.compile(rf"^{re.escape(path[0])}\s*:")
        for i, ln in enumerate(lines):
            if rx.match(ln):
                lines[i] = _replace_in_line(ln, value)
                return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
        raise KeyError(path)
    parent, child = path
    prx, crx = re.compile(rf"^{re.escape(parent)}\s*:\s*(#.*)?$"), re.compile(rf"^(\s+){re.escape(child)}\s*:")
    in_parent = False
    for i, ln in enumerate(lines):
        if not in_parent:
            in_parent = bool(prx.match(ln))
            continue
        if ln.strip() and not ln.startswith((" ", "\t", "#")):     # dedent: the parent block ended
            break
        if crx.match(ln):
            lines[i] = _replace_in_line(ln, value)
            return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    raise KeyError(path)
