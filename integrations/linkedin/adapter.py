"""LinkedIn: MANUAL IMPORT ONLY.

LinkedIn's terms prohibit automated collection and no public job-search API is available, so this adapter has
no network code at all: no scraping, no automated login. A job the user found themselves can be imported by
pasting its details (the URL is only validated and stored, never fetched).
"""
from integrations.base_adapter import ManualOnlyAdapter


class LinkedInAdapter(ManualOnlyAdapter):
    name = "linkedin"
    host_suffixes = ("linkedin.com",)
    id_patterns = (r"/jobs/view/(?:[\w\-%]*?-)?(\d{6,})", r"[?&]currentJobId=(\d{6,})")
