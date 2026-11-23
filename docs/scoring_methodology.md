# Matching methodology (scoring version 1.1)

The numeric score, the eligibility result and every list in a match report are computed by **deterministic
code**. Claude (optional) may help parse a job description and may phrase a summary, but it never produces,
changes or overrides a number, a classification or an eligibility result.

## Pipeline
`verified profile + job description -> JD parser -> skill normalisation -> eligibility checker -> weighted scorer ->
missing-skill analysis -> explanation -> validated MatchReport -> MongoDB`.

## What changed in 1.1 (and why)
Found by running the engine on **377 real job postings** (11 companies) against a real, reviewed resume, then reading the evidence sentence behind
each failure. 69 % of the jobs failed; most failures were misreadings, not genuine gaps. Nothing was fixed by lowering a threshold.

| # | Fault (root cause) | Fix |
|---|---|---|
| 1 | Headings written with curly apostrophes ("What you’ll do") were not recognised, so whole JDs were read as one block (224 of 377 had *no* requirement section detected) | Typographic quotes/spaces are normalised first; more common headings added ("What we look for", "Your Expertise", "The impact you will have", "A Typical Day" ...) -> 3 of 377 |
| 2 | An unrecognised heading did not end the previous section, so "Pay Range", "About <Company>", diversity and legal boilerplate counted as requirements | Unknown title-like headings now end the section; boilerplate headings/sentences are ignored |
| 3 | The hiring company's own product was a mandatory skill (`Databricks` x63, `Elasticsearch`) | Own-product skills are at most *preferred* (learnable on the job), with a warning in the analysis |
| 4 | "strong plus", "huge pluses", "a willingness to learn it" were mandatory | Recognised as preferred wording |
| 5 | "at least three of the following: A, B, C", "one or more of", "including ...", "(e.g. ...)" and parenthesised lists made every item mandatory | Choose-N lists, "including" lists and parentheticals are *examples* |
| 6 | A degree *field* ("Master's in Statistics") became a Statistics skill; "Python for statistical modeling" made Statistics mandatory | Skills in degree sentences / "for ..." purpose phrases are not requirements |
| 7 | Skills named in *responsibilities* became mandatory on words like "strong" or "hands-on" | Only explicit wording ("required", "must", "5+ years") counts there |
| 8 | Certifications that are "huge pluses", "within 6 months of hire", or the employer's own certification were mandatory | Not gates; label words ("Mandatory Certifications:") are not certifications |
| 9 | "12+ years for M.S. holders / 8+ for Ph.D. holders" used the first number | Alternative routes use the lowest |
| 10 | A mandatory "Deep Learning" was not met by CNN/RNN/LSTM/Keras/TensorFlow (exact match only) | A documented near-equivalent (credit >= `eligibility.mandatory_skill_equivalent_credit`, 0.7) meets a mandatory skill; loose relatives (TensorFlow for PyTorch, 0.5) still do not. CNN/RNN/LSTM/Transformers/Transfer Learning/YOLO added to the dictionary with documented credits |
| 11 | **Scores ignored the function of the job**: a sales posting that mentions "machine learning" once earned full skills marks, so an Account Executive scored 74 and PASSED | New *role fit* factor (below) |

**Role fit** (`engines/matching/role_fit.py`, `role_fit:` in `config/scoring_weights.yaml`): the job's function is read from its title and compared with the
function(s) in the candidate's job titles. Same function x1.0, adjacent (e.g. product, customer-facing technical) x0.8, different (sales, marketing, finance,
legal, people) x0.4, unknown (either side) x1.0. A strong technical noun makes a title technical ("Data Scientist, Marketing"); "Sales Engineer" stays sales.
The factor is applied to the final score, stored in the report (`role_fit`, with `score_before_role_fit`) and listed as the first weakness, so it is never hidden.
It never rejects a job - eligibility stays a separate, hard-requirement check.

Resume parsing (not scoring): spoken languages ("English (Native)") are no longer technical skills; section headings such as "Competitions & Seminars" /
"Content & Publications" no longer fall into Certifications (they become achievements); page footers ("NAME - 2") are dropped. An existing profile is not
rewritten automatically: re-parse (needs `force`) and re-review to apply.

**Before / after (rules only, no AI; same profile v7).** Measured twice, both ways reproducible from the stored reports and the engine:

*All 377 jobs, 1.0 engine vs 1.1 engine run offline on the same data:* eligibility FAIL / PASS / UNKNOWN **262 / 110 / 5 -> 210 / 162 / 5**; FAIL because of
mandatory skills 181 -> 95, of a certification 8 -> 0, of minimum experience 151 -> 147 (a genuine gap: 7-15 years asked, 5.2 held), of education 27 -> 27.

*The 289 jobs that have a stored 1.0 report (same jobs, newest report of each version):*

| | 1.0 | 1.1 |
|---|---:|---:|
| Eligibility FAIL / PASS / UNKNOWN | 190 / 96 / 3 | 148 / 138 / 3 |
| FAIL because of mandatory skills | 121 | 52 |
| FAIL because of a certification | 8 | 0 |
| FAIL because of minimum experience | 114 | 110 |
| FAIL because of education | 11 | 11 |
| JDs with no requirement section detected | 215 | 2 |
| Mean score, ML/AI/data titles (n = 44) | 44.1 | 44.5 |
| Mean score, sales/finance/legal/HR titles (n = 111) | 30.4 | 15.6 |
| Sales/finance/legal/HR jobs scoring >= 55 | 11 | 1 |

What still fails is mostly genuine: senior roles (7-15 years), a Master's degree stated as mandatory, and real skill gaps (Spark, MLOps, ETL, microservices).

## 1. Job parsing (`engines/matching/jd_parser.py`)
Each skill found in a JD is classified once: **mandatory** (under requirements / "must", "required", "strong experience",
or the core skill in the job title), **preferred** ("nice to have", "plus", "familiarity", "exposure"), **example**
("such as AWS or Azure", "e.g.", "including ...", "(...)", "at least three of the following"), or **mentioned** (appears in the company blurb or a duty). Only mandatory and
preferred skills are scored; examples and mentions never become requirements; "About us / Benefits" text is ignored.
"A or B" in a requirement becomes an any-of group. Experience, education, certifications, salary, work mode, location,
seniority, work-authorisation and other explicit conditions are extracted with rules. Seniority from the title is
labelled `title`; seniority inferred from required years is labelled `experience_requirement`.

## 2. Skill normalisation (`config/skills.yaml`)
Synonyms map to one canonical skill ("Gen AI" -> Generative AI, "Py Torch" -> PyTorch, "SKLearn" -> Scikit-learn).
Add skills by editing the YAML - no code change. Relationships are explicit and documented (`related_groups`,
`related_directed`) and give **partial** credit only (e.g. TensorFlow<->PyTorch 0.5). Python and Java are not related.
Same category is never enough. Ambiguous names (R, Go, C, Excel, Swift) are recognised only as whole list items.

## 3. Eligibility (`engines/matching/eligibility_checker.py`)
Checks return PASS / FAIL / UNKNOWN / NOT_APPLICABLE: minimum experience, restrictive maximum experience, mandatory
skills (an exact match or a documented near-equivalent, credit >= 0.7, is required; loose relatives do not satisfy a mandatory skill), mandatory education,
mandatory certifications, location, employment type, work authorisation, salary, other explicit conditions.
Missing candidate data is UNKNOWN. Overall: any **hard** FAIL -> FAIL; else an UNKNOWN in a *blocking* check
(`eligibility.blocking_unknown_checks`) -> UNKNOWN; else PASS. Preference checks (salary, employment type) only warn.
An application may proceed automatically only when the result is PASS (`can_proceed`).

## 4. Score
`category points = ratio (0..1) x category weight`;
`score = sum(points) / sum(weights of EVALUATED categories) x 100` (`missing_data_policy: normalize`) or
`sum(points)` (`zero`). Categories that lack data are reported **UNKNOWN**, not 0 and not full marks, and lower
`data_coverage = evaluated weight %`. Below `min_data_coverage` (60 %) the verdict is "Insufficient data".

| Category | Weight | Ratio |
|---|---:|---|
| Technical skills | 30 | 80 % mandatory (mean credit per requirement: exact 1.0, documented related <1, missing 0) + 20 % preferred; if one side is empty the other counts 100 % (noted in the report) |
| Relevant experience | 20 | 50 % years fit `min(1, candidate years / required)` + 50 % relevance (key skills demonstrated in work history/projects; skill-specific years from dated roles, overlaps counted once, missing dates -> unknown) |
| Projects | 15 | mean relevance of the best 3 projects (JD-skill credit / min(#skills, 5), +0.1 if outcomes are stated) |
| Responsibilities | 15 | per JD duty: best lexical/skill overlap with the candidate's responsibilities/achievements (>=50 % overlap = full, <15 % = none). Not embeddings |
| Education | 5 | meets level 1.0; one level below 0.4; two below 0; optional degree unmet 0.75; none required 1.0 |
| Location & work mode | 5 | remote 1.0; same city 1.0; other city: open to relocation 0.6, not open 0, unknown -> UNKNOWN |
| Seniority | 10 | candidate level (from verified years, not titles) vs job level: same 1.0, +1 0.9, +2 0.7, -1 0.5, -2 0.2 |

After the weighted sum the **role-fit factor** (see "What changed in 1.1") is applied; the report stores both numbers.

Classification (inclusive lower bounds, configurable): >=90 Excellent, >=80 Strong, >=75 Good, >=60 Moderate, else Low.
`recommendation_threshold` (75) + eligibility PASS + enough data are all needed for "Recommended".
A score of 85 with a failed mandatory requirement is reported as **Not eligible**; the score is shown separately.

## 5. Reproducibility
Every report stores `scoring_version`, `config_hash` (hash of the configuration *content*), `input_hash`
(profile id+version, job, parser, config, preferences, skill dictionary) and the parser mode. Identical inputs return
the saved result instead of a duplicate; profile corrections, configuration changes or a new `scoring_version`
produce new records, and `rerun` always adds a history entry. The JD text is stored once per distinct job and the
parsed analysis is cached by (parser version, skill dictionary digest, AI mode).

## 6. Known limitations
* Responsibilities are matched lexically; one vague duty can earn little credit even for a strong candidate.
* A mandatory skill must appear in the verified profile in words the dictionary recognises, or through a documented near-equivalent
  (credit >= 0.7). A resume that says "ML Engineer" but never lists "Machine Learning" gets only partial credit, so the mandatory check FAILS - review
  the profile and add skills you genuinely have. `mandatory_skills_fail_if_missing_fraction_above` (0 = any genuine miss fails) is left strict on purpose; raise it only if you
  want a job with one missing skill out of many to pass.
* Role fit reads the function from job *titles* only; a title that names no function is not adjusted. A very short skill list (fewer than 3 identified skills) makes the
  technical-skills score rest on little evidence - the report says so under limitations.
* JD parsing is rule-based (Claude optional); unusual layouts can be mis-classified. Always read the evidence snippets.
* Weights are a starting point, not a calibrated model; they were not tuned to the evaluation dataset.
