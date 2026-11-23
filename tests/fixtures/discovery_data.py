"""Synthetic source responses (all fictional) + a mock HTTP layer. No real network is ever used in tests."""
import html
import json

import httpx

DESC_ML = """<p>Acme Analytics is hiring a Machine Learning Engineer to build retrieval systems.</p>
<h3>Responsibilities</h3><ul><li>Design and deploy machine learning models to production.</li><li>Build RAG pipelines with LangChain.</li></ul>
<h3>Requirements</h3><ul><li>3+ years of experience in machine learning.</li><li>Strong Python and SQL skills.</li><li>Experience with PyTorch or TensorFlow.</li></ul>
<h3>Nice to have</h3><ul><li>Docker and Kubernetes.</li></ul><p>Location: Pune, India (Hybrid). Salary: INR 25-35 LPA. Full-time.</p>"""

DESC_SALES = "<p>Sales Manager for enterprise accounts. Requirements: 5+ years of B2B sales experience. Location: Mumbai.</p>"

GREENHOUSE_LIST = {"jobs": [
    {"id": 101, "title": "Machine Learning Engineer (m/f/d)", "absolute_url": "https://boards.greenhouse.io/acmeanalytics/jobs/101?gh_src=abc123&utm_source=li",
     "location": {"name": "Pune, India"}, "updated_at": "2026-11-10T09:00:00-05:00", "first_published": "2026-11-01T08:30:00-05:00",
     "content": html.escape(DESC_ML), "departments": [{"name": "Engineering"}], "requisition_id": "R-5521"},
    {"id": 102, "title": "Sales Manager", "absolute_url": "https://boards.greenhouse.io/acmeanalytics/jobs/102",
     "location": {"name": "Mumbai, India"}, "updated_at": "2026-10-01T09:00:00Z", "first_published": "2026-09-20T09:00:00Z",
     "content": html.escape(DESC_SALES)},
]}

LEVER_LIST = [
    {"id": "11111111-2222-3333-4444-555555555555", "text": "Senior Data Scientist", "hostedUrl": "https://jobs.lever.co/betalabs/11111111-2222-3333-4444-555555555555",
     "categories": {"location": "Bengaluru, India", "commitment": "Full-time", "team": "Data", "allLocations": ["Bengaluru, India"]},
     "descriptionPlain": "Beta Labs is hiring a Senior Data Scientist.", "workplaceType": "hybrid", "createdAt": 1762070400000,
     "lists": [{"text": "Requirements", "content": "<li>5+ years of experience in data science.</li><li>Python, SQL and Scikit-learn.</li>"}],
     "salaryRange": {"min": 3000000, "max": 4000000, "currency": "INR", "interval": "per-year-salary"}},
]

JSONLD_PAGE = """<html><head><script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",
"title":"Backend Engineer","description":"<p>Build APIs. Requirements: Python and PostgreSQL. 3+ years of experience.</p>",
"datePosted":"2026-11-05","employmentType":"FULL_TIME","hiringOrganization":{"@type":"Organization","name":"Gamma Corp"},
"jobLocation":{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Pune","addressRegion":"MH","addressCountry":"IN"}},
"identifier":{"@type":"PropertyValue","value":"GC-77"},
"baseSalary":{"@type":"MonetaryAmount","currency":"INR","value":{"@type":"QuantitativeValue","minValue":1800000,"maxValue":2600000,"unitText":"YEAR"}}}
</script></head><body><h1>Backend Engineer</h1></body></html>"""

LISTING_PAGE = """<html><body><a href="/jobs/backend-engineer">Backend</a> <a href="/jobs/data-analyst">Data</a>
<a href="https://other.example/jobs/x">elsewhere</a></body></html>"""


def json_response(payload, status=200):
    return httpx.Response(status, json=payload)


class Recorder:
    """Mock HTTP transport. ``routes`` maps URL-prefix -> Response | callable(request) | Exception."""

    def __init__(self, routes: dict | None = None):
        self.routes = dict(routes or {})  # copy: tests mutate routes and must not leak into each other
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        for prefix in sorted(self.routes, key=len, reverse=True):
            if url.startswith(prefix):
                item = self.routes[prefix]
                if isinstance(item, Exception):
                    raise item
                if isinstance(item, httpx.Response):  # responses are single-use streams: always hand out a fresh copy
                    return httpx.Response(item.status_code, headers=item.headers, content=item.content)
                return item(request) if callable(item) else item
        return httpx.Response(404, text="not found")

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    @property
    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


def html_response(text, status=200):
    return httpx.Response(status, text=text, headers={"content-type": "text/html; charset=utf-8"})
