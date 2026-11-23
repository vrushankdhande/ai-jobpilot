"""Synthetic candidate used to generate sample PDF/DOCX fixtures.

All details are fictional (example.com addresses). This is NOT anyone's real resume.
"""
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent

# ("h", text) = heading, ("b", text) = bullet, ("p", text) = plain line
SAMPLE_LINES = [
    ("p", "Jane Sample"),
    ("p", "Machine Learning Engineer"),
    ("p", "jane.sample@example.com | +91 98765 43210 | Pune, India"),
    ("p", "linkedin.com/in/janesample | github.com/janesample | janesample.dev"),
    ("h", "PROFESSIONAL SUMMARY"),
    ("p", "Machine learning engineer with experience building NLP and recommendation systems."),
    ("h", "TECHNICAL SKILLS"),
    ("p", "Programming Languages: Python, SQL"),
    ("p", "Machine Learning: Scikit-learn, XGBoost"),
    ("p", "Deep Learning: PyTorch, TensorFlow"),
    ("p", "Generative AI: RAG, LLMs"),
    ("p", "LLM Frameworks: LangChain"),
    ("p", "Databases: MongoDB, PostgreSQL"),
    ("p", "Cloud: Azure, AWS"),
    ("p", "Tools: Docker, Git"),
    ("h", "WORK EXPERIENCE"),
    ("p", "Senior ML Engineer | Acme Analytics | Pune, India"),
    ("p", "Jan 2022 - Present"),
    ("b", "Built a RAG pipeline using LangChain and MongoDB for internal search."),
    ("b", "Reduced inference latency by 35% by migrating models to ONNX."),
    ("p", "Data Scientist | Beta Labs"),
    ("p", "Jun 2019 - Dec 2021"),
    ("b", "Developed churn prediction models in Python and Scikit-learn."),
    ("b", "Improved model accuracy from 82% to 91%."),
    ("h", "PROJECTS"),
    ("p", "Resume Matcher"),
    ("p", "Technologies: Python, FastAPI, MongoDB"),
    ("b", "Matches resumes to job descriptions using embeddings."),
    ("b", "Processed 10,000 resumes with 94% accuracy."),
    ("h", "EDUCATION"),
    ("p", "Bachelor of Technology in Computer Science"),
    ("p", "Sample Institute of Technology, 2015 - 2019"),
    ("p", "CGPA: 8.4"),
    ("h", "CERTIFICATIONS"),
    ("p", "Azure AI Engineer Associate - Microsoft (2023)"),
    ("p", "Deep Learning Specialization - Coursera, Credential ID: ABC123XYZ"),
    ("h", "ACHIEVEMENTS"),
    ("b", "Won first place at the 2021 company hackathon."),
]


def build_pdf(path: Path) -> None:
    import pymupdf

    doc = pymupdf.open()
    page, y = doc.new_page(), 60
    for kind, text in SAMPLE_LINES:
        if y > 780:
            page, y = doc.new_page(), 60
        if kind == "h":
            y += 8
            page.insert_text((50, y), text, fontsize=12, fontname="hebo")
        else:
            page.insert_text((50, y), ("• " + text) if kind == "b" else text, fontsize=10, fontname="helv")
        y += 16
    doc.save(str(path))
    doc.close()


def build_docx(path: Path) -> None:
    from docx import Document

    doc = Document()
    for kind, text in SAMPLE_LINES:
        if kind == "h":
            doc.add_heading(text, level=2)
        elif kind == "b":
            doc.add_paragraph(text, style="List Bullet")
        else:
            doc.add_paragraph(text)
    doc.save(str(path))


def ensure_fixtures() -> tuple[Path, Path]:
    pdf, docx = FIXTURE_DIR / "sample_resume.pdf", FIXTURE_DIR / "sample_resume.docx"
    if not pdf.exists():
        build_pdf(pdf)
    if not docx.exists():
        build_docx(docx)
    return pdf, docx


if __name__ == "__main__":
    print(*ensure_fixtures())
