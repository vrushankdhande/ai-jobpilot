"""Location preferences: a country matches "City, Country", and remote jobs can be limited to the preferred places."""
from datetime import datetime, timezone

from database.models.job import JobRecord
from engines.discovery.deduplication import location_keys, title_key
from engines.discovery.discovery_engine import matches_query
from integrations.base_adapter import SearchQuery

NOW = datetime(2026, 11, 21, tzinfo=timezone.utc)


def _job(location, work_mode=None, title="Machine Learning Engineer"):
    return JobRecord(job_id="a" * 32, source="company_career_page", job_title=title, title_key=title_key(title), company_name="Acme", company_key="acme",
                     location=location, location_keys=location_keys(location), work_mode=work_mode, discovered_at=NOW, last_updated_at=NOW)


def _ok(job, **q):
    return matches_query(job, SearchQuery(**q), None, NOW)


def test_country_preference_matches_city_in_that_country():
    assert _ok(_job(["Bengaluru, India"]), locations=["Pune", "India"])[0]
    assert _ok(_job(["Pune, Maharashtra"]), locations=["Pune", "India"])[0]
    assert _ok(_job(["San Francisco, CA, US"]), locations=["Pune", "India"]) == (False, "location")


def test_remote_jobs_ignore_location_unless_asked():
    us_remote = _job(["United States"], "remote")
    assert _ok(us_remote, locations=["India"])[0]                                              # default: remote is kept wherever it is
    assert _ok(us_remote, locations=["India"], remote_within_locations=True) == (False, "location")
    assert _ok(_job(["India"], "remote"), locations=["India"], remote_within_locations=True)[0]
    assert _ok(_job([], "remote"), locations=["India"], remote_within_locations=True)[0]      # no place named: unknown never excludes


def test_postings_older_than_the_limit_are_skipped_and_undated_ones_kept():
    from datetime import timedelta
    old, recent, undated = _job(["Pune, India"]), _job(["Pune, India"]), _job(["Pune, India"])
    old.posted_date, recent.posted_date = NOW - timedelta(days=2179), NOW - timedelta(days=45)
    assert _ok(old, posted_within_days=90) == (False, "date_posted")          # e.g. a 2020 posting
    assert _ok(recent, posted_within_days=90)[0]
    assert _ok(undated, posted_within_days=90)[0]                               # no date = unknown, never a reason to skip
    assert _ok(old)[0]                                                          # no limit set: nothing is skipped for age


def test_indian_city_or_state_without_the_word_india_still_matches_india():
    # Real location strings from the 23 Nov 2026 career-page run that were wrongly dropped.
    for loc in (["Bangalore"], ["Bangalore, IND"], ["Gurugram"], ["Noida, Uttar Pradesh"], ["India Pune"], ["in Bangalore"], ["Bengaluru"]):
        assert _ok(_job(loc), locations=["Pune", "India"])[0], loc
    assert _ok(_job(["India Pune"]), locations=["Pune"])[0]                                    # city preference found inside "India Pune"


def test_places_outside_india_are_not_mistaken_for_india():
    for loc in (["Indianapolis, IN"], ["Indiana, US"], ["London, UK"], ["San Francisco, CA, US"], ["Singapore"]):
        assert _ok(_job(loc), locations=["Pune", "India"]) == (False, "location"), loc
