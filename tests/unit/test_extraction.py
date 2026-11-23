"""Unit tests for text extraction, cleaning and rule-based profile extraction.

Fixtures are synthetic documents (see tests/fixtures/sample_data.py) - they show
the parser works on that layout, not on any real resume.
"""
import pymupdf
import pytest
from docx import Document

from engines.resume.profile_extractor import (
    calculate_total_experience,
    extract_certifications,
    extract_contact,
    extract_education,
    extract_experience,
    extract_profile,
    extract_projects,
    extract_skills,
    find_overlaps,
    parse_date,
    split_sections,
)
from engines.resume.resume_parser import (
    ResumeExtractionError,
    clean_text,
    extract_docx_text,
    extract_pdf_text,
    parse_resume_file,
)


# ---- file extraction --------------------------------------------------------
def test_pdf_text_extraction(sample_pdf):
    text = extract_pdf_text(sample_pdf)
    assert "Jane Sample" in text and "jane.sample@example.com" in text


def test_docx_text_extraction(sample_docx):
    text = extract_docx_text(sample_docx)
    assert "Acme Analytics" in text
    assert "• Built a RAG pipeline" in text  # list style turned into a bullet


def test_pdf_and_docx_give_same_profile(sample_pdf, sample_docx):
    a = parse_resume_file(sample_pdf, "pdf", "r").profile
    b = parse_resume_file(sample_docx, "docx", "r").profile
    assert a.contact == b.contact
    assert a.technical_skills == b.technical_skills
    assert a.work_experience == b.work_experience
    assert a.education == b.education


def test_corrupted_pdf(tmp_path):
    p = tmp_path / "bad.pdf"
    p.write_bytes(b"%PDF-1.4\nthis is not really a pdf")
    with pytest.raises(ResumeExtractionError) as e:
        extract_pdf_text(p)
    assert e.value.code == "corrupted"


def test_corrupted_docx(tmp_path):
    p = tmp_path / "bad.docx"
    p.write_bytes(b"PK\x03\x04garbage")
    with pytest.raises(ResumeExtractionError) as e:
        extract_docx_text(p)
    assert e.value.code == "corrupted"


def test_encrypted_pdf(tmp_path):
    p = tmp_path / "enc.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), "secret resume text " * 20)
    doc.save(str(p), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    with pytest.raises(ResumeExtractionError) as e:
        extract_pdf_text(p)
    assert e.value.code == "encrypted"


def test_scanned_pdf_is_not_reported_as_parsed(tmp_path):
    p = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(50, 50, 300, 300), fill=(0.5, 0.5, 0.5))  # image-like, no text
    doc.save(str(p))
    with pytest.raises(ResumeExtractionError) as e:
        parse_resume_file(p, "pdf", "r")
    assert e.value.code == "needs_ocr"


def test_ocr_extension_point_is_used(tmp_path):
    p = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    doc.new_page().draw_rect(pymupdf.Rect(50, 50, 300, 300), fill=(0.5, 0.5, 0.5))
    doc.save(str(p))
    fake_ocr = lambda path: "Jane Sample\njane@example.com\n" + "filler text " * 20  # noqa: E731
    out = parse_resume_file(p, "pdf", "r", ocr=fake_ocr)
    assert out.profile.contact.email == "jane@example.com"


def test_empty_docx(tmp_path):
    p = tmp_path / "empty.docx"
    Document().save(str(p))
    with pytest.raises(ResumeExtractionError) as e:
        parse_resume_file(p, "docx", "r")
    assert e.value.code == "empty_document"


# ---- cleaning ---------------------------------------------------------------
def test_clean_text_normalises_bullets_and_whitespace():
    raw = "Name Here\r\n\r\n\r\n\r\n●   Did   a thing\n Another\n· third\nA · B"
    out = clean_text(raw)
    assert "\n\n\n" not in out
    assert "• Did a thing" in out and "• Another" in out and "• third" in out
    assert "A · B" in out  # mid-line separators are untouched


# ---- sections ---------------------------------------------------------------
def test_section_detection_with_varied_headings():
    text = "Name\n\nAbout Me\nhello\nKey Skills:\nPython\nEmployment History\nx\nAcademic Background\ny\nCourses\nz"
    s = split_sections(text)
    assert s["summary"] == ["hello"]
    assert s["skills"] == ["Python"]
    assert "experience" in s and "education" in s and "certifications" in s


def test_inline_skills_heading():
    s = split_sections("Jane\nTechnical Skills: Python, SQL\nProjects\nA")
    assert s["skills"] == ["Python, SQL"]


def test_projects_technologies_line_is_not_a_heading():
    s = split_sections("Projects\nX\nTechnologies: Python")
    assert "skills" not in s


# ---- contact ----------------------------------------------------------------
def test_contact_extraction_only_what_is_present():
    c, _ = extract_contact(["Jane Sample", "jane@example.com"], "Jane Sample\njane@example.com")
    assert c.full_name == "Jane Sample" and c.email == "jane@example.com"
    assert c.phone is None and c.location is None and c.linkedin_url is None


def test_contact_urls_normalised():
    c, _ = extract_contact(["A B", "linkedin.com/in/ab | github.com/ab | ab.dev"], "")
    assert c.linkedin_url == "https://linkedin.com/in/ab"
    assert c.github_url == "https://github.com/ab"
    assert c.portfolio_url == "https://ab.dev"


def test_name_not_taken_from_title_line():
    c, _ = extract_contact(["Senior Data Engineer", "x@example.com"], "")
    assert c.full_name is None


# ---- skills -----------------------------------------------------------------
def test_skills_categorised_and_deduplicated():
    skills, flags, _ = extract_skills(
        ["Languages: Python, SQL, python", "Cloud: Azure", "Misc: Public Speaking"], ""
    )
    assert skills.programming_languages == ["Python", "SQL"]
    assert skills.cloud_platforms == ["Azure"]
    assert "Public Speaking" in skills.other_skills


def test_skills_not_invented_when_absent():
    skills, flags, _ = extract_skills([], "I enjoy cooking and hiking.")
    assert skills.all_skills() == []


def test_skills_from_text_when_no_section_are_flagged():
    skills, flags, prov = extract_skills([], "Built apps with Docker and PyTorch")
    assert {s for _, s in skills.all_skills()} == {"Docker", "PyTorch"}
    assert flags and prov["technical_skills"].kind == "inferred"


# ---- dates / experience -----------------------------------------------------
@pytest.mark.parametrize("tok,exp", [
    ("Jan 2022", "2022-01"), ("September 2020", "2020-09"), ("03/2019", "2019-03"),
    ("2018-07", "2018-07"), ("2017", "2017"), ("Foo 2020", None), ("13/2020", None),
])
def test_parse_date(tok, exp):
    assert parse_date(tok) == exp


def test_experience_entries():
    lines = [
        "Lead Engineer | Acme Corp | Remote",
        "Mar 2020 - Present",
        "• Led a team of 5 engineers.",
        "• Cut costs by 20% using Docker.",
        "Developer, Beta Inc (Contract)",
        "2017 - 2019",
        "• Built APIs.",
    ]
    jobs, flags, _ = extract_experience(lines)
    assert len(jobs) == 2
    assert jobs[0].job_title == "Lead Engineer" and jobs[0].company_name == "Acme Corp"
    assert jobs[0].is_current and jobs[0].end_date is None
    assert jobs[0].achievements == ["Cut costs by 20% using Docker."]
    assert jobs[0].technologies_used == ["Docker"]
    assert jobs[1].employment_type == "Contract"
    assert jobs[1].start_date == "2017" and any("year" in f.reason for f in flags)


def test_experience_wrapped_bullets_are_merged():
    lines = ["Engineer | Acme", "Jan 2020 - Dec 2020", "• Built a very long pipeline that", "processed data nightly."]
    jobs, _, _ = extract_experience(lines)
    assert jobs[0].responsibilities == ["Built a very long pipeline that processed data nightly."]


def test_missing_company_is_flagged_not_invented():
    jobs, flags, _ = extract_experience(["Jan 2020 - Dec 2020", "• Did things."])
    assert jobs[0].company_name is None and jobs[0].job_title is None
    assert {f.field for f in flags} >= {"work_experience[0].company_name", "work_experience[0].job_title"}


def test_total_experience_and_overlap():
    jobs, _, _ = extract_experience([
        "Engineer | A", "Jan 2020 - Dec 2021", "• x.",
        "Engineer | B", "Jun 2021 - Dec 2022", "• y.",
    ])
    assert find_overlaps(jobs) == [(0, 1)]
    assert calculate_total_experience(jobs) == 3.0  # union of 2020-01..2022-12, overlap counted once


def test_total_experience_unknown_without_dates():
    jobs, _, _ = extract_experience(["Engineer | A", "Jan 2020 - Dec 2021", "Dev | B", "garbage"])
    assert calculate_total_experience(jobs + [type(jobs[0])(company_name="X")]) is None


# ---- projects / education / certifications ----------------------------------
def test_projects_extraction():
    projects, _ = extract_projects([
        "Chat Bot", "Role: Lead", "Tech Stack: Python, LangChain",
        "• Reached 95% accuracy on 2,000 queries.", "• Wrote the backend.", "",
        "Second Project - A small tool", "• Used Redis for caching.",
    ])
    assert [p.project_name for p in projects] == ["Chat Bot", "Second Project"]
    assert projects[0].role == "Lead" and projects[0].technologies == ["Python", "LangChain"]
    assert projects[0].achievements == ["Reached 95% accuracy on 2,000 queries."]
    assert projects[1].technologies == ["Redis"] and projects[1].description == "A small tool"


def test_education_extraction():
    edu, _ = extract_education([
        "M.Tech in Data Science", "Some University, 2019 - 2021", "Percentage: 85%", "",
        "B.E. Computer Engineering - City College (2015-2019)",
    ])
    assert len(edu) == 2
    assert edu[0].degree.startswith("M") and edu[0].specialization == "Data Science"
    assert edu[0].institution == "Some University" and (edu[0].start_year, edu[0].graduation_year) == (2019, 2021)
    assert edu[1].institution == "City College" and edu[1].graduation_year == 2019


def test_certification_extraction():
    certs, _ = extract_certifications([
        "• AWS Certified Developer - Amazon Web Services, Issued Mar 2022, Expires Mar 2025, ID: AB-123",
        "Prompt Engineering Course",
    ])
    assert certs[0].certification_name == "AWS Certified Developer"
    assert certs[0].issuing_organization == "Amazon Web Services"
    assert (certs[0].issue_date, certs[0].expiry_date, certs[0].credential_id) == ("2022-03", "2025-03", "AB-123")
    assert certs[1].certification_name == "Prompt Engineering Course" and certs[1].issuing_organization is None


# ---- whole profile ------------------------------------------------------------
def test_full_profile_on_synthetic_text(sample_docx):
    text = clean_text(extract_docx_text(sample_docx))
    p = extract_profile(text, "r1")
    assert p.contact.full_name == "Jane Sample"
    assert p.summary.current_job_title == "Machine Learning Engineer"
    assert len(p.work_experience) == 2 and len(p.projects) == 1
    assert p.education[0].specialization == "Computer Science"
    assert [c.certification_name for c in p.certifications] == ["Azure AI Engineer Associate", "Deep Learning Specialization"]
    assert p.achievements == ["Won first place at the 2021 company hackathon."]
    assert p.confidence_score is None
    assert p.summary.primary_domains == []  # never guessed
    assert p.provenance["summary.total_experience_years"].kind == "inferred"


def test_minimal_text_lists_missing_fields():
    p = extract_profile("Just some words without structure.", "r1")
    assert "contact.email" in p.missing_fields and "work_experience" in p.missing_fields
    assert p.work_experience == [] and p.technical_skills.all_skills() == []


# ---- headings seen on a real resume: the experience and projects sections were folded into "summary" and 0 jobs were extracted ----
SYNTHETIC = """Alex Example
alex.example@example.com | +91 90000 00000 | Pune

PROFESSIONAL SUMMARY
Data scientist with a focus on forecasting and applied machine learning.

RELEVANT WORK EXPERIENCE
NORTHWIND ANALYTICS PVT. LTD. | Pune, MH  Jun 2024 – Dec 2024
Data Science Intern
• Built demand forecasting models in Python and SQL, cutting forecast error by 12%.
• Automated weekly reporting with pandas.
CONTOSO LABS | Remote  Jan 2024 – May 2024
Machine Learning Intern
• Trained classification models with scikit-learn.

SELF PROJECTS
• Churn Predictor (live) — Gradient-boosted churn model built with Python and pandas,
Served through a small API.
• Sales Dashboard — Interactive dashboard in SQL and Tableau.

EDUCATION
B.Tech, Example University, 2020 - 2024

SKILLS
Languages: Python, SQL, R
"""


@pytest.mark.parametrize("heading,section", [
    ("RELEVANT WORK EXPERIENCE", "experience"), ("Professional Work Experience", "experience"), ("Internships", "experience"), ("Work Experience and Internships", "experience"),
    ("SELF PROJECTS", "projects"), ("Self Project", "projects"), ("Technical Projects", "projects"), ("Major Projects", "projects"),
    ("Skills Summary", "skills"), ("Academic Details", "education"), ("Certifications and Courses", "certifications"),
])
def test_common_resume_headings_are_recognised(heading, section):
    assert section in split_sections(f"Jane\n{heading}\nsomething under it\n")


def test_experience_and_projects_under_relevant_work_experience_and_self_projects_are_extracted():
    sections = split_sections(SYNTHETIC)
    assert sections["experience"] and sections["projects"]
    assert not any("Data Science Intern" in line for line in sections.get("summary", []))      # no longer swallowed by the summary
    jobs, _flags, _prov = extract_experience(sections["experience"])
    assert [j.job_title for j in jobs] == ["Data Science Intern", "Machine Learning Intern"]       # the title on the line AFTER "COMPANY | City  dates"
    assert [j.company_name for j in jobs] == ["NORTHWIND ANALYTICS PVT. LTD.", "CONTOSO LABS"]
    assert jobs[0].location == "Pune, MH" and jobs[0].start_date and jobs[0].end_date and not jobs[0].is_current
    assert len(jobs[0].achievements) + len(jobs[0].responsibilities) == 2 and any("12%" in a for a in jobs[0].achievements)
    assert "Python" in jobs[0].technologies_used


def test_bullet_per_project_layout_is_extracted_verbatim():
    projects, prov = extract_projects(split_sections(SYNTHETIC)["projects"])
    assert [p.project_name for p in projects] == ["Churn Predictor (live)", "Sales Dashboard"]
    assert "Served through a small API" in projects[0].description                                   # continuation line joins the previous project
    assert "Python" in projects[0].technologies and "SQL" in projects[1].technologies
    assert set(prov) == {"projects.0", "projects.1"}


def test_a_title_is_never_guessed_from_a_bullet_a_sentence_a_date_or_a_label():
    for nxt in ("• Built things in Python.", "Built a forecasting platform used by the whole finance team every day.", "Jan 2020 - Mar 2020", "Technologies: Python, SQL", ""):
        jobs, flags, _ = extract_experience(["ACME LTD | Pune  Jan 2021 – Dec 2021", nxt, "• Wrote code."])
        assert jobs[0].job_title is None, nxt
        assert any("job title" in f.reason.lower() for f in flags), nxt                               # reported for review instead of invented


def test_a_title_already_in_the_header_is_not_replaced_by_the_next_line():
    jobs, _f, _p = extract_experience(["Lead Engineer | Acme Corp | Remote", "Mar 2020 - Present", "Platform team", "• Led a team."])
    assert jobs[0].job_title == "Lead Engineer" and "Platform team" in (jobs[0].description or "")


def test_heading_first_project_layout_still_works():
    projects, _ = extract_projects(["Churn Predictor | github.com/x/y", "• Built a churn model.", "Technologies: Python", "", "Sales Dashboard", "• Built a dashboard."])
    assert [p.project_name for p in projects] == ["Churn Predictor", "Sales Dashboard"]


def test_a_technologies_line_inside_a_project_still_does_not_start_a_skills_section():
    s = split_sections("Self Projects\nChurn Predictor\nTechnologies: Python, pandas\nTech Stack: Python\nEducation\nB.Tech")
    assert "skills" not in s and any("Technologies:" in line for line in s["projects"])
