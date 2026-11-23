"""Repeatable evaluation of parser + eligibility + scoring against a labelled JD dataset.

Used by the test-suite and by ``scripts/evaluate_matching.py`` (which writes the human-review report).
Nothing here tunes weights: it only measures and reports.
"""
from dataclasses import dataclass, field

from ai.schemas.candidate_profile import CandidateProfile
from ai.schemas.job_schema import JobInput
from config.scoring_config import Preferences, ScoringConfig
from engines.matching.jd_parser import parse_jd
from engines.matching.matching_engine import run_match
from engines.matching.skill_extractor import SkillCatalog


@dataclass
class EvalRow:
    jd: dict
    analysis: object
    report: object
    diffs: list[str] = field(default_factory=list)
    explanation_issues: list[str] = field(default_factory=list)
    mandatory_tp: int = 0
    mandatory_fp: int = 0
    mandatory_fn: int = 0
    preferred_tp: int = 0
    preferred_fp: int = 0
    preferred_fn: int = 0

    @property
    def score(self) -> float:
        return self.report.match_score

    @property
    def distance_outside_range(self) -> float:
        lo, hi = self.jd["score_range"]
        return max(0.0, lo - self.score, self.score - hi)

    @property
    def passed(self) -> bool:
        return not self.diffs and not self.explanation_issues


def _prf(extracted: set, expected: set):
    return len(extracted & expected), len(extracted - expected), len(expected - extracted)


def check_explanation(report) -> list[str]:
    """Consistency of the explanation with the deterministic facts."""
    issues = []
    if f"{report.match_score:g}/100" not in report.explanation:
        issues.append("explanation does not state the numeric score")
    if report.eligibility.status not in report.explanation:
        issues.append("explanation does not state the eligibility status")
    if report.eligibility.status == "FAIL" and report.recommendation_code != "NOT_ELIGIBLE":
        issues.append("failed eligibility but recommendation is not NOT_ELIGIBLE")
    if report.eligibility.status != "PASS" and report.recommendation_code == "RECOMMENDED":
        issues.append("recommended despite eligibility not PASS")
    for s in report.missing_mandatory_skills:
        if any(s in line for line in report.strengths):
            issues.append(f"missing mandatory skill '{s}' appears in strengths")
    if set(report.matched_skills) & set(report.missing_mandatory_skills + report.missing_preferred_skills):
        issues.append("a skill is both matched and missing")
    if report.eligibility.status == "FAIL" and not report.rejection_reasons:
        issues.append("FAIL without rejection reasons")
    if not report.limitations:
        issues.append("no limitations listed")
    return issues


def evaluate_one(jd: dict, profile: CandidateProfile, cfg: ScoringConfig, prefs: Preferences, catalog: SkillCatalog,
                 ai_client=None) -> EvalRow:
    job = JobInput(job_description=jd["text"], job_title=jd.get("title"), company_name=jd.get("company"),
                   location=jd.get("location"))
    analysis = parse_jd(job, catalog, ai_client=ai_client, bands=cfg.seniority.bands)
    report = run_match(profile, analysis, f"eval-{jd['id']}", cfg, prefs, catalog)
    row = EvalRow(jd=jd, analysis=analysis, report=report)

    exp_m, exp_p = set(jd["mandatory"]), set(jd["preferred"])
    got_m, got_p = set(analysis.mandatory_skills), set(analysis.preferred_skills)
    row.mandatory_tp, row.mandatory_fp, row.mandatory_fn = _prf(got_m, exp_m)
    row.preferred_tp, row.preferred_fp, row.preferred_fn = _prf(got_p, exp_p)
    if got_m != exp_m:
        row.diffs.append(f"mandatory skills: missing {sorted(exp_m - got_m) or '-'}, unexpected {sorted(got_m - exp_m) or '-'}")
    if got_p != exp_p:
        row.diffs.append(f"preferred skills: missing {sorted(exp_p - got_p) or '-'}, unexpected {sorted(got_p - exp_p) or '-'}")
    if "groups" in jd and sorted(map(sorted, analysis.mandatory_groups)) != sorted(map(sorted, jd["groups"])):
        row.diffs.append(f"mandatory any-of groups: got {analysis.mandatory_groups}")
    if report.eligibility.status != jd["eligibility"]:
        row.diffs.append(f"eligibility: expected {jd['eligibility']}, got {report.eligibility.status} "
                         f"(failed={report.eligibility.failed_checks}, unresolved={report.eligibility.unresolved_checks})")
    for name in jd.get("failed", []):
        if name not in report.eligibility.failed_checks:
            row.diffs.append(f"expected failed check '{name}' not failed (failed={report.eligibility.failed_checks})")
    for name in jd.get("soft_fail", []):
        if not any(c.name == name and c.status == "FAIL" and not c.hard for c in report.eligibility.checks):
            row.diffs.append(f"expected soft (preference) failure '{name}'")
    if report.classification not in jd["classes"]:
        row.diffs.append(f"classification: expected one of {jd['classes']}, got {report.classification}")
    if row.distance_outside_range > 0:
        row.diffs.append(f"score {report.match_score} outside expected range {jd['score_range']}")
    for s in jd.get("missing_preferred_includes", []):
        if s not in report.missing_preferred_skills:
            row.diffs.append(f"expected '{s}' among missing preferred skills (got {report.missing_preferred_skills})")
    if "max_coverage" in jd and report.data_coverage > jd["max_coverage"]:
        row.diffs.append(f"data coverage {report.data_coverage} should be <= {jd['max_coverage']} for this incomplete JD")
    if jd.get("expect_warning") and not analysis.warnings:
        row.diffs.append("expected parser warnings for an incomplete/ambiguous JD")
    row.explanation_issues = check_explanation(report)
    return row


def evaluate_dataset(jds, profile, cfg, prefs, catalog, ai_client=None) -> list[EvalRow]:
    return [evaluate_one(jd, profile, cfg, prefs, catalog, ai_client) for jd in jds]


def metrics(rows: list[EvalRow]) -> dict:
    def pr(tp, fp, fn):
        return (tp / (tp + fp) if tp + fp else 1.0), (tp / (tp + fn) if tp + fn else 1.0)

    mp, mr = pr(sum(r.mandatory_tp for r in rows), sum(r.mandatory_fp for r in rows), sum(r.mandatory_fn for r in rows))
    pp, prc = pr(sum(r.preferred_tp for r in rows), sum(r.preferred_fp for r in rows), sum(r.preferred_fn for r in rows))
    n = len(rows)
    return {
        "jds": n,
        "mandatory_precision": mp, "mandatory_recall": mr, "preferred_precision": pp, "preferred_recall": prc,
        "eligibility_accuracy": sum(r.report.eligibility.status == r.jd["eligibility"] for r in rows) / n,
        "classification_accuracy": sum(r.report.classification in r.jd["classes"] for r in rows) / n,
        "scores_in_expected_range": sum(r.distance_outside_range == 0 for r in rows) / n,
        "mean_distance_outside_range": sum(r.distance_outside_range for r in rows) / n,
        "fully_passed": sum(r.passed for r in rows),
        "explanation_consistent": sum(not r.explanation_issues for r in rows) / n,
    }


def render_markdown(rows: list[EvalRow], cfg: ScoringConfig) -> str:
    m = metrics(rows)
    out = [
        "# Phase 3 - Matching Evaluation Report (30 synthetic JDs)",
        "",
        "> **Manual validation status: NOT DONE.** Everything below is produced by the automated evaluation. "
        "The *Reviewer comments* and *Human verdict* columns are intentionally empty - a person must review every "
        "JD and sign off. Automated agreement with my own written expectations is not human validation.",
        "",
        f"* Scoring version `{cfg.scoring_version}`, config hash `{cfg.config_hash}`; weights {cfg.weights}",
        "* Candidate: synthetic profile (`tests/fixtures/candidate.py`); preferences: see `tests/conftest.py`.",
        "* Expectations (skills, eligibility, class, score band) were written from each JD *before* the engine was run.",
        "* No reference scores exist, so 'score difference' = distance outside the expected band (0 = inside).",
        "",
        "## Aggregate metrics",
        "",
        "| Metric | Value |", "|---|---|",
        f"| JDs | {m['jds']} |",
        f"| Mandatory-skill extraction precision / recall | {m['mandatory_precision']:.2f} / {m['mandatory_recall']:.2f} |",
        f"| Preferred-skill extraction precision / recall | {m['preferred_precision']:.2f} / {m['preferred_recall']:.2f} |",
        f"| Eligibility accuracy | {m['eligibility_accuracy']:.0%} |",
        f"| Classification within expected set | {m['classification_accuracy']:.0%} |",
        f"| Scores inside expected band | {m['scores_in_expected_range']:.0%} |",
        f"| Mean distance outside band | {m['mean_distance_outside_range']:.2f} points |",
        f"| Explanations consistent with facts | {m['explanation_consistent']:.0%} |",
        f"| JDs with zero automated differences | {m['fully_passed']} / {m['jds']} |",
        "",
        "## Per-JD results",
        "",
        "| ID | Scenario | Exp. eligibility | Actual | Exp. class | Actual class | Score | Band | Dist. | Mandatory P/R | Preferred P/R | Auto result | Human verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        def p(tp, fp, fn):
            return f"{tp / (tp + fp) if tp + fp else 1:.2f}/{tp / (tp + fn) if tp + fn else 1:.2f}"
        out.append(
            f"| {r.jd['id']} | {r.jd['scenario']} | {r.jd['eligibility']} | {r.report.eligibility.status} | "
            f"{' / '.join(c.replace(' Match', '') for c in r.jd['classes'])} | {r.report.classification} | {r.score:g} | "
            f"{r.jd['score_range'][0]}-{r.jd['score_range'][1]} | {r.distance_outside_range:g} | "
            f"{p(r.mandatory_tp, r.mandatory_fp, r.mandatory_fn)} | {p(r.preferred_tp, r.preferred_fp, r.preferred_fn)} | "
            f"{'PASS' if r.passed else 'DIFF'} | ☐ pending |")
    out += ["", "## Details for the human reviewer", ""]
    for r in rows:
        rep = r.report
        out += [
            f"### {r.jd['id']} - {r.jd['scenario']}",
            f"*Why this result is expected:* {r.jd['why']}",
            "",
            f"* **Automated differences:** {'; '.join(r.diffs) if r.diffs else 'none'}",
            f"* **Explanation issues:** {'; '.join(r.explanation_issues) if r.explanation_issues else 'none'}",
            f"* **Extracted mandatory:** {r.analysis.mandatory_skills} (groups {r.analysis.mandatory_groups})",
            f"* **Extracted preferred:** {r.analysis.preferred_skills}; examples: {r.analysis.example_skills}; mentioned: {r.analysis.mentioned_skills}",
            f"* **Experience:** min {r.analysis.minimum_experience_years}, max {r.analysis.maximum_experience_years}; "
            f"seniority {r.analysis.seniority_level} ({r.analysis.seniority_source}); mode {r.analysis.work_mode}; location {r.analysis.location}",
            f"* **Score:** {rep.match_score} ({rep.classification}), coverage {rep.data_coverage}%, breakdown {rep.score_breakdown}",
            f"* **Eligibility:** {rep.eligibility.status}; " + ", ".join(f"{c.name}={c.status}" for c in rep.eligibility.checks if c.status != "NOT_APPLICABLE"),
            f"* **Matched:** {rep.matched_skills}; **missing mandatory:** {rep.missing_mandatory_skills}; "
            f"**missing preferred:** {rep.missing_preferred_skills}; **partial:** {[(p.candidate_skill, p.required_skill) for p in rep.partial_matches]}",
            f"* **Recommendation:** {rep.recommendation}",
            f"* **Explanation:** {rep.explanation}",
            "* **Reviewer comments:** _(to be filled in by a human)_",
            "",
        ]
    out += [
        "## Human sign-off", "",
        "| Reviewer | Date | JDs reviewed | Verdict (approve / changes needed) | Comments |", "|---|---|---|---|---|",
        "| _pending_ | | 0 / 30 | | |", "",
    ]
    return "\n".join(out)
