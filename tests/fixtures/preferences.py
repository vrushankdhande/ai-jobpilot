"""Candidate job preferences used by the evaluation dataset (synthetic)."""
from config.scoring_config import Preferences


def evaluation_preferences(**overrides) -> Preferences:
    data = dict(
        locations=["Pune"], remote=True, hybrid=None, onsite=None, open_to_relocation=False,
        employment_types=["full-time"], min_salary=2_000_000, salary_currency="INR", work_authorization=["India"],
    )
    data.update(overrides)
    return Preferences(**data)
