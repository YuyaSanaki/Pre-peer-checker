"""照合進捗トラッカー（WebUI 進捗バー・残り時間）。"""

from __future__ import annotations

from pathlib import Path

from pre_peer_checker.pipeline.progress import (
    ProgressTracker,
    Stage,
    load_history,
    save_history,
)


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _tracker(clock: FakeClock, history: dict[str, float] | None = None) -> ProgressTracker:
    tr = ProgressTracker(history=history, clock=clock)
    tr.set_stages(
        [
            Stage("a", "A", 10.0),
            Stage("llm", "LLM", 100.0),
            Stage("b", "B", 10.0),
        ]
    )
    return tr


def test_snapshot_stage_and_sub_progress() -> None:
    clock = FakeClock()
    tr = _tracker(clock)
    tr.start("a", "scanning")
    snap = tr.snapshot()
    assert snap["stage_id"] == "a"
    assert snap["stage_index"] == 1
    assert snap["n_stages"] == 3
    assert snap["detail"] == "scanning"
    assert snap["eta_s"] is None  # 開始直後は推定しない

    clock.t = 10.0
    tr.start("llm")
    tr.update(done=0, total=4, detail="Figure 1")
    clock.t = 30.0
    tr.update(done=1, detail="Figure 2")
    snap = tr.snapshot()
    assert snap["sub_done"] == 1 and snap["sub_total"] == 4
    # 1 件目だけでは実測ペースを信用せず見積り（100 - 経過 20 秒）+ 後続 b（10 秒）
    assert snap["eta_s"] == 90.0
    assert [s["status"] for s in snap["stages"]] == ["done", "active", "pending"]
    assert snap["stages"][0]["seconds"] == 10.0


def test_first_item_warmup_is_excluded_from_per_item_rate() -> None:
    clock = FakeClock()
    tr = ProgressTracker(clock=clock)
    tr.set_stages([Stage("llm", "LLM", 500.0)])
    tr.start("llm")
    tr.update(done=0, total=10)
    clock.t = 60.0  # 1 件目: モデル読込 50 秒 + 生成 10 秒
    tr.update(done=1)
    for i in range(2, 6):
        clock.t = 60.0 + 10.0 * (i - 1)
        tr.update(done=i)
    # 1 件目以降 4 件で実測を全面採用: 件あたり 10 秒 × 残り 5 件
    # （1 件目込みで外挿すると 100 秒、見積りのままだと 400 秒）
    assert tr.snapshot()["eta_s"] == 50.0


def test_few_fast_items_do_not_jump_to_near_complete() -> None:
    clock = FakeClock()
    tr = ProgressTracker(clock=clock)
    tr.set_stages([Stage("corpus", "Corpus", 300.0)])
    tr.start("corpus")
    clock.t = 50.0  # サブ進捗の無い前処理
    tr.update(done=0, total=80)
    for i in range(1, 6):  # 最初の数ペアだけ一瞬で終わる
        clock.t = 50.0 + 0.1 * i
        tr.update(done=i)
    snap = tr.snapshot()
    assert snap["eta_s"] > 150.0
    assert snap["fraction"] < 0.3


def test_overrun_stage_without_sub_progress_keeps_growing() -> None:
    clock = FakeClock()
    tr = ProgressTracker(clock=clock)
    tr.set_stages([Stage("scan", "Scan", 10.0)])
    tr.start("scan")
    clock.t = 5.0
    assert tr.snapshot()["eta_s"] == 5.0
    clock.t = 100.0
    assert tr.snapshot()["eta_s"] == 20.0


def test_fraction_is_monotonic_and_finishes_at_one() -> None:
    clock = FakeClock()
    tr = _tracker(clock)
    tr.start("a")
    clock.t = 5.0
    tr.start("llm")
    tr.update(done=1, total=2)
    clock.t = 10.0
    f1 = tr.snapshot()["fraction"]
    # 2 件目が遅く見積りが伸びても、バーは後退しない
    tr.update(done=1, total=10)
    clock.t = 11.0
    f2 = tr.snapshot()["fraction"]
    assert f2 >= f1
    assert f2 < 1.0
    tr.finish()
    snap = tr.snapshot()
    assert snap["finished"] is True
    assert snap["fraction"] == 1.0
    assert snap["eta_s"] is None


def test_completed_stages_calibrate_future_estimates() -> None:
    clock = FakeClock()
    tr = _tracker(clock)
    tr.start("a")
    clock.t = 100.0  # 見積り 10 秒のところ 100 秒 → このマシンは遅い
    tr.start("llm")
    clock.t = 101.0
    slow_eta = tr.snapshot()["eta_s"]

    clock2 = FakeClock()
    tr2 = _tracker(clock2)
    tr2.start("a")
    clock2.t = 10.0
    tr2.start("llm")
    clock2.t = 11.0
    normal_eta = tr2.snapshot()["eta_s"]
    assert slow_eta > normal_eta * 2


def test_history_scales_estimates(tmp_path: Path) -> None:
    hist = tmp_path / "progress_history.json"
    assert load_history(hist) == {}
    save_history({"llm": 3.0}, hist)
    save_history({"llm": 1.0}, hist)
    assert load_history(hist) == {"llm": 2.0}

    clock = FakeClock()
    base = _tracker(clock)
    base.start("a")
    clock.t = 10.0
    base.start("llm")
    clock.t = 11.0
    eta_base = base.snapshot()["eta_s"]

    clock2 = FakeClock()
    calibrated = _tracker(clock2, history=load_history(hist))
    calibrated.start("a")
    clock2.t = 10.0
    calibrated.start("llm")
    clock2.t = 11.0
    assert calibrated.snapshot()["eta_s"] > eta_base * 1.5


def test_stage_ratios_and_set_stages_keeps_current() -> None:
    clock = FakeClock()
    tr = _tracker(clock)
    tr.start("a")
    clock.t = 20.0
    tr.start("llm")
    tr.set_stages([Stage("a", "A", 10.0), Stage("llm", "LLM", 50.0), Stage("b", "B", 1.0)])
    snap = tr.snapshot()
    assert snap["stage_id"] == "llm"
    assert tr.stage_ratios() == {"a": 2.0}


def test_set_stages_keeps_started_stages_missing_from_new_plan() -> None:
    clock = FakeClock()
    tr = ProgressTracker(clock=clock)
    tr.set_stages([Stage("prep", "Prep", 5.0), Stage("a", "A", 10.0)])
    tr.start("prep")
    clock.t = 30.0
    tr.set_stages([Stage("a", "A", 10.0), Stage("b", "B", 10.0)])
    assert tr.snapshot()["stage_id"] == "prep"
    tr.start("a")
    clock.t = 40.0
    tr.set_stages([Stage("a", "A", 10.0), Stage("b", "B", 10.0)])
    snap = tr.snapshot()
    assert [s["id"] for s in snap["stages"]] == ["prep", "a", "b"]
    assert snap["stages"][0]["status"] == "done"
    assert snap["stages"][0]["seconds"] == 30.0
    assert snap["stage_index"] == 2


def test_elapsed_is_frozen_at_finish_and_matches_stage_sum() -> None:
    clock = FakeClock()
    tr = _tracker(clock)
    tr.start("a")
    clock.t = 10.0
    tr.start("llm")
    clock.t = 25.0
    tr.start("b")
    clock.t = 30.0
    tr.finish()
    clock.t = 500.0  # 完了後の後処理（監査ハッシュ等）は所要時間に含めない
    tr.finish()
    snap = tr.snapshot()
    assert snap["elapsed_s"] == 30.0
    assert sum(s["seconds"] for s in snap["stages"]) == snap["elapsed_s"]
