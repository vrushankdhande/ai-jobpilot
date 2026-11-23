"""Skill normalisation: a configurable dictionary (config/skills.yaml).

* ``normalize`` maps messy terms ("Gen AI", "Py Torch", "SKLearn") to one canonical skill.
* ``find_in_text`` finds explicit skill mentions in prose.
* ``match`` compares a required skill with a candidate's skills: exact = full credit,
  a *documented* relationship = partial credit, anything else = none. Same category is
  never enough (Python != Java, TensorFlow != PyTorch).
"""
import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from config.settings import Settings


def skill_key(text: str) -> str:
    """Spacing/punctuation-insensitive key: 'Py Torch' -> 'pytorch', 'Node.js' -> 'nodejs'."""
    text = re.sub(r"\([^)]*\)", "", text.lower())
    return re.sub(r"[\s\-_./]+", "", text).strip()


@dataclass(frozen=True)
class SkillMention:
    canonical: str
    written: str
    start: int
    end: int


@dataclass
class SkillMatch:
    kind: str  # "exact" | "related" | "none"
    credit: float = 0.0
    candidate_skill: str | None = None
    reason: str | None = None


@dataclass
class SkillCatalog:
    skills: dict[str, dict] = field(default_factory=dict)  # canonical -> {category, ambiguous}
    alias_to_canonical: dict[str, str] = field(default_factory=dict)  # skill_key -> canonical
    edges: dict[tuple[str, str], tuple[float, str]] = field(default_factory=dict)  # (have, need) -> (credit, reason)
    _scan_re: re.Pattern | None = None
    _surface_to_canonical: dict[str, str] = field(default_factory=dict)
    digest: str = ""  # hash of skills.yaml: changing the dictionary invalidates cached analyses/matches

    # ---- build ---------------------------------------------------------------
    @classmethod
    def from_file(cls, path: Path) -> "SkillCatalog":
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
        cat = cls(digest=hashlib.sha256(raw.encode()).hexdigest()[:12])
        for s in data.get("skills", []):
            name = s["name"]
            cat.skills[name] = {"category": s.get("category", "tools"), "ambiguous": bool(s.get("ambiguous"))}
            for form in [name, *s.get("aliases", [])]:
                cat.alias_to_canonical.setdefault(skill_key(form), name)
                if not (s.get("ambiguous") and form == name) and len(skill_key(form)) > 1:
                    cat._surface_to_canonical[form.lower()] = name
        for g in data.get("related_groups", []):
            members = [m for m in g["members"] if m in cat.skills]
            for a in members:
                for b in members:
                    if a != b and g.get("credit", 0) > 0:
                        cat.edges[(a, b)] = (float(g["credit"]), g.get("reason", "Related skills"))
        for d in data.get("related_directed", []):
            if d["from"] in cat.skills and d["to"] in cat.skills:
                cat.edges[(d["from"], d["to"])] = (float(d["credit"]), d.get("reason", "Related skills"))
        cat._build_scan_regex()
        return cat

    def _build_scan_regex(self) -> None:
        forms = sorted(self._surface_to_canonical, key=len, reverse=True)
        parts = []
        for f in forms:
            tokens = [t for t in re.split(r"[\s\-_]+", f) if t]
            parts.append(r"[\s\-_]*".join(re.escape(t) for t in tokens))
        body = "|".join(parts)
        self._scan_re = re.compile(rf"(?<![A-Za-z0-9+#_])(?:{body})(?![A-Za-z0-9+#_]|\.[A-Za-z0-9])", re.IGNORECASE)

    # ---- lookups -------------------------------------------------------------
    def normalize(self, text: str) -> str | None:
        return self.alias_to_canonical.get(skill_key(text))

    def normalize_or_raw(self, text: str) -> str:
        """Canonical name if known, otherwise the cleaned original (supports unknown skills)."""
        return self.normalize(text) or re.sub(r"\s+", " ", text).strip(" .,;:")

    def category(self, canonical: str) -> str:
        return self.skills.get(canonical, {}).get("category", "tools")

    def find_in_text(self, text: str) -> list[SkillMention]:
        out = []
        for m in self._scan_re.finditer(text):
            canon = self.normalize(m.group(0)) or self._surface_to_canonical.get(m.group(0).lower())
            if canon:
                out.append(SkillMention(canon, m.group(0), m.start(), m.end()))
        return out

    def list_item_skill(self, item: str) -> str | None:
        """Whole-item lookup, also resolving ambiguous names such as 'R' or 'Go'."""
        return self.normalize(item.strip(" .,;:()"))

    # ---- matching ------------------------------------------------------------
    def match(self, required: str, candidate_skills: set[str]) -> SkillMatch:
        if required in candidate_skills:
            return SkillMatch("exact", 1.0, required)
        best = SkillMatch("none")
        for have in candidate_skills:
            hit = self.edges.get((have, required))
            if hit and hit[0] > best.credit:
                best = SkillMatch("related", hit[0], have, hit[1])
        return best


@lru_cache(maxsize=8)
def _load(path_str: str, mtime: float) -> SkillCatalog:
    return SkillCatalog.from_file(Path(path_str))


def get_catalog(settings: Settings | None = None, path: Path | None = None) -> SkillCatalog:
    """Cached per file modification time, so editing skills.yaml takes effect without a restart."""
    path = path or (settings or Settings()).config_path("skills_file")
    return _load(str(path), path.stat().st_mtime)
