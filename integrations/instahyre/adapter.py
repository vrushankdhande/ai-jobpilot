"""Instahyre: MANUAL IMPORT ONLY.

No public job-search API was found and the platform is account/invite based, so there is no automated discovery code here
(the terms have NOT been verified by a human - see needs_human_confirmation in the policy files). A job the user found can be
imported by pasting its details; the URL is validated and stored, never fetched. No job-id pattern is assumed: de-duplication
uses the canonical URL.
"""
from integrations.base_adapter import ManualOnlyAdapter


class InstahyreAdapter(ManualOnlyAdapter):
    name = "instahyre"
    host_suffixes = ("instahyre.com",)
