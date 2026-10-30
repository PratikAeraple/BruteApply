from app.tasks import celery_app


def test_worker_schedule_is_conservative_multiple_daily_scans_and_submission_is_fail_closed():
    schedules=[value for key,value in celery_app.conf.beat_schedule.items()
               if key.startswith("schedule-permitted-public-discovery-")]
    assert len(schedules)==3
    assert all(item["task"]=="bruteapply.discover_enabled_sources" for item in schedules)
    assert {hour for item in schedules for hour in item["schedule"].hour}=={7,12,17}
    assert all(15 in item["schedule"].minute for item in schedules)
    task=celery_app.tasks["bruteapply.submit_application"]
    try:
        task.run("00000000-0000-4000-8000-000000000001")
    except RuntimeError as exc:
        assert "submission remains disabled" in str(exc)
    else:
        raise AssertionError("submission handler unexpectedly became runnable")
