from manager.metrics import MetricsStore


async def test_metrics_keep_verdicts_separate(tmp_path):
    store = MetricsStore(tmp_path / "metrics.sqlite")
    await store.open()
    metric = await store.record(run_id="r1", task_kind="coding", model_id="m1",
                                role="developer", success=True, duration_seconds=12.5,
                                input_tokens=100, output_tokens=50, cost=0.01, repairs=1)
    await store.record(run_id="r2", task_kind="coding", model_id="m1", success=False,
                       duration_seconds=20, cost=0.02, repairs=2)
    await store.set_verdicts(metric["id"], reviewer_verdict="pass",
                             executed_test="pass", user_acceptance="accepted")
    summary = await store.summary(task_kind="coding")
    model = summary["models"][0]
    assert model["model_id"] == "m1"
    assert model["runs"] == 2
    assert model["success_rate"] == 0.5
    assert model["avg_duration_seconds"] == 16.25
    assert model["reviewer_pass"] == 1
    assert model["executed_test_pass"] == 1
    assert model["user_accepted"] == 1
    assert model["repairs"] == 3
    await store.close()
