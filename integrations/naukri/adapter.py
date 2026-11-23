"""Naukri: DISABLED for automation (PENDING_POLICY_REVIEW); manual import only.

Naukri's terms list automated crawling/scraping as prohibited and no public job-search API was found. Automation
stays off until a human verifies the policy. A job the user found can be imported by pasting its details.
"""
from integrations.base_adapter import ManualOnlyAdapter


class NaukriAdapter(ManualOnlyAdapter):
    name = "naukri"
    host_suffixes = ("naukri.com",)
    id_patterns = (r"-(\d{9,})(?:[?#/]|$)", r"[?&]jobId=(\d{6,})")
