# Phase 3 - Matching Evaluation Report (30 synthetic JDs)

> **Manual validation status: NOT DONE.** Everything below is produced by the automated evaluation. The *Reviewer comments* and *Human verdict* columns are intentionally empty - a person must review every JD and sign off. Automated agreement with my own written expectations is not human validation.

* Scoring version `1.0`, config hash `e8a04e354173d17f`; weights {'technical_skills': 30.0, 'experience': 20.0, 'projects': 15.0, 'responsibilities': 15.0, 'education': 5.0, 'location': 5.0, 'seniority': 10.0}
* Candidate: synthetic profile (`tests/fixtures/candidate.py`); preferences: see `tests/conftest.py`.
* Expectations (skills, eligibility, class, score band) were written from each JD *before* the engine was run.
* No reference scores exist, so 'score difference' = distance outside the expected band (0 = inside).

## Aggregate metrics

| Metric | Value |
|---|---|
| JDs | 30 |
| Mandatory-skill extraction precision / recall | 1.00 / 1.00 |
| Preferred-skill extraction precision / recall | 1.00 / 1.00 |
| Eligibility accuracy | 100% |
| Classification within expected set | 97% |
| Scores inside expected band | 97% |
| Mean distance outside band | 0.28 points |
| Explanations consistent with facts | 100% |
| JDs with zero automated differences | 29 / 30 |

## Per-JD results

| ID | Scenario | Exp. eligibility | Actual | Exp. class | Actual class | Score | Band | Dist. | Mandatory P/R | Preferred P/R | Auto result | Human verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| JD01 | Excellent technical match | PASS | PASS | Excellent / Strong / Good | Strong Match | 84.6 | 72-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD02 | Strong AI/ML match + cloud examples | PASS | PASS | Excellent / Strong / Good | Good Match | 76.6 | 72-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD03 | Strong Generative AI match (normalisation: Gen AI, LLM, RAG pipeline) | PASS | PASS | Excellent / Strong / Good | Moderate Match | 63.6 | 72-100 | 8.4 | 1.00/1.00 | 1.00/1.00 | DIFF | ☐ pending |
| JD04 | Strong Data Science match | PASS | PASS | Excellent / Strong / Good | Strong Match | 81.9 | 72-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD05 | Python-heavy role | PASS | PASS | Excellent / Strong / Good | Good Match | 75.9 | 70-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD06 | Machine learning role (compact skills list) | PASS | PASS | Excellent / Strong / Good | Strong Match | 82.1 | 70-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD07 | Deep learning role (related-not-equivalent skills) | FAIL | FAIL | Excellent / Strong / Good / Moderate / Low | Moderate Match | 73 | 55-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD08 | NLP role | FAIL | FAIL | Excellent / Strong / Good / Moderate / Low | Low Match | 49.9 | 35-85 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD09 | RAG and LLM role (alternate wording) | PASS | PASS | Excellent / Strong / Good | Good Match | 77.7 | 70-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD10 | Computer vision role | FAIL | FAIL | Excellent / Strong / Good / Moderate / Low | Low Match | 58.5 | 35-85 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD11 | Data engineering role | FAIL | FAIL | Excellent / Strong / Good / Moderate / Low | Low Match | 54.6 | 30-80 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD12 | Backend engineering role (any-of groups) | PASS | PASS | Excellent / Strong / Good | Strong Match | 82.9 | 65-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD13 | Cloud engineering role | FAIL | FAIL | Excellent / Strong / Good / Moderate / Low | Low Match | 58.6 | 30-80 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD14 | MLOps role | FAIL | FAIL | Excellent / Strong / Good / Moderate / Low | Moderate Match | 64.6 | 30-85 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD15 | Data analyst role (over-qualified candidate) | PASS | PASS | Excellent / Strong / Good / Moderate | Moderate Match | 70.2 | 60-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD16 | Entry-level role | PASS | PASS | Excellent / Strong / Good / Moderate | Strong Match | 85 | 60-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD17 | Senior-level role | PASS | PASS | Excellent / Strong / Good | Strong Match | 85.7 | 72-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD18 | Experience requirement mismatch | FAIL | FAIL | Excellent / Strong / Good / Moderate / Low | Moderate Match | 74.3 | 55-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD19 | Mandatory skill mismatch (different stack) | FAIL | FAIL | Moderate / Low | Low Match | 52.4 | 15-65 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD20 | Preferred skill mismatch only | PASS | PASS | Excellent / Strong / Good | Good Match | 79.1 | 70-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD21 | Education requirement mismatch | FAIL | FAIL | Excellent / Strong / Good / Moderate / Low | Moderate Match | 68.6 | 55-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD22 | Certification requirement | FAIL | FAIL | Excellent / Strong / Good / Moderate / Low | Strong Match | 86 | 55-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD23 | Remote position | PASS | PASS | Excellent / Strong / Good / Moderate | Strong Match | 87.9 | 55-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD24 | Hybrid position | PASS | PASS | Excellent / Strong / Good / Moderate | Strong Match | 87.9 | 55-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD25 | On-site position | PASS | PASS | Excellent / Strong / Good / Moderate | Strong Match | 86 | 55-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD26 | Location mismatch | FAIL | FAIL | Excellent / Strong / Good / Moderate / Low | Strong Match | 82.1 | 55-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD27 | Salary requirement mismatch | PASS | PASS | Excellent / Strong / Good / Moderate | Strong Match | 85.6 | 55-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD28 | Incomplete job description | PASS | PASS | Excellent / Strong / Good / Moderate / Low | Good Match | 78.6 | 30-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD29 | Ambiguous job description | PASS | PASS | Excellent / Strong / Good / Moderate / Low | Strong Match | 80.5 | 0-100 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |
| JD30 | Unrelated technical role | FAIL | FAIL | Moderate / Low | Low Match | 49.1 | 10-65 | 0 | 1.00/1.00 | 1.00/1.00 | PASS | ☐ pending |

## Details for the human reviewer

### JD01 - Excellent technical match
*Why this result is expected:* Every mandatory skill is in the profile, 7.4 yrs > 5, degree and city match.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Machine Learning', 'Retrieval-Augmented Generation', 'Python', 'PyTorch', 'LangChain', 'MongoDB'] (groups [['Machine Learning'], ['Retrieval-Augmented Generation'], ['Python'], ['PyTorch'], ['LangChain'], ['MongoDB']])
* **Extracted preferred:** ['Docker', 'Azure']; examples: []; mentioned: []
* **Experience:** min 5.0, max None; seniority senior (title); mode hybrid; location ['Pune, India']
* **Score:** 84.6 (Strong Match), coverage 100.0%, breakdown {'technical_skills': 30.0, 'experience': 19.07, 'projects': 5.25, 'responsibilities': 10.24, 'education': 5.0, 'location': 5.0, 'seniority': 10.0}
* **Eligibility:** PASS; min_experience=PASS, mandatory_skills=PASS, education=PASS, location=PASS, employment_type=PASS
* **Matched:** ['Machine Learning', 'Retrieval-Augmented Generation', 'Python', 'PyTorch', 'LangChain', 'MongoDB', 'Docker', 'Azure']; **missing mandatory:** []; **missing preferred:** []; **partial:** []
* **Recommendation:** Recommended: score 84.6/100 meets the 75 threshold and eligibility passed.
* **Explanation:** Senior Machine Learning Engineer - RAG Platform at Northwind AI: match score 84.6/100 (Strong Match), based on 100% data coverage. Eligibility: PASS. Strengths: Exactly matches 8 of 8 required/preferred skills: Machine Learning, Retrieval-Augmented Generation, Python, PyTorch, LangChain, MongoDB, Docker, Azure. Main concern: Projects: 35% fit (e.g. Resume Matcher).
* **Reviewer comments:** _(to be filled in by a human)_

### JD02 - Strong AI/ML match + cloud examples
*Why this result is expected:* Cloud platforms are only EXAMPLES (not requirements); Kubernetes/MLflow are preferred; remote role.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Machine Learning', 'Python', 'Scikit-learn', 'Docker'] (groups [['Machine Learning'], ['Python'], ['Scikit-learn'], ['Docker']])
* **Extracted preferred:** ['Kubernetes', 'MLflow']; examples: ['AWS', 'Azure', 'GCP']; mentioned: []
* **Experience:** min 4.0, max None; seniority mid (experience_requirement); mode remote; location []
* **Score:** 76.6 (Good Match), coverage 100.0%, breakdown {'technical_skills': 24.9, 'experience': 20.0, 'projects': 3.75, 'responsibilities': 8.98, 'education': 5.0, 'location': 5.0, 'seniority': 9.0}
* **Eligibility:** PASS; min_experience=PASS, mandatory_skills=PASS, employment_type=PASS
* **Matched:** ['Machine Learning', 'Python', 'Scikit-learn', 'Docker']; **missing mandatory:** []; **missing preferred:** ['MLflow']; **partial:** [('Docker', 'Kubernetes')]
* **Recommendation:** Recommended: score 76.6/100 meets the 75 threshold and eligibility passed.
* **Explanation:** AI Engineer (Remote) at Skyline Labs: match score 76.6/100 (Good Match), based on 100% data coverage. Eligibility: PASS. Strengths: Exactly matches 4 of 6 required/preferred skills: Machine Learning, Python, Scikit-learn, Docker. Main concern: Only a related (not equivalent) skill for Kubernetes: Docker - Container runtime vs orchestration - related, not the same skill.
* **Reviewer comments:** _(to be filled in by a human)_

### JD03 - Strong Generative AI match (normalisation: Gen AI, LLM, RAG pipeline)
*Why this result is expected:* 'About us' skills (Docker/K8s) must be ignored; Pinecone/FAISS are examples of the preferred vector DB.

* **Automated differences:** classification: expected one of ['Excellent Match', 'Strong Match', 'Good Match'], got Moderate Match; score 63.6 outside expected range (72, 100)
* **Explanation issues:** none
* **Extracted mandatory:** ['Generative AI', 'Python', 'Large Language Models', 'Retrieval-Augmented Generation', 'LangChain'] (groups [['Generative AI'], ['Python'], ['Large Language Models'], ['Retrieval-Augmented Generation'], ['LangChain']])
* **Extracted preferred:** ['Fine-tuning', 'Vector Database']; examples: ['Pinecone', 'FAISS']; mentioned: []
* **Experience:** min 3.0, max None; seniority mid (experience_requirement); mode None; location []
* **Score:** 63.6 (Moderate Match), coverage 95.0%, breakdown {'technical_skills': 24.0, 'experience': 18.0, 'projects': 3.75, 'responsibilities': 0.71, 'education': 5.0, 'location': None, 'seniority': 9.0}
* **Eligibility:** PASS; min_experience=PASS, mandatory_skills=PASS
* **Matched:** ['Generative AI', 'Python', 'Large Language Models', 'Retrieval-Augmented Generation', 'LangChain']; **missing mandatory:** []; **missing preferred:** ['Fine-tuning', 'Vector Database']; **partial:** []
* **Recommendation:** Not recommended: score 63.6/100 is below the 75 threshold.
* **Explanation:** Generative AI Developer at Pixel Foundry: match score 63.6/100 (Moderate Match), based on 95% data coverage. Eligibility: PASS. Strengths: Exactly matches 5 of 7 required/preferred skills: Generative AI, Python, Large Language Models, Retrieval-Augmented Generation, LangChain. Main concern: Missing preferred skills: Fine-tuning, Vector Database.
* **Reviewer comments:** _(to be filled in by a human)_

### JD04 - Strong Data Science match
*Why this result is expected:* Candidate has every mandatory and preferred skill; '3-5 years' is a target range, not a ceiling.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Python', 'SQL', 'Scikit-learn', 'Machine Learning'] (groups [['Python'], ['SQL'], ['Scikit-learn'], ['Machine Learning']])
* **Extracted preferred:** ['XGBoost', 'Tableau']; examples: []; mentioned: []
* **Experience:** min 3.0, max 5.0; seniority mid (experience_requirement); mode None; location ['Pune, India']
* **Score:** 81.9 (Strong Match), coverage 100.0%, breakdown {'technical_skills': 30.0, 'experience': 20.0, 'projects': 5.25, 'responsibilities': 7.62, 'education': 5.0, 'location': 5.0, 'seniority': 9.0}
* **Eligibility:** PASS; min_experience=PASS, mandatory_skills=PASS, location=PASS
* **Matched:** ['Python', 'SQL', 'Scikit-learn', 'Machine Learning', 'XGBoost', 'Tableau']; **missing mandatory:** []; **missing preferred:** []; **partial:** []
* **Recommendation:** Recommended: score 81.9/100 meets the 75 threshold and eligibility passed.
* **Explanation:** Data Scientist at Insight Metrics: match score 81.9/100 (Strong Match), based on 100% data coverage. Eligibility: PASS. Strengths: Exactly matches 6 of 6 required/preferred skills: Python, SQL, Scikit-learn, Machine Learning, XGBoost, Tableau. Main concern: Projects: 35% fit (e.g. Churn Dashboard).
* **Reviewer comments:** _(to be filled in by a human)_

### JD05 - Python-heavy role
*Why this result is expected:* All skills present (FastAPI from a project).

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Python', 'FastAPI', 'MongoDB', 'Git'] (groups [['Python'], ['FastAPI'], ['MongoDB'], ['Git']])
* **Extracted preferred:** ['Docker', 'PostgreSQL']; examples: []; mentioned: []
* **Experience:** min 3.0, max None; seniority mid (experience_requirement); mode hybrid; location ['Pune, India']
* **Score:** 75.9 (Good Match), coverage 100.0%, breakdown {'technical_skills': 30.0, 'experience': 18.75, 'projects': 6.75, 'responsibilities': 1.43, 'education': 5.0, 'location': 5.0, 'seniority': 9.0}
* **Eligibility:** PASS; min_experience=PASS, mandatory_skills=PASS, location=PASS
* **Matched:** ['Python', 'FastAPI', 'MongoDB', 'Git', 'Docker', 'PostgreSQL']; **missing mandatory:** []; **missing preferred:** []; **partial:** []
* **Recommendation:** Recommended: score 75.9/100 meets the 75 threshold and eligibility passed.
* **Explanation:** Python Backend Developer at CloudNest: match score 75.9/100 (Good Match), based on 100% data coverage. Eligibility: PASS. Strengths: Exactly matches 6 of 6 required/preferred skills: Python, FastAPI, MongoDB, Git, Docker, PostgreSQL. Main concern: Responsibilities: 10% fit (e.g. Built a RAG pipeline using LangChain and MongoDB for internal search. [experience: Acme An).
* **Reviewer comments:** _(to be filled in by a human)_

### JD06 - Machine learning role (compact skills list)
*Why this result is expected:* Inline 'Required skills:' / 'Preferred skills:' headings.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Machine Learning', 'Python', 'Scikit-learn', 'XGBoost', 'SQL'] (groups [['Machine Learning'], ['Python'], ['Scikit-learn'], ['XGBoost'], ['SQL']])
* **Extracted preferred:** ['Docker', 'AWS']; examples: []; mentioned: []
* **Experience:** min 2.0, max None; seniority junior (experience_requirement); mode remote; location []
* **Score:** 82.1 (Strong Match), coverage 85.0%, breakdown {'technical_skills': 30.0, 'experience': 19.0, 'projects': 3.75, 'responsibilities': None, 'education': 5.0, 'location': 5.0, 'seniority': 7.0}
* **Eligibility:** PASS; min_experience=PASS, mandatory_skills=PASS
* **Matched:** ['Machine Learning', 'Python', 'Scikit-learn', 'XGBoost', 'SQL', 'Docker', 'AWS']; **missing mandatory:** []; **missing preferred:** []; **partial:** []
* **Recommendation:** Recommended: score 82.1/100 meets the 75 threshold and eligibility passed.
* **Explanation:** Machine Learning Engineer at Orbit Data: match score 82.1/100 (Strong Match), based on 85% data coverage. Eligibility: PASS. Strengths: Exactly matches 7 of 7 required/preferred skills: Machine Learning, Python, Scikit-learn, XGBoost, SQL, Docker, AWS. Main concern: Projects: 25% fit (e.g. Resume Matcher).
* **Reviewer comments:** _(to be filled in by a human)_

### JD07 - Deep learning role (related-not-equivalent skills)
*Why this result is expected:* 'Deep Learning' is not listed in the profile; PyTorch/TensorFlow only imply it (partial credit). Policy: related skills never satisfy a mandatory requirement. 'PyTorch or TensorFlow' is an any-of group.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Deep Learning', 'Python', 'PyTorch', 'TensorFlow'] (groups [['Deep Learning'], ['Python'], ['PyTorch', 'TensorFlow']])
* **Extracted preferred:** ['Docker']; examples: []; mentioned: []
* **Experience:** min 4.0, max None; seniority mid (experience_requirement); mode None; location ['Pune']
* **Score:** 73.0 (Moderate Match), coverage 85.0%, breakdown {'technical_skills': 27.6, 'experience': 11.67, 'projects': 3.75, 'responsibilities': None, 'education': 5.0, 'location': 5.0, 'seniority': 9.0}
* **Eligibility:** FAIL; min_experience=PASS, mandatory_skills=FAIL, location=PASS
* **Matched:** ['Python', 'PyTorch', 'Docker']; **missing mandatory:** []; **missing preferred:** []; **partial:** [('PyTorch', 'Deep Learning')]
* **Recommendation:** Not eligible: mandatory_skills. The match score (73/100) is shown separately and does not override this.
* **Explanation:** Deep Learning Engineer at Neuron Works: match score 73/100 (Moderate Match), based on 85% data coverage. Eligibility: FAIL (mandatory_skills). Strengths: Exactly matches 3 of 4 required/preferred skills: Python, PyTorch, Docker. Main concern: Only a related (not equivalent) skill for Deep Learning: PyTorch - PyTorch is a deep learning framework; 'Deep Learning' is not listed explicitly.
* **Reviewer comments:** _(to be filled in by a human)_

### JD08 - NLP role
*Why this result is expected:* NLP, spaCy and Hugging Face are absent from the profile.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Natural Language Processing', 'Python', 'spaCy', 'Hugging Face'] (groups [['Natural Language Processing'], ['Python'], ['spaCy', 'Hugging Face']])
* **Extracted preferred:** ['Fine-tuning']; examples: []; mentioned: []
* **Experience:** min 3.0, max None; seniority mid (experience_requirement); mode None; location ['Pune']
* **Score:** 49.9 (Low Match), coverage 85.0%, breakdown {'technical_skills': 8.0, 'experience': 11.67, 'projects': 3.75, 'responsibilities': None, 'education': 5.0, 'location': 5.0, 'seniority': 9.0}
* **Eligibility:** FAIL; min_experience=PASS, mandatory_skills=FAIL, location=PASS
* **Matched:** ['Python']; **missing mandatory:** ['Natural Language Processing', 'spaCy or Hugging Face']; **missing preferred:** ['Fine-tuning']; **partial:** []
* **Recommendation:** Not eligible: mandatory_skills. The match score (49.9/100) is shown separately and does not override this.
* **Explanation:** NLP Engineer at Lexi Labs: match score 49.9/100 (Low Match), based on 85% data coverage. Eligibility: FAIL (mandatory_skills). Strengths: Exactly matches 1 of 4 required/preferred skills: Python. Main concern: Missing mandatory skills: Natural Language Processing, spaCy or Hugging Face.
* **Reviewer comments:** _(to be filled in by a human)_

### JD09 - RAG and LLM role (alternate wording)
*Why this result is expected:* 'Familiarity' is a weak cue so MongoDB is preferred; LangChain/LlamaIndex is an any-of group.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Large Language Models', 'Python', 'Retrieval-Augmented Generation', 'LangChain', 'LlamaIndex'] (groups [['Large Language Models'], ['Python'], ['Retrieval-Augmented Generation'], ['LangChain', 'LlamaIndex']])
* **Extracted preferred:** ['MongoDB', 'AWS', 'Azure']; examples: []; mentioned: []
* **Experience:** min 3.0, max None; seniority mid (experience_requirement); mode None; location []
* **Score:** 77.7 (Good Match), coverage 95.0%, breakdown {'technical_skills': 30.0, 'experience': 18.75, 'projects': 5.25, 'responsibilities': 5.82, 'education': 5.0, 'location': None, 'seniority': 9.0}
* **Eligibility:** PASS; min_experience=PASS, mandatory_skills=PASS
* **Matched:** ['Large Language Models', 'Python', 'Retrieval-Augmented Generation', 'LangChain', 'MongoDB', 'AWS', 'Azure']; **missing mandatory:** []; **missing preferred:** []; **partial:** []
* **Recommendation:** Recommended: score 77.7/100 meets the 75 threshold and eligibility passed.
* **Explanation:** LLM Application Engineer at Prompt Labs: match score 77.7/100 (Good Match), based on 95% data coverage. Eligibility: PASS. Strengths: Exactly matches 7 of 7 required/preferred skills: Large Language Models, Python, Retrieval-Augmented Generation, LangChain, MongoDB, AWS, Azure. Main concern: Responsibilities: 39% fit (e.g. Mentored junior engineers on model evaluation. [experience: Acme Analytics]).
* **Reviewer comments:** _(to be filled in by a human)_

### JD10 - Computer vision role
*Why this result is expected:* Computer Vision and OpenCV are not in the profile.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Computer Vision', 'Python', 'OpenCV', 'PyTorch'] (groups [['Computer Vision'], ['Python'], ['OpenCV'], ['PyTorch']])
* **Extracted preferred:** []; examples: []; mentioned: []
* **Experience:** min 3.0, max None; seniority mid (experience_requirement); mode None; location ['Pune']
* **Score:** 58.5 (Low Match), coverage 85.0%, breakdown {'technical_skills': 15.0, 'experience': 11.25, 'projects': 4.5, 'responsibilities': None, 'education': 5.0, 'location': 5.0, 'seniority': 9.0}
* **Eligibility:** FAIL; min_experience=PASS, mandatory_skills=FAIL, location=PASS
* **Matched:** ['Python', 'PyTorch']; **missing mandatory:** ['Computer Vision', 'OpenCV']; **missing preferred:** []; **partial:** []
* **Recommendation:** Not eligible: mandatory_skills. The match score (58.5/100) is shown separately and does not override this.
* **Explanation:** Computer Vision Engineer at Pixel Eye: match score 58.5/100 (Low Match), based on 85% data coverage. Eligibility: FAIL (mandatory_skills). Strengths: Exactly matches 2 of 4 required/preferred skills: Python, PyTorch. Main concern: Missing mandatory skills: Computer Vision, OpenCV.
* **Reviewer comments:** _(to be filled in by a human)_

### JD11 - Data engineering role
*Why this result is expected:* ETL, Spark and Airflow are missing.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['ETL', 'SQL', 'Python', 'Spark', 'Airflow', 'AWS'] (groups [['ETL'], ['SQL'], ['Python'], ['Spark'], ['Airflow'], ['AWS']])
* **Extracted preferred:** ['Kafka', 'Snowflake']; examples: []; mentioned: []
* **Experience:** min 4.0, max None; seniority mid (experience_requirement); mode hybrid; location ['Pune']
* **Score:** 54.6 (Low Match), coverage 85.0%, breakdown {'technical_skills': 12.0, 'experience': 11.67, 'projects': 3.75, 'responsibilities': None, 'education': 5.0, 'location': 5.0, 'seniority': 9.0}
* **Eligibility:** FAIL; min_experience=PASS, mandatory_skills=FAIL, location=PASS
* **Matched:** ['SQL', 'Python', 'AWS']; **missing mandatory:** ['ETL', 'Spark', 'Airflow']; **missing preferred:** ['Kafka', 'Snowflake']; **partial:** []
* **Recommendation:** Not eligible: mandatory_skills. The match score (54.6/100) is shown separately and does not override this.
* **Explanation:** Data Engineer at Pipeline Co: match score 54.6/100 (Low Match), based on 85% data coverage. Eligibility: FAIL (mandatory_skills). Strengths: Exactly matches 3 of 8 required/preferred skills: SQL, Python, AWS. Main concern: Missing mandatory skills: ETL, Spark, Airflow.
* **Reviewer comments:** _(to be filled in by a human)_

### JD12 - Backend engineering role (any-of groups)
*Why this result is expected:* Python is required on its own; only 'FastAPI, Flask or Django' and 'PostgreSQL or MongoDB' are any-of groups.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Python', 'FastAPI', 'Flask', 'Django', 'PostgreSQL', 'MongoDB', 'Docker', 'Git'] (groups [['Python'], ['Docker'], ['Git'], ['FastAPI', 'Flask', 'Django'], ['PostgreSQL', 'MongoDB']])
* **Extracted preferred:** ['Redis', 'Kubernetes']; examples: []; mentioned: []
* **Experience:** min 3.0, max None; seniority mid (experience_requirement); mode None; location ['Pune, India']
* **Score:** 82.9 (Strong Match), coverage 85.0%, breakdown {'technical_skills': 24.9, 'experience': 18.0, 'projects': 8.55, 'responsibilities': None, 'education': 5.0, 'location': 5.0, 'seniority': 9.0}
* **Eligibility:** PASS; min_experience=PASS, mandatory_skills=PASS, location=PASS
* **Matched:** ['Python', 'Docker', 'Git', 'FastAPI', 'PostgreSQL']; **missing mandatory:** []; **missing preferred:** ['Redis']; **partial:** [('Docker', 'Kubernetes')]
* **Recommendation:** Recommended: score 82.9/100 meets the 75 threshold and eligibility passed.
* **Explanation:** Backend Engineer at Stackly: match score 82.9/100 (Strong Match), based on 85% data coverage. Eligibility: PASS. Strengths: Exactly matches 5 of 7 required/preferred skills: Python, Docker, Git, FastAPI, PostgreSQL. Main concern: Only a related (not equivalent) skill for Kubernetes: Docker - Container runtime vs orchestration - related, not the same skill.
* **Reviewer comments:** _(to be filled in by a human)_

### JD13 - Cloud engineering role
*Why this result is expected:* Terraform, Kubernetes (only Docker, a related skill) and Linux are missing.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['AWS', 'Terraform', 'Kubernetes', 'Linux', 'Python'] (groups [['AWS'], ['Terraform'], ['Kubernetes'], ['Linux'], ['Python']])
* **Extracted preferred:** ['Jenkins', 'Azure']; examples: []; mentioned: []
* **Experience:** min 5.0, max None; seniority senior (experience_requirement); mode None; location ['Pune']
* **Score:** 58.6 (Low Match), coverage 85.0%, breakdown {'technical_skills': 14.04, 'experience': 12.0, 'projects': 3.75, 'responsibilities': None, 'education': 5.0, 'location': 5.0, 'seniority': 10.0}
* **Eligibility:** FAIL; min_experience=PASS, mandatory_skills=FAIL, location=PASS
* **Matched:** ['AWS', 'Python', 'Azure']; **missing mandatory:** ['Terraform', 'Linux']; **missing preferred:** ['Jenkins']; **partial:** [('Docker', 'Kubernetes')]
* **Recommendation:** Not eligible: mandatory_skills. The match score (58.6/100) is shown separately and does not override this.
* **Explanation:** Cloud Engineer at Nimbus: match score 58.6/100 (Low Match), based on 85% data coverage. Eligibility: FAIL (mandatory_skills). Strengths: Exactly matches 3 of 7 required/preferred skills: AWS, Python, Azure. Main concern: Missing mandatory skills: Terraform, Linux.
* **Reviewer comments:** _(to be filled in by a human)_

### JD14 - MLOps role
*Why this result is expected:* MLOps, CI/CD and MLflow are missing; Docker only partially covers Kubernetes/CI-CD.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['MLOps', 'Machine Learning', 'Docker', 'CI/CD', 'MLflow', 'Kubernetes', 'Python'] (groups [['MLOps'], ['Machine Learning'], ['Docker'], ['CI/CD'], ['MLflow'], ['Kubernetes'], ['Python']])
* **Extracted preferred:** ['Airflow', 'AWS']; examples: []; mentioned: []
* **Experience:** min 3.0, max None; seniority mid (experience_requirement); mode remote; location []
* **Score:** 64.6 (Moderate Match), coverage 85.0%, breakdown {'technical_skills': 15.0, 'experience': 17.14, 'projects': 3.75, 'responsibilities': None, 'education': 5.0, 'location': 5.0, 'seniority': 9.0}
* **Eligibility:** FAIL; min_experience=PASS, mandatory_skills=FAIL
* **Matched:** ['Machine Learning', 'Docker', 'Python', 'AWS']; **missing mandatory:** ['MLOps', 'MLflow']; **missing preferred:** ['Airflow']; **partial:** [('Docker', 'CI/CD'), ('Docker', 'Kubernetes')]
* **Recommendation:** Not eligible: mandatory_skills. The match score (64.6/100) is shown separately and does not override this.
* **Explanation:** MLOps Engineer at Pipeline Labs: match score 64.6/100 (Moderate Match), based on 85% data coverage. Eligibility: FAIL (mandatory_skills). Strengths: Exactly matches 4 of 9 required/preferred skills: Machine Learning, Docker, Python, AWS. Main concern: Missing mandatory skills: MLOps, MLflow.
* **Reviewer comments:** _(to be filled in by a human)_

### JD15 - Data analyst role (over-qualified candidate)
*Why this result is expected:* Candidate is more senior than the role (small seniority discount, no rejection).

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['SQL', 'Python', 'Tableau', 'Power BI'] (groups [['SQL'], ['Python'], ['Tableau', 'Power BI']])
* **Extracted preferred:** ['Excel']; examples: []; mentioned: []
* **Experience:** min 2.0, max None; seniority junior (experience_requirement); mode None; location ['Pune, India']
* **Score:** 70.2 (Moderate Match), coverage 100.0%, breakdown {'technical_skills': 24.0, 'experience': 20.0, 'projects': 6.3, 'responsibilities': 2.86, 'education': 5.0, 'location': 5.0, 'seniority': 7.0}
* **Eligibility:** PASS; min_experience=PASS, mandatory_skills=PASS, location=PASS
* **Matched:** ['SQL', 'Python', 'Tableau']; **missing mandatory:** []; **missing preferred:** ['Excel']; **partial:** []
* **Recommendation:** Not recommended: score 70.2/100 is below the 75 threshold.
* **Explanation:** Data Analyst at Retail Insights: match score 70.2/100 (Moderate Match), based on 100% data coverage. Eligibility: PASS. Strengths: Exactly matches 3 of 4 required/preferred skills: SQL, Python, Tableau. Main concern: Missing preferred skills: Excel.
* **Reviewer comments:** _(to be filled in by a human)_

### JD16 - Entry-level role
*Why this result is expected:* '0-1 years' is not a ceiling; over-qualification is not a rejection.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Python', 'SQL', 'Machine Learning', 'Scikit-learn'] (groups [['Python'], ['SQL'], ['Machine Learning'], ['Scikit-learn']])
* **Extracted preferred:** ['Tableau']; examples: []; mentioned: []
* **Experience:** min 0.0, max None; seniority junior (title); mode None; location ['Pune']
* **Score:** 85.0 (Strong Match), coverage 85.0%, breakdown {'technical_skills': 30.0, 'experience': 20.0, 'projects': 5.25, 'responsibilities': None, 'education': 5.0, 'location': 5.0, 'seniority': 7.0}
* **Eligibility:** PASS; min_experience=PASS, mandatory_skills=PASS, location=PASS, employment_type=PASS
* **Matched:** ['Python', 'SQL', 'Machine Learning', 'Scikit-learn', 'Tableau']; **missing mandatory:** []; **missing preferred:** []; **partial:** []
* **Recommendation:** Recommended: score 85/100 meets the 75 threshold and eligibility passed.
* **Explanation:** Junior Data Scientist (Entry Level) at Fresh Start Analytics: match score 85/100 (Strong Match), based on 85% data coverage. Eligibility: PASS. Strengths: Exactly matches 5 of 5 required/preferred skills: Python, SQL, Machine Learning, Scikit-learn, Tableau. Main concern: Projects: 35% fit (e.g. Churn Dashboard).
* **Reviewer comments:** _(to be filled in by a human)_

### JD17 - Senior-level role
*Why this result is expected:* 7.4 yrs >= 6; senior vs senior; on-site in the candidate's city.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Machine Learning', 'Python', 'SQL', 'Scikit-learn'] (groups [['Machine Learning'], ['Python'], ['SQL'], ['Scikit-learn']])
* **Extracted preferred:** ['PyTorch']; examples: []; mentioned: []
* **Experience:** min 6.0, max None; seniority senior (title); mode onsite; location ['Pune']
* **Score:** 85.7 (Strong Match), coverage 85.0%, breakdown {'technical_skills': 30.0, 'experience': 19.08, 'projects': 3.75, 'responsibilities': None, 'education': 5.0, 'location': 5.0, 'seniority': 10.0}
* **Eligibility:** PASS; min_experience=PASS, mandatory_skills=PASS, location=PASS
* **Matched:** ['Machine Learning', 'Python', 'SQL', 'Scikit-learn', 'PyTorch']; **missing mandatory:** []; **missing preferred:** []; **partial:** []
* **Recommendation:** Recommended: score 85.7/100 meets the 75 threshold and eligibility passed.
* **Explanation:** Senior Data Scientist at Apex Analytics: match score 85.7/100 (Strong Match), based on 85% data coverage. Eligibility: PASS. Strengths: Exactly matches 5 of 5 required/preferred skills: Machine Learning, Python, SQL, Scikit-learn, PyTorch. Main concern: Projects: 25% fit (e.g. Resume Matcher).
* **Reviewer comments:** _(to be filled in by a human)_

### JD18 - Experience requirement mismatch
*Why this result is expected:* 7.4 years < 12 required. The numeric score can still be high; eligibility must still FAIL.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Machine Learning', 'Python', 'PyTorch', 'Scikit-learn'] (groups [['Machine Learning'], ['Python'], ['PyTorch'], ['Scikit-learn']])
* **Extracted preferred:** []; examples: []; mentioned: []
* **Experience:** min 12.0, max None; seniority lead (title); mode None; location ['Pune']
* **Score:** 74.3 (Moderate Match), coverage 85.0%, breakdown {'technical_skills': 30.0, 'experience': 13.67, 'projects': 4.5, 'responsibilities': None, 'education': 5.0, 'location': 5.0, 'seniority': 5.0}
* **Eligibility:** FAIL; min_experience=FAIL, mandatory_skills=PASS, location=PASS
* **Matched:** ['Machine Learning', 'Python', 'PyTorch', 'Scikit-learn']; **missing mandatory:** []; **missing preferred:** []; **partial:** []
* **Recommendation:** Not eligible: min_experience. The match score (74.3/100) is shown separately and does not override this.
* **Explanation:** Staff Machine Learning Engineer at Titan AI: match score 74.3/100 (Moderate Match), based on 85% data coverage. Eligibility: FAIL (min_experience). Strengths: Exactly matches 4 of 4 required/preferred skills: Machine Learning, Python, PyTorch, Scikit-learn. Main concern: Seniority: Job level: lead (from title); candidate level: senior (from 7.4 years of verified experience, not from job titles).
* **Reviewer comments:** _(to be filled in by a human)_

### JD19 - Mandatory skill mismatch (different stack)
*Why this result is expected:* Java, Spring Boot, Kafka and Microservices are missing; Python is only preferred here.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Java', 'Spring Boot', 'Kafka', 'Microservices', 'SQL'] (groups [['Java'], ['Spring Boot'], ['Kafka'], ['Microservices'], ['SQL']])
* **Extracted preferred:** ['Python', 'Docker']; examples: []; mentioned: []
* **Experience:** min 4.0, max None; seniority mid (experience_requirement); mode None; location ['Pune']
* **Score:** 52.4 (Low Match), coverage 85.0%, breakdown {'technical_skills': 10.8, 'experience': 11.0, 'projects': 3.75, 'responsibilities': None, 'education': 5.0, 'location': 5.0, 'seniority': 9.0}
* **Eligibility:** FAIL; min_experience=PASS, mandatory_skills=FAIL, location=PASS
* **Matched:** ['SQL', 'Python', 'Docker']; **missing mandatory:** ['Java', 'Spring Boot', 'Kafka', 'Microservices']; **missing preferred:** []; **partial:** []
* **Recommendation:** Not eligible: mandatory_skills. The match score (52.4/100) is shown separately and does not override this.
* **Explanation:** Java Backend Engineer at Enterprise Corp: match score 52.4/100 (Low Match), based on 85% data coverage. Eligibility: FAIL (mandatory_skills). Strengths: Exactly matches 3 of 7 required/preferred skills: SQL, Python, Docker. Main concern: Missing mandatory skills: Java, Spring Boot, Kafka, Microservices.
* **Reviewer comments:** _(to be filled in by a human)_

### JD20 - Preferred skill mismatch only
*Why this result is expected:* Missing preferred skills lower the score but must never cause rejection.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Machine Learning', 'Python', 'Scikit-learn'] (groups [['Machine Learning'], ['Python'], ['Scikit-learn']])
* **Extracted preferred:** ['Kubernetes', 'Spark', 'Kafka', 'MLflow']; examples: []; mentioned: []
* **Experience:** min 3.0, max None; seniority mid (experience_requirement); mode None; location ['Pune']
* **Score:** 79.1 (Good Match), coverage 85.0%, breakdown {'technical_skills': 24.45, 'experience': 20.0, 'projects': 3.75, 'responsibilities': None, 'education': 5.0, 'location': 5.0, 'seniority': 9.0}
* **Eligibility:** PASS; min_experience=PASS, mandatory_skills=PASS, location=PASS
* **Matched:** ['Machine Learning', 'Python', 'Scikit-learn']; **missing mandatory:** []; **missing preferred:** ['Spark', 'Kafka', 'MLflow']; **partial:** [('Docker', 'Kubernetes')]
* **Recommendation:** Recommended: score 79.1/100 meets the 75 threshold and eligibility passed.
* **Explanation:** Machine Learning Engineer at Quantum Insights: match score 79.1/100 (Good Match), based on 85% data coverage. Eligibility: PASS. Strengths: Exactly matches 3 of 7 required/preferred skills: Machine Learning, Python, Scikit-learn. Main concern: Only a related (not equivalent) skill for Kubernetes: Docker - Container runtime vs orchestration - related, not the same skill.
* **Reviewer comments:** _(to be filled in by a human)_

### JD21 - Education requirement mismatch
*Why this result is expected:* A PhD is mandatory; the candidate holds a bachelor's degree.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Machine Learning', 'Python', 'PyTorch'] (groups [['Machine Learning'], ['Python'], ['PyTorch']])
* **Extracted preferred:** []; examples: []; mentioned: []
* **Experience:** min 3.0, max None; seniority mid (experience_requirement); mode None; location ['Pune']
* **Score:** 68.6 (Moderate Match), coverage 100.0%, breakdown {'technical_skills': 30.0, 'experience': 16.67, 'projects': 5.75, 'responsibilities': 2.14, 'education': 0.0, 'location': 5.0, 'seniority': 9.0}
* **Eligibility:** FAIL; min_experience=PASS, mandatory_skills=PASS, education=FAIL, location=PASS
* **Matched:** ['Machine Learning', 'Python', 'PyTorch']; **missing mandatory:** []; **missing preferred:** []; **partial:** []
* **Recommendation:** Not eligible: education. The match score (68.6/100) is shown separately and does not override this.
* **Explanation:** Research Scientist - Machine Learning at Deep Think Labs: match score 68.6/100 (Moderate Match), based on 100% data coverage. Eligibility: FAIL (education). Strengths: Exactly matches 3 of 3 required/preferred skills: Machine Learning, Python, PyTorch. Main concern: Education: Below the required phd level.
* **Reviewer comments:** _(to be filled in by a human)_

### JD22 - Certification requirement
*Why this result is expected:* The candidate holds an Azure certification, not the required AWS one.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['AWS', 'Machine Learning', 'Python'] (groups [['AWS'], ['Machine Learning'], ['Python']])
* **Extracted preferred:** []; examples: []; mentioned: []
* **Experience:** min 3.0, max None; seniority mid (experience_requirement); mode None; location ['Pune']
* **Score:** 86.0 (Strong Match), coverage 85.0%, breakdown {'technical_skills': 30.0, 'experience': 18.33, 'projects': 5.75, 'responsibilities': None, 'education': 5.0, 'location': 5.0, 'seniority': 9.0}
* **Eligibility:** FAIL; min_experience=PASS, mandatory_skills=PASS, certifications=FAIL, location=PASS
* **Matched:** ['AWS', 'Machine Learning', 'Python']; **missing mandatory:** []; **missing preferred:** []; **partial:** []
* **Recommendation:** Not eligible: certifications. The match score (86/100) is shown separately and does not override this.
* **Explanation:** Cloud AI Engineer at Cert Corp: match score 86/100 (Strong Match), based on 85% data coverage. Eligibility: FAIL (certifications). Strengths: Exactly matches 3 of 3 required/preferred skills: AWS, Machine Learning, Python. Main concern: Projects: 38% fit (e.g. Resume Matcher).
* **Reviewer comments:** _(to be filled in by a human)_

### JD23 - Remote position
*Why this result is expected:* Remote: location is not a restriction. Short JD: some categories are UNKNOWN.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Machine Learning', 'Python'] (groups [['Machine Learning'], ['Python']])
* **Extracted preferred:** ['Docker']; examples: []; mentioned: []
* **Experience:** min 3.0, max None; seniority mid (experience_requirement); mode remote; location []
* **Score:** 87.9 (Strong Match), coverage 85.0%, breakdown {'technical_skills': 30.0, 'experience': 20.0, 'projects': 5.75, 'responsibilities': None, 'education': 5.0, 'location': 5.0, 'seniority': 9.0}
* **Eligibility:** PASS; min_experience=PASS, mandatory_skills=PASS, employment_type=PASS
* **Matched:** ['Machine Learning', 'Python', 'Docker']; **missing mandatory:** []; **missing preferred:** []; **partial:** []
* **Recommendation:** Recommended: score 87.9/100 meets the 75 threshold and eligibility passed.
* **Explanation:** Remote Machine Learning Engineer at Anywhere Inc: match score 87.9/100 (Strong Match), based on 85% data coverage. Eligibility: PASS. Strengths: Exactly matches 3 of 3 required/preferred skills: Machine Learning, Python, Docker. Main concern: Projects: 38% fit (e.g. Resume Matcher).
* **Reviewer comments:** _(to be filled in by a human)_

### JD24 - Hybrid position
*Why this result is expected:* Hybrid in the candidate's own city.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Machine Learning', 'Python', 'SQL'] (groups [['Machine Learning'], ['Python'], ['SQL']])
* **Extracted preferred:** []; examples: []; mentioned: []
* **Experience:** min 3.0, max None; seniority mid (experience_requirement); mode hybrid; location ['Pune, India']
* **Score:** 87.9 (Strong Match), coverage 85.0%, breakdown {'technical_skills': 30.0, 'experience': 20.0, 'projects': 5.75, 'responsibilities': None, 'education': 5.0, 'location': 5.0, 'seniority': 9.0}
* **Eligibility:** PASS; min_experience=PASS, mandatory_skills=PASS, location=PASS
* **Matched:** ['Machine Learning', 'Python', 'SQL']; **missing mandatory:** []; **missing preferred:** []; **partial:** []
* **Recommendation:** Recommended: score 87.9/100 meets the 75 threshold and eligibility passed.
* **Explanation:** ML Engineer at Hybrid Works: match score 87.9/100 (Strong Match), based on 85% data coverage. Eligibility: PASS. Strengths: Exactly matches 3 of 3 required/preferred skills: Machine Learning, Python, SQL. Main concern: Projects: 38% fit (e.g. Resume Matcher).
* **Reviewer comments:** _(to be filled in by a human)_

### JD25 - On-site position
*Why this result is expected:* On-site in the candidate's own city.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Machine Learning', 'Python', 'PyTorch'] (groups [['Machine Learning'], ['Python'], ['PyTorch']])
* **Extracted preferred:** []; examples: []; mentioned: []
* **Experience:** min 4.0, max None; seniority mid (experience_requirement); mode onsite; location ['Pune, India']
* **Score:** 86.0 (Strong Match), coverage 85.0%, breakdown {'technical_skills': 30.0, 'experience': 18.33, 'projects': 5.75, 'responsibilities': None, 'education': 5.0, 'location': 5.0, 'seniority': 9.0}
* **Eligibility:** PASS; min_experience=PASS, mandatory_skills=PASS, location=PASS
* **Matched:** ['Machine Learning', 'Python', 'PyTorch']; **missing mandatory:** []; **missing preferred:** []; **partial:** []
* **Recommendation:** Recommended: score 86/100 meets the 75 threshold and eligibility passed.
* **Explanation:** Machine Learning Engineer (On-site) at Onsite Ltd: match score 86/100 (Strong Match), based on 85% data coverage. Eligibility: PASS. Strengths: Exactly matches 3 of 3 required/preferred skills: Machine Learning, Python, PyTorch. Main concern: Projects: 38% fit (e.g. Resume Matcher).
* **Reviewer comments:** _(to be filled in by a human)_

### JD26 - Location mismatch
*Why this result is expected:* On-site in another city and the candidate is not open to relocation.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Machine Learning', 'Python', 'Scikit-learn'] (groups [['Machine Learning'], ['Python'], ['Scikit-learn']])
* **Extracted preferred:** []; examples: []; mentioned: []
* **Experience:** min 3.0, max None; seniority mid (experience_requirement); mode onsite; location ['Bengaluru, India']
* **Score:** 82.1 (Strong Match), coverage 85.0%, breakdown {'technical_skills': 30.0, 'experience': 20.0, 'projects': 5.75, 'responsibilities': None, 'education': 5.0, 'location': 0.0, 'seniority': 9.0}
* **Eligibility:** FAIL; min_experience=PASS, mandatory_skills=PASS, location=FAIL
* **Matched:** ['Machine Learning', 'Python', 'Scikit-learn']; **missing mandatory:** []; **missing preferred:** []; **partial:** []
* **Recommendation:** Not eligible: location. The match score (82.1/100) is shown separately and does not override this.
* **Explanation:** Machine Learning Engineer at Far Away Co: match score 82.1/100 (Strong Match), based on 85% data coverage. Eligibility: FAIL (location). Strengths: Exactly matches 3 of 3 required/preferred skills: Machine Learning, Python, Scikit-learn. Main concern: Projects: 38% fit (e.g. Resume Matcher).
* **Reviewer comments:** _(to be filled in by a human)_

### JD27 - Salary requirement mismatch
*Why this result is expected:* Below the candidate's expected minimum salary: a preference mismatch (warning), not a hard rejection.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Machine Learning', 'Python', 'Scikit-learn'] (groups [['Machine Learning'], ['Python'], ['Scikit-learn']])
* **Extracted preferred:** []; examples: []; mentioned: []
* **Experience:** min 2.0, max None; seniority junior (experience_requirement); mode None; location ['Pune']
* **Score:** 85.6 (Strong Match), coverage 85.0%, breakdown {'technical_skills': 30.0, 'experience': 20.0, 'projects': 5.75, 'responsibilities': None, 'education': 5.0, 'location': 5.0, 'seniority': 7.0}
* **Eligibility:** PASS; min_experience=PASS, mandatory_skills=PASS, location=PASS, salary=FAIL
* **Matched:** ['Machine Learning', 'Python', 'Scikit-learn']; **missing mandatory:** []; **missing preferred:** []; **partial:** []
* **Recommendation:** Recommended: score 85.6/100 meets the 75 threshold and eligibility passed.
* **Explanation:** Machine Learning Engineer at Budget Co: match score 85.6/100 (Strong Match), based on 85% data coverage. Eligibility: PASS. Strengths: Exactly matches 3 of 3 required/preferred skills: Machine Learning, Python, Scikit-learn. Main concern: Projects: 38% fit (e.g. Resume Matcher).
* **Reviewer comments:** _(to be filled in by a human)_

### JD28 - Incomplete job description
*Why this result is expected:* One sentence: only the title skill is mandatory; coverage must be visibly reduced and a warning given.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['Machine Learning'] (groups [['Machine Learning']])
* **Extracted preferred:** []; examples: []; mentioned: ['Python']
* **Experience:** min None, max None; seniority None (None); mode None; location []
* **Score:** 78.6 (Good Match), coverage 70.0%, breakdown {'technical_skills': 30.0, 'experience': 20.0, 'projects': 0.0, 'responsibilities': None, 'education': 5.0, 'location': None, 'seniority': None}
* **Eligibility:** PASS; mandatory_skills=PASS
* **Matched:** ['Machine Learning']; **missing mandatory:** []; **missing preferred:** []; **partial:** []
* **Recommendation:** Recommended: score 78.6/100 meets the 75 threshold and eligibility passed.
* **Explanation:** ML Engineer: match score 78.6/100 (Good Match), based on 70% data coverage. Eligibility: PASS. Strengths: Exactly matches 1 of 1 required/preferred skills: Machine Learning. Main concern: Projects: 0% fit.
* **Reviewer comments:** _(to be filled in by a human)_

### JD29 - Ambiguous job description
*Why this result is expected:* Nothing is explicitly required: skills are only 'mentioned' or examples, so none may be mandatory.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** [] (groups [])
* **Extracted preferred:** []; examples: ['AWS', 'Azure']; mentioned: ['Python', 'Java']
* **Experience:** min 2.0, max 10.0; seniority junior (experience_requirement); mode None; location []
* **Score:** 80.5 (Strong Match), coverage 50.0%, breakdown {'technical_skills': None, 'experience': 20.0, 'projects': 8.25, 'responsibilities': None, 'education': 5.0, 'location': None, 'seniority': 7.0}
* **Eligibility:** PASS; min_experience=PASS
* **Matched:** []; **missing mandatory:** []; **missing preferred:** []; **partial:** []
* **Recommendation:** Only 50% of the scoring weight could be evaluated (minimum 60%). Add missing profile/job details.
* **Explanation:** This job: match score 80.5/100 (Strong Match), based on 50% data coverage. Eligibility: PASS. Strengths: Relevant experience: Total experience 7.4 years vs 2+ required (profile (stated or calculated, as reviewed)).
* **Reviewer comments:** _(to be filled in by a human)_

### JD30 - Unrelated technical role
*Why this result is expected:* C/C++ firmware work; the profile is ML/Python.

* **Automated differences:** none
* **Explanation issues:** none
* **Extracted mandatory:** ['C++', 'C'] (groups [['C++'], ['C']])
* **Extracted preferred:** ['Python']; examples: []; mentioned: []
* **Experience:** min 5.0, max None; seniority senior (experience_requirement); mode None; location ['Pune']
* **Score:** 49.1 (Low Match), coverage 85.0%, breakdown {'technical_skills': 6.0, 'experience': 10.0, 'projects': 5.75, 'responsibilities': None, 'education': 5.0, 'location': 5.0, 'seniority': 10.0}
* **Eligibility:** FAIL; min_experience=PASS, mandatory_skills=FAIL, location=PASS
* **Matched:** ['Python']; **missing mandatory:** ['C++', 'C']; **missing preferred:** []; **partial:** []
* **Recommendation:** Not eligible: mandatory_skills. The match score (49.1/100) is shown separately and does not override this.
* **Explanation:** Embedded Firmware Engineer at Chipset Corp: match score 49.1/100 (Low Match), based on 85% data coverage. Eligibility: FAIL (mandatory_skills). Strengths: Exactly matches 1 of 3 required/preferred skills: Python. Main concern: Missing mandatory skills: C++, C.
* **Reviewer comments:** _(to be filled in by a human)_

## Human sign-off

| Reviewer | Date | JDs reviewed | Verdict (approve / changes needed) | Comments |
|---|---|---|---|---|
| _pending_ | | 0 / 30 | | |
