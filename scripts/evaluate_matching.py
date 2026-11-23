"""Run the 30-JD evaluation and write docs/phase3_evaluation_report.md for human review.

Usage:  .venv\\Scripts\\python.exe scripts/evaluate_matching.py
Deterministic engine only (no Claude calls). Does not change any weights.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.scoring_config import load_scoring_config  # noqa: E402
from engines.matching.evaluation import evaluate_dataset, metrics, render_markdown  # noqa: E402
from engines.matching.skill_extractor import get_catalog  # noqa: E402
from tests.fixtures.candidate import synthetic_profile  # noqa: E402
from tests.fixtures.jd_dataset import JDS  # noqa: E402
from tests.fixtures.preferences import evaluation_preferences  # noqa: E402


def main() -> int:
    cfg, catalog = load_scoring_config(), get_catalog()
    rows = evaluate_dataset(JDS, synthetic_profile(), cfg, evaluation_preferences(), catalog)
    out = ROOT / "docs" / "phase3_evaluation_report.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(render_markdown(rows, cfg), encoding="utf-8")
    m = metrics(rows)
    print(f"Wrote {out}")
    for k, v in m.items():
        print(f"  {k}: {v:.3f}" if isinstance(v, float) else f"  {k}: {v}")
    for r in rows:
        if not r.passed:
            print(f"  [DIFF] {r.jd['id']} score={r.score}: " + " | ".join(r.diffs + r.explanation_issues))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
