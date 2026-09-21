import json

from copilot import apply, logbook, paths
from copilot.models import JobPosting


def _posts(n=4):
    return [JobPosting(source="adzuna", title=f"Ops Manager {i}", employer=f"Co{i}",
                       location="Seattle, WA", url=f"https://x/{i}", description="logistics")
            for i in range(n)]


def test_build_queue_writes_topn(user_env, data_root):
    q = apply.build_queue(user_env, _posts(5), data_root=data_root, top_n=3)
    data = json.loads(q.read_text(encoding="utf-8"))
    assert len(data["items"]) == 3
    d = paths.applications_dir(user_env.user, data_root=data_root)
    assert (d / "queue.md").exists()
    assert (d / "results.template.json").exists()
    assert "cover_letter" in data["items"][0]


def test_record_results_logs_only_applied(user_env, data_root):
    apply.build_queue(user_env, _posts(3), data_root=data_root, top_n=3)
    d = paths.applications_dir(user_env.user, data_root=data_root)
    results = [
        {"employer": "Co0", "title": "Ops Manager 0", "url": "https://x/0",
         "outcome": "applied", "applied_at": paths.week_ending().isoformat()},
        {"employer": "Co1", "title": "Ops Manager 1", "url": "https://x/1", "outcome": "failed"},
    ]
    (d / "results.json").write_text(json.dumps(results), encoding="utf-8")
    summary = apply.record_results(user_env, data_root=data_root)
    assert summary["applied"] == 1 and summary["logged"] == 1
    # idempotent: a second pass logs nothing new
    assert apply.record_results(user_env, data_root=data_root)["logged"] == 0
    rows = [r for r in logbook.read_all(user_env.user, data_root)
            if r["activity_type"] == "applied_to_job"]
    assert len(rows) == 1
    assert "[auto-applied]" in rows[0]["notes"]


def test_run_apply_off(user_env, data_root):
    user_env.data["apply"]["mode"] = "off"
    assert apply.run_apply(user_env, _posts(3), data_root=data_root)["mode"] == "off"
    assert not (paths.applications_dir(user_env.user, data_root=data_root) / "queue.md").exists()


def test_run_apply_stage_builds_queue_no_submit(user_env, data_root):
    user_env.data["apply"]["mode"] = "stage"
    out = apply.run_apply(user_env, _posts(3), data_root=data_root)
    assert out["mode"] == "stage"
    assert (paths.applications_dir(user_env.user, data_root=data_root) / "queue.md").exists()
    # stage never logs an application
    assert logbook.count_valid(user_env.user, paths.week_ending(), data_root) == 0
