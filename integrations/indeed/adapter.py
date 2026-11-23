"""Indeed: MANUAL IMPORT ONLY.

Indeed's terms prohibit robots/scrapers and its APIs are partner-only, so there is no automated discovery code here.
A job the user found can be imported by pasting its details; the URL is validated and stored, never fetched.
"""
from integrations.base_adapter import ManualOnlyAdapter


class IndeedAdapter(ManualOnlyAdapter):
    name = "indeed"
    host_suffixes = ("indeed.com",)
    id_patterns = (r"[?&](?:jk|vjk)=([0-9a-fA-F]{8,})", r"/viewjob/([0-9a-fA-F]{8,})")
