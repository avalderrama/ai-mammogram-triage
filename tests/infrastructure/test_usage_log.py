"""Anonymous usage counters: counts per day, each person once per day, nothing that identifies anyone kept."""
from mammo_application.abstractions import UsageEvent
from mammo_infrastructure import JsonUsageLog

SECRET = b"server-secret-key-of-the-pilot"


class Clock:
    def __init__(self, day="2026-10-22"):
        self.day = day

    def __call__(self):
        return self.day


def usage_log(path, clock):
    return JsonUsageLog(path, SECRET, "America/Bogota", today=clock)


def test_counts_outcomes_views_and_distinct_people_per_day(tmp_path):
    log = usage_log(tmp_path / "usage.json", Clock())
    log.record(UsageEvent("high_priority", views=2, detailed_views=1, requester="Doctor@Example.org"))
    log.record(UsageEvent("review", views=1, detailed_views=1, requester="doctor@example.org"))
    log.record(UsageEvent("not_a_mammogram", requester="other@example.org"))
    log.record(UsageEvent("error"))
    [day] = log.daily(30)
    assert (day.day, day.analyses, day.people) == ("2026-10-22", 4, 2)
    assert (day.high_priority, day.review, day.no_suspicious_findings) == (1, 1, 0)
    assert (day.not_a_mammogram, day.errors, day.views, day.detailed_views) == (1, 1, 3, 2)


def test_a_person_counts_again_on_another_day_and_yesterdays_hashes_are_dropped(tmp_path):
    path, clock = tmp_path / "usage.json", Clock()
    log = usage_log(path, clock)
    log.record(UsageEvent("review", requester="doctor@example.org"))
    clock.day = "2026-10-23"
    log.record(UsageEvent("review", requester="doctor@example.org"))
    assert [(d.day, d.people) for d in log.daily(30)] == [("2026-10-23", 1), ("2026-10-22", 1)]
    saved = path.read_text(encoding="utf-8")
    assert saved.count('"people":[') == 1 and len(saved.split('"people":[')[1].split("]")[0].split(",")) == 1


def test_counts_survive_a_restart_without_counting_a_person_twice(tmp_path):
    path, clock = tmp_path / "usage.json", Clock()
    usage_log(path, clock).record(UsageEvent("review", requester="doctor@example.org"))
    restarted = usage_log(path, clock)
    restarted.record(UsageEvent("review", requester="doctor@example.org"))
    [day] = restarted.daily(30)
    assert (day.analyses, day.people) == (2, 1)


def test_the_file_holds_no_e_mail(tmp_path):
    path = tmp_path / "usage.json"
    usage_log(path, Clock()).record(UsageEvent("review", requester="doctor@example.org"))
    assert "doctor" not in path.read_text(encoding="utf-8")


def test_a_storage_failure_never_breaks_an_analysis(tmp_path):
    log = usage_log(tmp_path / "missing-folder" / "usage.json", Clock())
    log.record(UsageEvent("review"))  # cannot be saved: logged, not raised
    assert log.daily(30)[0].analyses == 1


def test_an_unreadable_file_starts_empty(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text("not json", encoding="utf-8")
    assert usage_log(path, Clock()).daily(30) == []


def test_malformed_files_start_empty_instead_of_stopping_the_server(tmp_path):
    for content in ("null", "[]", '{"days": {"2026-10-22": 5}, "current": {"day": null}}'):
        path = tmp_path / "usage.json"
        path.write_text(content, encoding="utf-8")
        log = usage_log(path, Clock())
        log.record(UsageEvent("review"))
        assert log.daily(30)[0].analyses == 1


def test_yesterdays_hashes_are_dropped_even_without_a_new_analysis(tmp_path):
    path, clock = tmp_path / "usage.json", Clock()
    usage_log(path, clock).record(UsageEvent("review", requester="doctor@example.org"))
    clock.day = "2026-10-24"
    usage_log(path, clock)  # a restart on a later day, with no analysis yet
    assert '"people":[]' in path.read_text(encoding="utf-8")


def test_service_tokens_are_not_counted_as_people(tmp_path):
    log = usage_log(tmp_path / "usage.json", Clock())
    log.record(UsageEvent("review", requester="unknown"))
    assert log.daily(30)[0].people == 0
