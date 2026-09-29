"""照合パイプラインの進捗と残り時間の推定（WebUI 表示用）。

各ステージに「見積り秒数」（入力ファイル数から算出）を持たせ、実測で補正する:

- 実行中ステージにサブ進捗（i/n）があれば、その実測ペースで残りを外挿
- 完了済みステージの「実測 / 見積り」比で、未着手ステージの見積りを補正
- 前回実行のステージ別比率を ``cache/progress_history.json`` に残し、このマシンの速度を次回へ持ち越す
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pre_peer_checker.catalog.paths import REPO_ROOT

HISTORY_PATH = REPO_ROOT / "cache" / "progress_history.json"

# 序盤の軽いステージが見積りより速くても重いステージ（LLM・画像照合）が速いとは限らないので、
# 事前分を足して縮約し、さらに補正係数の幅を制限する
_PRIOR_SECONDS = 60.0
_FACTOR_CLAMP = (0.5, 4.0)
_RATIO_CLAMP = (0.05, 20.0)


@dataclass
class Stage:
    id: str
    label: str
    estimate_s: float


class ProgressTracker:
    """スレッド安全な進捗トラッカー（書き手＝照合スレッド、読み手＝API ポーリング）。"""

    def __init__(
        self,
        *,
        history: dict[str, float] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._history = dict(history or {})
        self._stages: list[Stage] = []
        self._durations: dict[str, float] = {}
        self._current: int = -1
        self._t0 = clock()
        self._t_end: float | None = None
        self._stage_t0 = self._t0
        self._sub_done: int | None = None
        self._sub_total: int | None = None
        # 1 件目完了時刻と最新完了時刻（1 件目はモデル読込を含みがちなので件あたり速度から除く）
        self._sub_first_t: float | None = None
        self._sub_first_done = 0
        self._sub_last_t: float | None = None
        self._detail = ""
        self._finished = False
        self._max_fraction = 0.0

    # --- 書き手 API ---
    def set_stages(self, stages: list[Stage]) -> None:
        """ステージ一覧を差し替える（同じ id の完了記録・現在位置は保持）。

        開始済みで新しい一覧に無いステージ（照合前の準備など）は先頭に残す。
        """
        with self._lock:
            current_id = self._stages[self._current].id if self._current >= 0 else None
            new_ids = {s.id for s in stages}
            kept = [
                s
                for s in self._stages
                if s.id not in new_ids and (s.id in self._durations or s.id == current_id)
            ]
            self._stages = kept + list(stages)
            self._current = next(
                (i for i, s in enumerate(self._stages) if s.id == current_id), -1
            )

    def start(self, stage_id: str, detail: str = "") -> None:
        """``stage_id`` を開始し、実行中だったステージを完了扱いにする。"""
        with self._lock:
            now = self._clock()
            self._close_current(now)
            idx = next((i for i, s in enumerate(self._stages) if s.id == stage_id), None)
            if idx is None:
                self._stages.append(Stage(stage_id, stage_id, 1.0))
                idx = len(self._stages) - 1
            self._current = idx
            self._stage_t0 = now
            self._sub_done = None
            self._sub_total = None
            self._sub_first_t = None
            self._sub_first_done = 0
            self._sub_last_t = None
            self._detail = detail

    def update(
        self,
        *,
        done: int | None = None,
        total: int | None = None,
        detail: str | None = None,
    ) -> None:
        with self._lock:
            if total is not None:
                self._sub_total = max(int(total), 0)
            if done is not None:
                done = max(int(done), 0)
                if done > (self._sub_done or 0):
                    now = self._clock()
                    if self._sub_first_t is None:
                        self._sub_first_t = now
                        self._sub_first_done = done
                    self._sub_last_t = now
                self._sub_done = done
            if detail is not None:
                self._detail = detail

    def finish(self) -> None:
        with self._lock:
            if self._finished:
                return
            self._t_end = self._clock()
            self._close_current(self._t_end)
            self._current = -1
            self._detail = ""
            self._finished = True

    # --- 読み手 API ---
    def stage_ratios(self) -> dict[str, float]:
        """完了ステージの「実測 / 見積り」比（履歴保存用）。"""
        with self._lock:
            out: dict[str, float] = {}
            for s in self._stages:
                if s.id in self._durations and s.estimate_s > 0:
                    out[s.id] = self._durations[s.id] / s.estimate_s
            return out

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            elapsed = (self._t_end if self._t_end is not None else now) - self._t0
            eta = None if self._finished else self._eta_locked(now)
            if self._finished:
                fraction = 1.0
            elif eta is None:
                fraction = self._max_fraction
            else:
                fraction = elapsed / max(elapsed + eta, 1e-6)
                fraction = min(max(fraction, self._max_fraction), 0.99)
            self._max_fraction = fraction
            cur = self._stages[self._current] if self._current >= 0 else None
            stages = []
            for i, s in enumerate(self._stages):
                if s.id in self._durations and i != self._current:
                    status = "done"
                elif i == self._current:
                    status = "active"
                else:
                    status = "pending"
                stages.append(
                    {
                        "id": s.id,
                        "label": s.label,
                        "status": status,
                        "seconds": round(self._durations[s.id], 1)
                        if status == "done"
                        else None,
                    }
                )
            return {
                "finished": self._finished,
                "stage_id": cur.id if cur else None,
                "stage_label": cur.label if cur else None,
                "stage_index": self._current + 1 if cur else None,
                "n_stages": len(self._stages),
                "detail": self._detail,
                "sub_done": self._sub_done,
                "sub_total": self._sub_total,
                "fraction": round(fraction, 4),
                "elapsed_s": round(elapsed, 1),
                "eta_s": None if eta is None else round(eta, 1),
                "stages": stages,
            }

    # --- 内部 ---
    def _close_current(self, now: float) -> None:
        if self._current >= 0:
            sid = self._stages[self._current].id
            self._durations[sid] = self._durations.get(sid, 0.0) + (now - self._stage_t0)

    def _calibrated(self, s: Stage) -> float:
        ratio = self._history.get(s.id)
        if ratio is None:
            return s.estimate_s
        lo, hi = _RATIO_CLAMP
        return s.estimate_s * min(max(ratio, lo), hi)

    def _eta_locked(self, now: float) -> float | None:
        if self._current < 0:
            return None
        elapsed = now - self._t0
        if elapsed < 2.0:
            return None
        cur = self._stages[self._current]
        stage_elapsed = now - self._stage_t0

        done_actual = sum(
            self._durations[s.id]
            for i, s in enumerate(self._stages)
            if i < self._current and s.id in self._durations
        )
        done_est = sum(
            self._calibrated(s)
            for i, s in enumerate(self._stages)
            if i < self._current and s.id in self._durations
        )
        factor = (done_actual + _PRIOR_SECONDS) / (done_est + _PRIOR_SECONDS)
        factor = min(max(factor, _FACTOR_CLAMP[0]), _FACTOR_CLAMP[1])

        cur_est = self._calibrated(cur) * factor
        # 見積りベース: 超過したら「経過に比例してまだかかる」とみなす
        est_rem = max(cur_est - stage_elapsed, 0.2 * stage_elapsed)
        done, total = self._sub_done or 0, self._sub_total or 0
        if total and done >= total:
            cur_rem = 0.0
        elif total and done:
            # 実測ペース: 数件だけだと偶然速い/遅い件に引っ張られるので、件数に応じて見積りから重みを移す
            n_after = done - self._sub_first_done
            if n_after > 0 and self._sub_first_t is not None and self._sub_last_t is not None:
                per_item = (self._sub_last_t - self._sub_first_t) / n_after
                since_last = now - self._sub_last_t
                rate_rem = max((total - done) * per_item - since_last, 0.1 * per_item)
            else:
                rate_rem = est_rem
            w = min(1.0, n_after / max(3.0, 0.15 * total))
            cur_rem = w * rate_rem + (1.0 - w) * est_rem
        else:
            cur_rem = est_rem

        fut_rem = factor * sum(
            self._calibrated(s) for s in self._stages[self._current + 1 :]
        )
        return max(cur_rem + fut_rem, 0.0)


def load_history(path: Path | None = None) -> dict[str, float]:
    p = path or HISTORY_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    ratios = data.get("stage_ratios") if isinstance(data, dict) else None
    if not isinstance(ratios, dict):
        return {}
    return {str(k): float(v) for k, v in ratios.items() if isinstance(v, (int, float))}


def save_history(ratios: dict[str, float], path: Path | None = None) -> None:
    """前回値と平均して保存（1 回の外れ値で次回の見積りが暴れないように）。"""
    if not ratios:
        return
    p = path or HISTORY_PATH
    merged = load_history(p)
    lo, hi = _RATIO_CLAMP
    for k, v in ratios.items():
        v = min(max(float(v), lo), hi)
        merged[k] = 0.5 * merged[k] + 0.5 * v if k in merged else v
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"stage_ratios": merged}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
