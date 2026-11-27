"""Resumes often show only "LinkedIn | GitHub | Portfolio" with the web addresses hidden behind the words. The parser writes each address
next to its words, so the contact links are found and the profile validator can see them in the source text. Nothing is fetched."""
import pymupdf

from ai.validators.factuality_validator import validate_profile
from engines.resume.profile_extractor import _norm_url
from engines.resume.resume_parser import build_profile, clean_text, extract_pdf_text


def _pdf(tmp_path, lines, links):
    """lines: text lines top to bottom; links: (line index, word, uri) - the uri is attached to that word as a hidden hyperlink."""
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for i, line in enumerate(lines):
        page.insert_text((72, y), line, fontsize=11)
        for li, word, uri in links:
            if li == i:
                for r in page.search_for(word):
                    if abs(r.y1 - y) < 6:
                        page.insert_link({"kind": pymupdf.LINK_URI, "from": r, "uri": uri})
        y += 18
    path = tmp_path / "resume.pdf"
    doc.save(path)
    return path


HEADER = ["Jane Sample", "Pune, India | +91-9000000000 | jane.sample@example.com | Portfolio", "GitHub | LinkedIn",
          "PROFESSIONAL EXPERIENCE", "AI Engineer, Acme Analytics, Pune  Jan 2022 - Present",
          "- Built retrieval pipelines in Python for search.", "PROJECTS", "- Loan Triage Agent (GitHub) - an LLM agent for loan triage"]
LINKS = [(1, "Portfolio", "https://janesample.github.io/"), (2, "GitHub", "https://github.com/janesample"),
         (2, "LinkedIn", "https://www.linkedin.com/in/janesample/"), (7, "GitHub", "https://github.com/janesample/loan-triage"),
         (1, "jane.sample@example.com", "mailto:jane.sample@example.com")]


def test_hidden_link_addresses_are_written_next_to_their_words(tmp_path):
    text = clean_text(extract_pdf_text(_pdf(tmp_path, HEADER, LINKS)))
    assert "Portfolio (https://janesample.github.io/)" in text
    assert "GitHub (https://github.com/janesample) | LinkedIn (https://www.linkedin.com/in/janesample/)" in text
    assert "Loan Triage Agent (GitHub (https://github.com/janesample/loan-triage))" in text   # a project link lands next to its own word
    assert "mailto:" not in text                                                       # only web addresses; the e-mail is visible already


def test_contact_links_are_found_and_pass_the_source_check(tmp_path):
    text = clean_text(extract_pdf_text(_pdf(tmp_path, HEADER, LINKS)))
    p = build_profile(text, "r1")
    assert p.contact.linkedin_url == "https://www.linkedin.com/in/janesample"
    assert p.contact.github_url == "https://github.com/janesample"
    assert p.contact.portfolio_url == "https://janesample.github.io"                   # no stray ")" from the surrounding text
    assert not [w for w in validate_profile(p, text) if w.field.startswith("contact.")]


def test_a_link_whose_address_is_visible_or_has_no_words_is_not_duplicated_or_guessed(tmp_path):
    lines = ["Jane Sample", "https://github.com/janesample", "Skills: Python"]
    links = [(1, "https://github.com/janesample", "https://github.com/janesample")]
    text = extract_pdf_text(_pdf(tmp_path, lines, links))
    assert text.count("github.com/janesample") == 1
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Jane Sample", fontsize=11)
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(300, 300, 320, 320), "uri": "https://www.kaggle.com/janesample"})   # an icon: no words
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(300, 340, 320, 360), "uri": "javascript:alert(1)"})                 # not a web address
    path = tmp_path / "icons.pdf"
    doc.save(path)
    out = extract_pdf_text(path)
    assert "Links: https://www.kaggle.com/janesample" in out and "javascript" not in out


def test_url_normalisation_keeps_balanced_brackets():
    assert _norm_url("https://x.io/)") == "https://x.io"
    assert _norm_url("https://en.wikipedia.org/wiki/Foo_(bar)") == "https://en.wikipedia.org/wiki/Foo_(bar)"
