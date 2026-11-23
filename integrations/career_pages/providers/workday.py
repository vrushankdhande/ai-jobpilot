"""Workday: DETECTED but NOT IMPLEMENTED (PENDING_POLICY_REVIEW).

Workday career sites have no documented public API; their JSON endpoints are not an approved integration, so there is
no extraction code here. A job from a Workday site can still be imported manually (user-supplied details).
"""
from integrations.base_adapter import AdapterResult, JobAdapter, RawJob, SearchQuery
from integrations.policy import PolicyViolation


class WorkdayAdapter(JobAdapter):
    name = "workday"
    automated_discovery_supported = False

    def search(self, query: SearchQuery) -> AdapterResult:
        self.require_discovery()
        raise PolicyViolation("unreachable")  # pragma: no cover

    def fetch_details(self, job: RawJob) -> RawJob:
        self.require_discovery()
        raise PolicyViolation("unreachable")  # pragma: no cover

    def to_raw(self, payload: dict, **context) -> RawJob:
        raise PolicyViolation("workday: no authorised extraction method is implemented")
