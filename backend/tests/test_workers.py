from app.tasks import celery_app


def test_worker_schedule_is_daily_and_submission_is_fail_closed():
    scheduled=celery_app.conf.beat_schedule["schedule-permitted-public-discovery"]
    assert scheduled["task"]=="bruteapply.discover_enabled_sources"
    assert 7 in scheduled["schedule"].hour and 15 in scheduled["schedule"].minute
    task=celery_app.tasks["bruteapply.submit_application"]
    try:
        task.run("00000000-0000-4000-8000-000000000001")
    except RuntimeError as exc:
        assert "submission remains disabled" in str(exc)
    else:
        raise AssertionError("submission handler unexpectedly became runnable")
