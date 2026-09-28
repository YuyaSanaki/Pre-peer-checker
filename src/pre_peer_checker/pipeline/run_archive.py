"""照合ごとの保存フォルダ（入力・照合条件・監査ハッシュ・レポート）.

``outputs/runs/<YYYYMMDD>_<HHMM>_<論文タイトル>/`` に 1 照合 = 1 フォルダで残す::

    input/      照合した manuscript/ ・ data/ の複製（APFS では clonefile で容量を食わない）
    condition/  照合設定 run_config.json ・ 照合カタログ ・ モデルレジストリ
    audit/      manifest.json（集約 SHA-256・実行環境・所要時間）・ SHA256SUMS ・ progress.json
    report/     report.html ・ warnings.json

``cd <run> && shasum -a 256 -c audit/SHA256SUMS`` で入力〜レポートの改変有無を検証できる。
同じ ``hashes.input`` を持つ照合は、バイト単位で同じ入力を照合したことを意味する。
"""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pre_peer_checker.catalog.paths import REPO_ROOT

if TYPE_CHECKING:
    from pre_peer_checker.pipeline.progress import ProgressTracker

DEFAULT_RUNS_DIR = REPO_ROOT / "outputs" / "runs"
MANIFEST_SCHEMA = "pre-peer-checker/run-archive@1"

INPUT_DIR = "input"
CONDITION_DIR = "condition"
AUDIT_DIR = "audit"
REPORT_DIR = "report"
_HASHED_DIRS = (INPUT_DIR, CONDITION_DIR, REPORT_DIR)

_MAX_TITLE_BYTES = 150
_SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
_CHUNK = 1024 * 1024


def _now_local() -> datetime:
    return datetime.now().astimezone()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 論文タイトル → フォルダ名
# ---------------------------------------------------------------------------
_GENERIC_TITLES = re.compile(
    r"^(microsoft word\s*-|untitled|manuscript|document\d*|main|draft)\b", re.IGNORECASE
)


def _clean_title(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", str(text)).strip()
    text = re.sub(r"^(title|running title|タイトル)\s*[:：]\s*", "", text, flags=re.IGNORECASE)
    return text.strip(" .")


def _usable_title(text: str | None) -> str | None:
    t = _clean_title(text)
    if len(t) < 4 or len(t) > 400:
        return None
    if _GENERIC_TITLES.match(t) or re.search(r"\.(docx?|pdf|tex)$", t, re.IGNORECASE):
        return None
    return t


def _title_from_docx(path: Path) -> str | None:
    from docx import Document

    doc = Document(str(path))
    t = _usable_title(doc.core_properties.title)
    if t:
        return t
    paragraphs = [p for p in doc.paragraphs if p.text.strip()]
    for p in paragraphs[:40]:
        style = (p.style.name if p.style is not None else "") or ""
        if style.lower().startswith("title"):
            t = _usable_title(p.text)
            if t:
                return t
    for p in paragraphs[:10]:
        t = _usable_title(p.text)
        if t and len(t) >= 15:
            return t
    return None


def _title_from_pdf(path: Path) -> str | None:
    import fitz

    doc = fitz.open(path)
    try:
        t = _usable_title((doc.metadata or {}).get("title"))
        if t:
            return t
        if doc.page_count == 0:
            return None
        spans: list[tuple[float, str]] = []
        for block in doc[0].get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    txt = (span.get("text") or "").strip()
                    if txt:
                        spans.append((round(float(span.get("size") or 0), 1), txt))
        if not spans:
            return None
        biggest = max(size for size, _ in spans)
        return _usable_title(" ".join(txt for size, txt in spans if size == biggest))
    finally:
        doc.close()


def _title_from_text(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8", errors="ignore")[:20000]
    if path.suffix.lower() == ".tex":
        m = re.search(r"\\title\s*(?:\[[^\]]*\])?\s*\{(.+?)\}\s*$", text, re.DOTALL | re.MULTILINE)
        if m:
            return _usable_title(re.sub(r"\\[a-zA-Z]+\*?|[{}]", " ", m.group(1)))
        return None
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    return _usable_title(m.group(1)) if m else None


_TITLE_READERS = (
    (".docx", _title_from_docx),
    (".pdf", _title_from_pdf),
    (".tex", _title_from_text),
    (".md", _title_from_text),
)


def guess_paper_title(manuscript_dir: Path | None, fallback: str) -> tuple[str, str]:
    """原稿からタイトルを推定し ``(title, source)`` を返す。読めなければ *fallback*。"""
    if manuscript_dir is not None and manuscript_dir.is_dir():
        files = sorted(
            (p for p in manuscript_dir.rglob("*") if p.is_file() and not _is_skipped(p)),
            key=lambda p: (len(p.relative_to(manuscript_dir).parts), p.name.lower()),
        )
        for suffix, reader in _TITLE_READERS:
            for p in files:
                if p.suffix.lower() != suffix:
                    continue
                try:
                    t = reader(p)
                except Exception:  # noqa: BLE001 — 壊れたファイルでも照合は止めない
                    t = None
                if t:
                    return t, f"{suffix.lstrip('.')}:{p.name}"
    return fallback, "case_root_name"


def safe_title(title: str) -> str:
    """フォルダ名に使えるタイトル（区切り文字除去・長さ制限）。"""
    t = re.sub(r"[\x00-\x1f\x7f]", "", title or "")
    t = re.sub(r'[/\\:*?"<>|]+', " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .")
    while len(t.encode("utf-8")) > _MAX_TITLE_BYTES:
        t = t[:-1]
    return t.rstrip(" .") or "untitled"


def run_folder_name(title: str, when: datetime) -> str:
    return f"{when:%Y%m%d}_{when:%H%M}_{safe_title(title)}"


# ---------------------------------------------------------------------------
# ハッシュ・複製
# ---------------------------------------------------------------------------
def _is_skipped(p: Path) -> bool:
    return p.name in _SKIP_NAMES or p.name.startswith("~$")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_clonefile() -> Any:
    if sys.platform != "darwin":
        return None
    try:
        fn = ctypes.CDLL(None, use_errno=True).clonefile
    except (OSError, AttributeError):
        return None
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
    fn.restype = ctypes.c_int
    return fn


_clonefile = _load_clonefile()


def _clone_or_copy(src: Path, dst: Path) -> None:
    """APFS 上では copy-on-write 複製（瞬時・追加容量なし）、それ以外は通常コピー。"""
    if _clonefile is not None and _clonefile(os.fsencode(src), os.fsencode(dst), 0) == 0:
        return
    shutil.copy2(src, dst)


def aggregate_hash(entries: list[dict[str, Any]]) -> str:
    """``SHA256SUMS`` 形式の行を path 順に連結した SHA-256（ファイル集合の指紋）。"""
    lines = "".join(f"{e['sha256']}  {e['path']}\n" for e in sorted(entries, key=lambda e: e["path"]))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def _hash_tree(run_dir: Path, sub: str) -> list[dict[str, Any]]:
    root = run_dir / sub
    if not root.is_dir():
        return []
    return [
        {"path": p.relative_to(run_dir).as_posix(), "sha256": sha256_file(p), "size": p.stat().st_size}
        for p in sorted(root.rglob("*"))
        if p.is_file()
    ]


def _git_info() -> dict[str, Any]:
    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    commit = git("rev-parse", "HEAD")
    if commit is None:
        return {}
    dirty = git("status", "--porcelain", "--untracked-files=no")
    return {"git_commit": commit, "git_dirty": bool(dirty)}


def _tool_info() -> dict[str, Any]:
    try:
        from importlib.metadata import version

        ver = version("pre-peer-checker")
    except Exception:  # noqa: BLE001
        ver = None
    return {
        "name": "pre-peer-checker",
        "version": ver,
        **_git_info(),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 照合フォルダ
# ---------------------------------------------------------------------------
@dataclass
class RunArchive:
    run_dir: Path
    title: str
    title_source: str
    case_root: Path | None
    started_at: datetime
    input_files: list[dict[str, Any]] = field(default_factory=list)
    _source_stats: dict[str, tuple[int, int, Path]] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.run_dir.name

    @property
    def input_dir(self) -> Path:
        return self.run_dir / INPUT_DIR

    @property
    def condition_dir(self) -> Path:
        return self.run_dir / CONDITION_DIR

    @property
    def audit_dir(self) -> Path:
        return self.run_dir / AUDIT_DIR

    @property
    def report_dir(self) -> Path:
        return self.run_dir / REPORT_DIR

    @property
    def manifest_path(self) -> Path:
        return self.audit_dir / "manifest.json"

    @classmethod
    def create(
        cls,
        runs_root: Path,
        *,
        case_root: Path | None,
        manuscript_dir: Path | None = None,
        title: str | None = None,
        when: datetime | None = None,
    ) -> RunArchive:
        when = when or _now_local()
        if title and title.strip():
            resolved_title, source = title.strip(), "user"
        else:
            fallback = case_root.name if case_root is not None else "untitled"
            resolved_title, source = guess_paper_title(manuscript_dir, fallback)
        runs_root.mkdir(parents=True, exist_ok=True)
        base = run_folder_name(resolved_title, when)
        run_dir = runs_root / base
        n = 2
        while True:
            try:
                run_dir.mkdir()
                break
            except FileExistsError:
                run_dir = runs_root / f"{base}_{n}"
                n += 1
        for sub in (INPUT_DIR, CONDITION_DIR, AUDIT_DIR, REPORT_DIR):
            (run_dir / sub).mkdir()
        archive = cls(
            run_dir=run_dir,
            title=resolved_title,
            title_source=source,
            case_root=case_root,
            started_at=when,
        )
        archive._write_manifest(status="running")
        return archive

    # --- input/ ---
    def snapshot_inputs(
        self, inputs: list[Path], *, progress: ProgressTracker | None = None
    ) -> None:
        """各入力フォルダ（またはファイル）を ``input/<名前>/`` に複製し SHA-256 を記録。"""
        plan: list[tuple[Path, Path]] = []
        for src_root in inputs:
            src_root = Path(src_root)
            dest_root = self.input_dir / src_root.name
            if src_root.is_file():
                plan.append((src_root, dest_root))
                continue
            for p in sorted(src_root.rglob("*")):
                if p.is_file() and not _is_skipped(p):
                    plan.append((p, dest_root / p.relative_to(src_root)))

        total = len(plan)
        for i, (src, dst) in enumerate(plan, 1):
            if progress is not None and (i == 1 or i % 20 == 0 or i == total):
                progress.update(detail=f"入力ファイルを保存・SHA-256 計算中（{i}/{total}）")
            dst.parent.mkdir(parents=True, exist_ok=True)
            st = src.stat()
            _clone_or_copy(src, dst)
            rel = dst.relative_to(self.run_dir).as_posix()
            self.input_files.append(
                {"path": rel, "sha256": sha256_file(dst), "size": st.st_size, "source": str(src)}
            )
            self._source_stats[rel] = (st.st_size, st.st_mtime_ns, src)
        self._write_manifest(status="running")

    def _changed_sources(self) -> list[str]:
        changed: list[str] = []
        for rel, (size, mtime_ns, src) in self._source_stats.items():
            try:
                st = src.stat()
            except OSError:
                changed.append(rel)
                continue
            if st.st_size != size or st.st_mtime_ns != mtime_ns:
                changed.append(rel)
        return changed

    # --- condition/ ---
    def snapshot_conditions(
        self, settings: dict[str, Any], *, patterns_path: Path | None
    ) -> Path | None:
        """照合設定と照合カタログを ``condition/`` へ保存し、照合に使うカタログのパスを返す。

        照合はこの複製を読むので、保存されたカタログ＝実際に使ったカタログになる。
        """
        _write_json(self.condition_dir / "run_config.json", settings)
        with contextlib.suppress(Exception):
            from pre_peer_checker.llm.registry import _registry_path

            reg = _registry_path()
            if reg.is_file():
                shutil.copy2(reg, self.condition_dir / reg.name)
        if patterns_path is None or not Path(patterns_path).is_file():
            return patterns_path
        dest = self.condition_dir / "pubpeer_patterns.json"
        shutil.copy2(patterns_path, dest)
        return dest

    def add_condition(self, name: str, data: Any) -> None:
        _write_json(self.condition_dir / name, data)

    # --- audit/ ---
    def finalize(
        self,
        *,
        ok: bool,
        error: str | None = None,
        n_warnings: int | None = None,
        progress: ProgressTracker | None = None,
    ) -> dict[str, Any]:
        finished = _now_local()
        if progress is not None:
            _write_json(self.audit_dir / "progress.json", progress.snapshot())
        sections = {
            INPUT_DIR: self.input_files,
            CONDITION_DIR: _hash_tree(self.run_dir, CONDITION_DIR),
            REPORT_DIR: _hash_tree(self.run_dir, REPORT_DIR),
        }
        lines = [f"{e['sha256']}  {e['path']}\n" for sub in _HASHED_DIRS for e in sections[sub]]
        (self.audit_dir / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")
        return self._write_manifest(
            status="ok" if ok else "failed",
            error=error,
            n_warnings=n_warnings,
            finished_at=finished,
            sections=sections,
            changed=self._changed_sources(),
        )

    def _write_manifest(
        self,
        *,
        status: str,
        error: str | None = None,
        n_warnings: int | None = None,
        finished_at: datetime | None = None,
        sections: dict[str, list[dict[str, Any]]] | None = None,
        changed: list[str] | None = None,
    ) -> dict[str, Any]:
        sections = sections or {INPUT_DIR: self.input_files}
        manifest: dict[str, Any] = {
            "schema": MANIFEST_SCHEMA,
            "run_name": self.name,
            "title": self.title,
            "title_source": self.title_source,
            "case_root": str(self.case_root) if self.case_root else None,
            "status": status,
            "error": error,
            "n_warnings": n_warnings,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(finished_at) if finished_at else None,
            "elapsed_s": round((finished_at - self.started_at).total_seconds(), 1)
            if finished_at
            else None,
            "hashes": {
                sub: aggregate_hash(entries) for sub, entries in sections.items() if entries
            },
            "n_input_files": len(self.input_files),
            "input_bytes": sum(int(e["size"]) for e in self.input_files),
            "inputs_changed_during_run": changed or [],
            "tool": _tool_info(),
            "input_files": self.input_files,
        }
        _write_json(self.manifest_path, manifest)
        return manifest


# ---------------------------------------------------------------------------
# 履歴一覧
# ---------------------------------------------------------------------------
def resolve_run_dir(runs_root: Path, name: str) -> Path | None:
    """``runs_root`` 直下の照合フォルダだけを返す（パス走査を拒否）。"""
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        return None
    root = runs_root.resolve()
    p = (root / name).resolve()
    if p.parent != root or not p.is_dir():
        return None
    return p


def list_runs(runs_root: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    """保存済み照合（新しい順）。manifest の大きな一覧は省く。"""
    if not runs_root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for d in runs_root.iterdir():
        mf = d / AUDIT_DIR / "manifest.json"
        if not mf.is_file():
            continue
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        data.pop("input_files", None)
        data["run_name"] = d.name
        data["run_dir"] = str(d.resolve())
        data["has_report"] = (d / REPORT_DIR / "report.html").is_file()
        rows.append(data)
    rows.sort(key=lambda r: (str(r.get("started_at") or ""), r["run_name"]), reverse=True)
    return rows[:limit] if limit else rows
