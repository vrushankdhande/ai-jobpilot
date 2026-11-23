"""Display name of a job's source (the Source column on the Job Matching page)."""
from database.repositories.jobs_repository import JobsRepository


class _Col:
    def __init__(self, docs): self.docs = docs
    def find(self, *_a, **_k): return iter(self.docs)


def _labels(docs):
    repo = JobsRepository.__new__(JobsRepository)
    repo._jobs = _Col(docs)
    return repo.sources_for([d["job_id"] for d in docs])


def test_source_names():
    out = _labels([
        {"job_id": "1", "source": "linkedin"},
        {"job_id": "2", "source": "naukri"},
        {"job_id": "3", "source": "company_career_page", "source_refs": [{"provider": "greenhouse"}]},
        {"job_id": "4", "source": "manual_paste", "job_url": "https://www.google.com/about/careers/applications/jobs/results/123"},
        {"job_id": "5", "source": "manual_paste", "job_url": "https://www.naukri.com/job-listings-x"},
        {"job_id": "6", "source": "manual_paste"},
        {"job_id": "7", "source": "manual_paste", "job_url": "https://careers.acme.io/jobs/1"},
    ])
    assert out == {"1": "LinkedIn", "2": "Naukri", "3": "Career page (greenhouse)", "4": "Google", "5": "Naukri", "6": "Manual entry", "7": "careers.acme.io"}
