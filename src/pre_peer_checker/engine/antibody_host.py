"""抗体の宿主動物種の記載矛盾 — P-ANTIBODY-HOST-MISMATCH.

原稿内だけで閉じる照合:
- 品番体系が宿主を決める抗体（Atlas HPA＝ウサギ等）の宿主記載が違う
- 二次抗体の標的動物種に対応する一次抗体の宿主が同じ記載ブロックに無い
- 同じ品番・RRID の抗体が箇所によって別の宿主で書かれている
- 1 つの抗体記載の中で宿主が食い違う（``rabbit anti-X (mouse monoclonal)``）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pre_peer_checker.warnings import WarningItem, WarningTag

_SPECIES: dict[str, str] = {
    "mouse": "mouse",
    "mice": "mouse",
    "murine": "mouse",
    "rabbit": "rabbit",
    "lapine": "rabbit",
    "rat": "rat",
    "goat": "goat",
    "caprine": "goat",
    "donkey": "donkey",
    "sheep": "sheep",
    "ovine": "sheep",
    "chicken": "chicken",
    "guinea pig": "guinea pig",
    "guinea-pig": "guinea pig",
    "hamster": "hamster",
    "horse": "horse",
    "equine": "horse",
    "dog": "dog",
    "canine": "dog",
    "bovine": "cow",
    "cow": "cow",
    "pig": "pig",
    "porcine": "pig",
    "swine": "pig",
    "llama": "llama",
    "alpaca": "alpaca",
    "human": "human",
}
_SP = r"(?:guinea[\s-]pig|" + "|".join(
    sorted((k for k in _SPECIES if "guinea" not in k), key=len, reverse=True)
) + r")"
_DASH = "-\u2010\u2011\u2012\u2013\u2014"

_ANTI_RE = re.compile(rf"\banti[{_DASH}](?P<target>(?:guinea[\s-]pig)|[^\s,;:()\[\]|]+)", re.IGNORECASE)
_PREFIX_HOST_RE = re.compile(
    rf"\b(?P<host>{_SP})\s+(?:(?:poly|mono)clonal\s+|(?:IgG\w*|IgM|IgY)\s+)?$", re.IGNORECASE
)
_POSTFIX_HOST_RES = [
    re.compile(rf"\(\s*(?P<host>{_SP})\s*(?:,\s*)?(?:poly|mono)clonal", re.IGNORECASE),
    re.compile(rf"\b(?P<host>{_SP})\s+(?:(?:poly|mono)clonal|mAb|pAb)\b", re.IGNORECASE),
    re.compile(rf"\b(?:raised|produced|generated|made)\s+in\s+(?:an?\s+)?(?P<host>{_SP})", re.IGNORECASE),
    re.compile(rf"\bderived\s+from\s+(?:an?\s+)?(?P<host>{_SP})\b", re.IGNORECASE),
    re.compile(rf"\bhost(?:\s+species)?\s*[:=]?\s*(?P<host>{_SP})\b", re.IGNORECASE),
    re.compile(rf"\b(?P<host>{_SP})\s+origin\b", re.IGNORECASE),
]
_STANDALONE_RE = re.compile(
    rf"\b(?P<host>{_SP})\s+(?:(?:poly|mono)clonal(?:\s+(?:IgG\w*|antibod(?:y|ies)))?|mAb|pAb)\b",
    re.IGNORECASE,
)
# Words after ``anti-<species>`` that make it a secondary antibody (not an antigen name).
_SECONDARY_NEXT_RE = re.compile(
    r"^(?:$|[,;:)\]|.]|\(|"
    r"(?:Ig[GMAEY]\w*|Ig|F\(ab|H\s*\+\s*L|secondar\w*|antibod\w*|antisera|serum|"
    r"Alexa|AF\d+|HRP|AP|biotin\w*|DyLight\w*|Cy\d\w*|FITC|TRITC|PE|APC|BV\d+|"
    r"conjugat\w*|labell?ed|whole|and|or|as|at|for|from|was|were|is|are|in)\b)",
    re.IGNORECASE,
)
_NOT_ANTIBODY_TARGET_RE = re.compile(
    r"^(?:inflammat|apopto|oxida|cancer|tumou?r|vir|bacteri|fung|diabet|sense|parallel|"
    r"correlat|clockwise|ag[e]?ing|obes|hypertens|coagul|thromb|microb|proliferat|angiogen|"
    r"depress|psychot|epilep|convuls|emetic|malari|histamin|cholinerg|adrenerg|aliasing|"
    r"fade|reflect|freez|foam|static|log|pod|node|symmetr)",
    re.IGNORECASE,
)
_CLAUSE_END_RE = re.compile(r"(?<!\d)\.\s+(?=[A-Z])|;|\|\s*\|")
_WINDOW = 200
_CAT_WINDOW = 110
_BLOCK_LINE_MAX = 160

_CATALOG_HOST_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\bHPA\d{6}\b"), "rabbit", "Atlas Antibodies（Sigma Prestige Antibodies）の HPA 品番（ウサギポリクローナル）"),
    (re.compile(r"\bAMAb\d{5}\b"), "mouse", "Atlas Antibodies の AMAb 品番（マウスモノクローナル）"),
    (
        re.compile(r"\(\s*[A-Z0-9][A-Za-z0-9.]{1,9}\s*\)\s*XP\b"),
        "rabbit",
        "Cell Signaling Technology の XP® 製品（ウサギモノクローナル）",
    ),
]
_ID_RES = [
    re.compile(r"\bRRID:?\s*(AB_\d+)", re.IGNORECASE),
    re.compile(r"\b(HPA\d{6}|AMAb\d{5})\b"),
    re.compile(r"\b(ab\d{3,7})\b"),
    re.compile(r"\b(sc-\d{2,7})\b", re.IGNORECASE),
    re.compile(r"\bCat(?:alog(?:ue)?)?\.?\s*(?:#|No\.?|number)?\s*:?\s*([A-Za-z]{0,4}-?\d[\w-]{2,})", re.IGNORECASE),
]


@dataclass
class AntibodyMention:
    text: str
    target: str
    secondary: bool
    host: str | None
    host_raw: str = ""
    postfix_host: str | None = None
    catalog_host: tuple[str, str, str] | None = None  # (host, matched id, rule note)
    identity: str | None = None
    block: int = 0


@dataclass
class _Block:
    text: str
    mentions: list[AntibodyMention] = field(default_factory=list)


def _canon(word: str | None) -> str | None:
    if not word:
        return None
    w = re.sub(r"[\s-]+", " ", word.strip().lower())
    return _SPECIES.get(w) or _SPECIES.get(w.replace(" ", "-"))


def _blocks(paragraphs: list[str]) -> list[str]:
    """Runs of short lines (key resources table rows) become one block."""
    out: list[str] = []
    run: list[str] = []
    for p in paragraphs:
        s = (p or "").strip()
        if not s:
            continue
        if len(s) <= _BLOCK_LINE_MAX:
            run.append(s)
            continue
        if run:
            out.append(" | ".join(run))
            run = []
        out.append(s)
    if run:
        out.append(" | ".join(run))
    return out


def _clause_end(text: str, start: int, limit: int) -> int:
    m = _CLAUSE_END_RE.search(text, start, min(len(text), limit))
    return m.start() if m else min(len(text), limit)


def _catalog_host(window: str) -> tuple[str, str, str] | None:
    for pat, host, note in _CATALOG_HOST_RULES:
        m = pat.search(window)
        if m:
            return host, m.group(0).strip(), note
    return None


def _identity(window: str) -> str | None:
    for pat in _ID_RES:
        m = pat.search(window)
        if m:
            return re.sub(r"[\s-]", "", m.group(1)).upper()
    return None


def extract_antibody_mentions(text: str, block: int = 0) -> list[AntibodyMention]:
    """Antibody descriptions in one paragraph / table block."""
    hits = [
        m for m in _ANTI_RE.finditer(text)
        if not _NOT_ANTIBODY_TARGET_RE.match(m.group("target"))
    ]
    prefixes = [_PREFIX_HOST_RE.search(text[max(0, m.start() - 40) : m.start()]) for m in hits]
    starts = [
        m.start() - (pm.end() - pm.start() if pm else 0) for m, pm in zip(hits, prefixes, strict=True)
    ]
    out: list[AntibodyMention] = []
    spans: list[tuple[int, int]] = []
    for i, m in enumerate(hits):
        nxt = starts[i + 1] if i + 1 < len(hits) else len(text)
        end = max(m.end(), min(nxt, _clause_end(text, m.end(), m.end() + _WINDOW)))
        post = text[m.end() : end]
        target = m.group("target")
        head, _, tail = target.partition("-")
        target_sp = _canon(target) or _canon(head)
        rest = post.lstrip() if _canon(target) else (tail + post).lstrip()
        secondary = bool(
            target_sp and target_sp != "human" and _SECONDARY_NEXT_RE.match(rest)
        )
        pm = prefixes[i]
        prefix = _canon(pm.group("host")) if pm else None
        postfix = None
        if not secondary:
            for pat in _POSTFIX_HOST_RES:
                hm = pat.search(post)
                if hm:
                    postfix = _canon(hm.group("host"))
                    break
        host = prefix or postfix
        cat_win = text[m.start() : min(end, m.end() + _CAT_WINDOW)]
        mention = AntibodyMention(
            text=text[starts[i] : end].strip()[:180],
            target=target_sp if secondary else target,
            secondary=secondary,
            host=host,
            host_raw=(pm.group("host") if pm else "") or "",
            postfix_host=postfix if prefix else None,
            catalog_host=None if secondary else _catalog_host(cat_win),
            identity=_identity(cat_win),
            block=block,
        )
        out.append(mention)
        spans.append((m.start(), end))
    for sm in _STANDALONE_RE.finditer(text):
        if any(a - 40 <= sm.start() < b for a, b in spans):
            continue
        host = _canon(sm.group("host"))
        win = text[max(0, sm.start() - 40) : sm.end() + _CAT_WINDOW]
        out.append(
            AntibodyMention(
                text=text[max(0, sm.start() - 40) : sm.end() + 20].strip(),
                target="",
                secondary=False,
                host=host,
                catalog_host=_catalog_host(win),
                identity=_identity(text[sm.end() : sm.end() + _CAT_WINDOW]),
                block=block,
            )
        )
    return out


def _warn(title: str, location: str, reason: str, check: str, **meta) -> WarningItem:
    return WarningItem(
        tag=WarningTag.CONFIG_MISMATCH,
        title=title,
        location=location,
        reason=reason,
        sources=list(meta.pop("sources", []) or []),
        metadata={"pattern_id": "P-ANTIBODY-HOST-MISMATCH", "check": check, **meta},
    )


def _snip(s: str, n: int = 90) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def warnings_from_antibody_hosts(
    paragraphs: list[str], *, sources: list[str] | None = None
) -> list[WarningItem]:
    """Host-species contradictions among the antibodies described in the manuscript."""
    seen_text: set[str] = set()
    uniq: list[str] = []
    for p in paragraphs:
        key = " ".join((p or "").split())
        if key and key not in seen_text:
            seen_text.add(key)
            uniq.append(p)
    blocks = [_Block(text=t) for t in _blocks(uniq)]
    for i, b in enumerate(blocks):
        b.mentions = extract_antibody_mentions(b.text, block=i)
    all_m = [m for b in blocks for m in b.mentions]
    src = list(sources or [])
    warnings: list[WarningItem] = []
    emitted: set[tuple] = set()

    # 1. catalog numbering fixes the host
    for m in all_m:
        if m.secondary or not m.host or not m.catalog_host:
            continue
        cat_host, cat_id, note = m.catalog_host
        if cat_host == m.host:
            continue
        key = ("catalog", cat_id, m.host)
        if key in emitted:
            continue
        emitted.add(key)
        warnings.append(
            _warn(
                "抗体の宿主動物種が品番と矛盾",
                f"抗体記載: 「{_snip(m.text)}」",
                f"「{cat_id}」は{note}で宿主は {cat_host} ですが、"
                f"本文では宿主が {m.host} と書かれています。"
                "宿主の記載ミス、または品番の転記ミスの可能性があります。",
                "catalog_host",
                stated_host=m.host,
                catalog_host=cat_host,
                catalog_id=cat_id,
                span=m.text,
                sources=src,
            )
        )

    # 2. prefix vs postfix host in one description
    for m in all_m:
        if m.postfix_host and m.host and m.postfix_host != m.host:
            key = ("inline", m.text)
            if key in emitted:
                continue
            emitted.add(key)
            warnings.append(
                _warn(
                    "1 つの抗体記載の中で宿主動物種が食い違う",
                    f"抗体記載: 「{_snip(m.text)}」",
                    f"同じ抗体の記載に {m.host} と {m.postfix_host} の両方が宿主として書かれています。",
                    "inline_conflict",
                    hosts=[m.host, m.postfix_host],
                    span=m.text,
                    sources=src,
                )
            )

    # 3. same catalog / RRID, different host
    by_id: dict[str, list[AntibodyMention]] = {}
    for m in all_m:
        if m.identity and m.host and not m.secondary:
            by_id.setdefault(m.identity, []).append(m)
    for ident, ms in by_id.items():
        hosts = sorted({m.host for m in ms if m.host})
        if len(hosts) < 2:
            continue
        warnings.append(
            _warn(
                "同じ抗体（品番・RRID）の宿主動物種が箇所によって違う",
                f"{ident}: {' / '.join(hosts)}",
                "同じ品番・RRID の抗体が、"
                + "、".join(f"「{_snip(m.text, 60)}」" for m in ms[:3])
                + " のように異なる宿主で記載されています。",
                "identity_conflict",
                identity=ident,
                hosts=hosts,
                spans=[m.text for m in ms],
                sources=src,
            )
        )

    # 4. secondary raised in the species it targets
    for m in all_m:
        if m.secondary and m.host and m.host == m.target:
            key = ("self", m.text)
            if key in emitted:
                continue
            emitted.add(key)
            warnings.append(
                _warn(
                    "二次抗体の宿主と標的動物種が同じ",
                    f"抗体記載: 「{_snip(m.text)}」",
                    f"{m.host} で作られた anti-{m.target} 二次抗体という記載になっています。"
                    "宿主または標的動物種の記載ミスの可能性があります。",
                    "secondary_self",
                    host=m.host,
                    target=m.target,
                    span=m.text,
                    sources=src,
                )
            )

    # 5. secondary antibody without a primary from that species
    def unmatched(ms: list[AntibodyMention]) -> tuple[list[AntibodyMention], set[str], set[str]] | None:
        prim = [m for m in ms if not m.secondary]
        sec = [m for m in ms if m.secondary]
        if not prim or not sec or any(m.host is None for m in prim):
            return None
        hosts = {m.host for m in prim if m.host}
        lone = [m for m in sec if m.target not in hosts]
        targets = {m.target for m in sec}
        return lone, hosts, {h for h in hosts if h not in targets}

    flagged_targets: set[str] = set()
    for b in blocks:
        res = unmatched(b.mentions)
        if not res or not res[0]:
            continue
        lone, hosts, orphan_hosts = res
        for t in sorted({m.target for m in lone}):
            flagged_targets.add(t)
            example = next(m for m in lone if m.target == t)
            reason = (
                f"二次抗体 anti-{t} がありますが、同じ記載ブロックの一次抗体の宿主は "
                f"{', '.join(sorted(hosts))} だけで、{t} の一次抗体がありません。"
            )
            if orphan_hosts:
                reason += (
                    f"逆に {', '.join(sorted(orphan_hosts))} の一次抗体には対応する二次抗体がありません。"
                )
            reason += "一次抗体の宿主の記載ミス、または二次抗体の取り違えの可能性があります。"
            warnings.append(
                _warn(
                    "二次抗体の標的動物種に対応する一次抗体がない",
                    f"抗体記載ブロック: 「{_snip(b.text, 110)}」",
                    reason,
                    "secondary_unmatched",
                    secondary_target=t,
                    primary_hosts=sorted(hosts),
                    primary_hosts_without_secondary=sorted(orphan_hosts),
                    span=example.text,
                    scope="block",
                    sources=src,
                )
            )

    res = unmatched(all_m)
    if res:
        lone, hosts, orphan_hosts = res
        for t in sorted({m.target for m in lone} - flagged_targets):
            example = next(m for m in lone if m.target == t)
            warnings.append(
                _warn(
                    "二次抗体の標的動物種に対応する一次抗体がない",
                    f"抗体記載: 「{_snip(example.text)}」",
                    f"二次抗体 anti-{t} がありますが、原稿中の一次抗体の宿主は "
                    f"{', '.join(sorted(hosts))} だけです。"
                    "一次抗体の宿主の記載ミス、二次抗体の取り違え、または一次抗体の記載漏れの可能性があります。",
                    "secondary_unmatched",
                    secondary_target=t,
                    primary_hosts=sorted(hosts),
                    primary_hosts_without_secondary=sorted(orphan_hosts),
                    span=example.text,
                    scope="document",
                    needs_review=True,
                    sources=src,
                )
            )
    return warnings


def antibody_mentions_artifact(paragraphs: list[str]) -> list[dict]:
    out: list[dict] = []
    for i, t in enumerate(_blocks(paragraphs)):
        for m in extract_antibody_mentions(t, block=i):
            out.append(
                {
                    "text": m.text,
                    "target": m.target,
                    "secondary": m.secondary,
                    "host": m.host,
                    "catalog_host": m.catalog_host[0] if m.catalog_host else None,
                    "identity": m.identity,
                    "block": m.block,
                }
            )
    return out
