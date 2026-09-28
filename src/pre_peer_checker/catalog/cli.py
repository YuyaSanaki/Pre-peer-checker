"""照合カタログ拡充 CLI.

Examples::

    python -m pre_peer_checker.catalog pulse --csv fixtures/catalog/sample_rw_snippet.csv
    python -m pre_peer_checker.catalog pull-rw
    python -m pre_peer_checker.catalog export-yaml
    python -m pre_peer_checker.catalog sync-resources
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pre_peer_checker.catalog.paths import CATALOG_FIXTURES, RW_CACHE_DIR
from pre_peer_checker.catalog.propose import build_proposal, write_proposal
from pre_peer_checker.catalog.reason_mine import mine_reasons
from pre_peer_checker.catalog.rw_pull import RwPullError, ensure_rw_repo
from pre_peer_checker.catalog.store import (
    assert_resources_in_sync,
    export_verification_yaml,
    load_patterns,
    sync_resources,
)


def _cmd_pull_rw(args: argparse.Namespace) -> int:
    try:
        csv_path = ensure_rw_repo(Path(args.cache_dir) if args.cache_dir else None, pull=not args.no_pull)
    except RwPullError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(csv_path)
    return 0


def _cmd_mine(args: argparse.Namespace) -> int:
    csv_path = Path(args.csv) if args.csv else None
    if csv_path is None:
        try:
            csv_path = ensure_rw_repo(pull=False)
        except RwPullError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    report = mine_reasons(csv_path, life_only=not args.all_subjects)
    if args.out:
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(args.out)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _cmd_propose(args: argparse.Namespace) -> int:
    csv_path = Path(args.csv) if args.csv else None
    if csv_path is None and not args.skip_rw:
        try:
            csv_path = ensure_rw_repo(pull=False)
        except RwPullError:
            csv_path = CATALOG_FIXTURES / "sample_rw_snippet.csv"
            print(f"note: RW cache missing; using fixture {csv_path}", file=sys.stderr)
    mine = mine_reasons(csv_path) if csv_path else None
    proposal = build_proposal(rw_csv=csv_path, rw_mine=mine)
    out = Path(args.out or "outputs/catalog_proposals/latest.json")
    write_proposal(proposal, out)
    print(out)
    print(
        json.dumps(
            {
                "missing": proposal["actions"]["missing_pattern_ids_referenced_by_sources"],
                "planned_backed": len(
                    proposal["actions"]["planned_patterns_backed_by_open_sources"]
                ),
                "unmapped_top": proposal["actions"]["suggest_add_to_rw_reason_map"][:5],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_pulse(args: argparse.Namespace) -> int:
    """日次パルス: pull（任意）→ mine → propose → export-yaml → sync 確認."""
    csv_path: Path | None = Path(args.csv) if args.csv else None
    if csv_path is None:
        try:
            csv_path = ensure_rw_repo(
                Path(args.cache_dir) if args.cache_dir else None,
                pull=not args.no_pull,
            )
        except RwPullError as exc:
            if args.allow_fixture_fallback:
                csv_path = CATALOG_FIXTURES / "sample_rw_snippet.csv"
                print(f"warning: {exc}; fallback {csv_path}", file=sys.stderr)
            else:
                print(f"error: {exc}", file=sys.stderr)
                return 1
    mine = mine_reasons(csv_path)
    proposal = build_proposal(rw_mine=mine)
    out_dir = Path(args.out_dir or "outputs/catalog_proposals")
    stamp = proposal["generated_at"].replace(":", "").replace("+00:00", "Z")
    prop_path = write_proposal(proposal, out_dir / f"proposal_{stamp}.json")
    latest = write_proposal(proposal, out_dir / "latest.json")
    mine_path = out_dir / "rw_mine_latest.json"
    mine_path.write_text(json.dumps(mine, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    yaml_path = export_verification_yaml(load_patterns())
    sync_resources()
    assert_resources_in_sync()
    print(
        json.dumps(
            {
                "csv": str(csv_path),
                "proposal": str(prop_path),
                "latest": str(latest),
                "mine": str(mine_path),
                "yaml": str(yaml_path),
                "rows_life_science": mine.get("rows_life_science"),
                "unmapped_life_reasons": len(mine.get("unmapped_life_reasons") or []),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_export_yaml(args: argparse.Namespace) -> int:
    path = export_verification_yaml(out=Path(args.out) if args.out else None)
    print(path)
    return 0


def _cmd_sync_resources(_args: argparse.Namespace) -> int:
    sync_resources()
    assert_resources_in_sync()
    print("ok: fixtures ↔ resources synced")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pre-peer-checker-catalog",
        description="公式オープンソースから照合カタログを拡充する（提案のみ・自動 merge なし）",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pull = sub.add_parser("pull-rw", help="Crossref RW Git を cache/ に clone/pull")
    pull.add_argument("--cache-dir", default=str(RW_CACHE_DIR))
    pull.add_argument("--no-pull", action="store_true", help="既存 clone だけ使う")
    pull.set_defaults(func=_cmd_pull_rw)

    mine = sub.add_parser("mine", help="RW CSV の Reason タグを集計し pattern にマップ")
    mine.add_argument("--csv", default=None, help="retraction_watch.csv パス")
    mine.add_argument("--out", default=None, help="JSON 出力先")
    mine.add_argument(
        "--all-subjects",
        action="store_true",
        help="生命科学フィルタを外して全 Reason を出す",
    )
    mine.set_defaults(func=_cmd_mine)

    prop = sub.add_parser("propose", help="カタログ拡充提案 JSON を書く")
    prop.add_argument("--csv", default=None)
    prop.add_argument("--out", default=None)
    prop.add_argument("--skip-rw", action="store_true", help="RW なしで ORI/COPE 種のみ")
    prop.set_defaults(func=_cmd_propose)

    pulse = sub.add_parser("pulse", help="pull→mine→propose→export-yaml→sync（日次）")
    pulse.add_argument("--csv", default=None, help="指定時は pull せずこの CSV を使う")
    pulse.add_argument("--cache-dir", default=str(RW_CACHE_DIR))
    pulse.add_argument("--no-pull", action="store_true")
    pulse.add_argument("--out-dir", default="outputs/catalog_proposals")
    pulse.add_argument(
        "--allow-fixture-fallback",
        action="store_true",
        help="RW 取得失敗時に fixtures/catalog/sample_rw_snippet.csv を使う",
    )
    pulse.set_defaults(func=_cmd_pulse)

    ey = sub.add_parser("export-yaml", help="JSON 正本 → rules/verification_catalog.yaml")
    ey.add_argument("--out", default=None)
    ey.set_defaults(func=_cmd_export_yaml)

    syn = sub.add_parser("sync-resources", help="fixtures → resources 同期")
    syn.set_defaults(func=_cmd_sync_resources)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
