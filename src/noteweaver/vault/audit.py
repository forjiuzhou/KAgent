"""Vault health audit, metrics, and tag-similarity helpers."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from noteweaver.vault.core import Vault


def stats(vault: Vault) -> dict:
    """Return vault statistics."""
    return {
        "concepts": len(vault.list_files("wiki/concepts")),
        "journals": len(vault.list_files("wiki/journals")),
        "synthesis": len(vault.list_files("wiki/synthesis")),
        "sources": len(vault.list_files("sources")),
    }


def health_metrics(vault: Vault) -> dict:
    """Compute quantitative health metrics for the knowledge base."""
    from noteweaver.frontmatter import page_summary_from_file

    all_pages = []
    all_content = {}
    no_frontmatter_count = 0
    for rel_path in vault.list_files("wiki"):
        if rel_path in ("wiki/index.md", "wiki/log.md"):
            continue
        if "/archive/" in rel_path:
            continue
        try:
            content = vault.read_file(rel_path)
            ps = page_summary_from_file(rel_path, content)
            all_pages.append({"path": rel_path, "ps": ps, "content": content})
            all_content[rel_path] = content
            if ps is None:
                no_frontmatter_count += 1
        except (FileNotFoundError, PermissionError):
            continue

    total = len(all_pages)
    if total == 0:
        return {"total_pages": 0}

    hubs = [p for p in all_pages if p["ps"] and p["ps"].type == "hub"]
    canonicals = [p for p in all_pages if p["ps"] and p["ps"].type == "canonical"]
    canonicals_with_sources = [
        c for c in canonicals if c["ps"].sources
    ]

    page_titles = {p["ps"].title for p in all_pages if p["ps"] and p["ps"].title}
    orphans = [
        p for p in all_pages
        if p["ps"] and p["ps"].title
        and vault.backlinks.reference_count(p["ps"].title) == 0
        and p["ps"].type not in ("hub", "journal")
    ]

    no_summary = [p for p in all_pages if p["ps"] and not p["ps"].summary]

    link_stats = vault.backlinks.stats()
    metrics = {
        "total_pages": total,
        "hubs": len(hubs),
        "canonicals": len(canonicals),
        "canonical_source_ratio": (
            f"{len(canonicals_with_sources)}/{len(canonicals)}"
            if canonicals else "n/a"
        ),
        "orphan_pages": len(orphans),
        "orphan_rate": f"{len(orphans)}/{total}" if total else "n/a",
        "pages_without_summary": len(no_summary),
        "missing_frontmatter": no_frontmatter_count,
        "hub_coverage": (
            f"{len(hubs)} hubs for {total - len(hubs)} content pages"
        ),
        "total_links": link_stats["total_links"],
        "avg_links_per_page": round(link_stats["total_links"] / total, 1) if total else 0,
    }

    source_count = len(vault.list_files("sources"))
    if source_count:
        metrics["source_files"] = source_count

    return metrics


def audit_vault(vault: Vault) -> dict:
    """Full vault health audit. Pure code, no LLM.

    Scans frontmatter and content to produce a structured findings
    report.  Each finding category is a list of dicts with enough
    detail for an LLM or CLI to act on.
    """
    from noteweaver.frontmatter import extract_frontmatter

    all_pages: list[dict] = []
    missing_frontmatter: list[str] = []
    for rel_path in vault.list_files("wiki"):
        if rel_path in ("wiki/index.md", "wiki/log.md"):
            continue
        if "/archive/" in rel_path:
            continue
        try:
            content = vault.read_file(rel_path)
            fm = extract_frontmatter(content)
            if fm is None:
                missing_frontmatter.append(rel_path)
                continue
            all_pages.append({
                "path": rel_path,
                "fm": fm,
                "content": content,
            })
        except (FileNotFoundError, PermissionError):
            continue

    if not all_pages and not missing_frontmatter:
        return {"summary": "0 issues found (vault is empty)"}

    bl_pages = [{"path": p["path"], "content": p["content"]} for p in all_pages]
    vault.backlinks.rebuild(bl_pages)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stale_imports: list[dict] = []
    hub_candidates: list[dict] = []
    orphan_pages: list[str] = []
    missing_summaries: list[str] = []
    broken_links: list[dict] = []
    missing_connections: list[dict] = []
    similar_tags: list[dict] = []

    titles_to_path: dict[str, str] = {}
    hubs: set[str] = set()
    hub_tags: set[str] = set()
    tag_pages: dict[str, list[str]] = {}

    for p in all_pages:
        fm = p["fm"]
        title = fm.get("title", "")
        ptype = fm.get("type", "")
        path = p["path"]
        if title:
            titles_to_path[title] = path
        if ptype == "hub":
            hubs.add(title)
            for t in (fm.get("tags") or []):
                hub_tags.add(str(t).lower())
        for t in (fm.get("tags") or []):
            tag_pages.setdefault(str(t), []).append(path)

    for p in all_pages:
        fm = p["fm"]
        tags = fm.get("tags") or []
        if "imported" not in tags:
            continue
        updated = str(fm.get("updated", ""))
        days = _days_since(updated, today)
        if days is not None and days > 7:
            stale_imports.append({
                "path": p["path"],
                "days_since_update": days,
            })

    for tag, pages in tag_pages.items():
        if tag in ("imported", "journal", "pinned"):
            continue
        if len(pages) >= 3 and str(tag).lower() not in hub_tags:
            hub_candidates.append({
                "tag": tag,
                "page_count": len(pages),
                "pages": pages[:5],
            })

    for p in all_pages:
        fm = p["fm"]
        title = fm.get("title", "")
        ptype = fm.get("type", "")
        if ptype in ("hub", "journal"):
            continue
        if title and vault.backlinks.reference_count(title) == 0:
            orphan_pages.append(p["path"])

    for p in all_pages:
        fm = p["fm"]
        summary = fm.get("summary", "")
        if not summary or summary.startswith("Imported from"):
            missing_summaries.append(p["path"])

    from noteweaver.backlinks import WIKILINK_PATTERN
    for p in all_pages:
        links = WIKILINK_PATTERN.findall(p["content"])
        for link_title in set(links):
            if link_title not in titles_to_path:
                broken_links.append({
                    "page": p["path"],
                    "link_title": link_title,
                })

    paths_by_tag: dict[str, set[str]] = {}
    for p in all_pages:
        fm = p["fm"]
        for t in (fm.get("tags") or []):
            paths_by_tag.setdefault(t, set()).add(p["path"])
    checked_pairs: set[tuple[str, str]] = set()
    for tag, paths in paths_by_tag.items():
        if len(paths) > 20:
            continue
        path_list = sorted(paths)
        for i, pa in enumerate(path_list):
            for pb in path_list[i + 1:]:
                pair = (pa, pb)
                if pair in checked_pairs:
                    continue
                checked_pairs.add(pair)
                shared = [
                    t for t in (tag_pages.keys())
                    if pa in tag_pages.get(t, []) and pb in tag_pages.get(t, [])
                ]
                if len(shared) < 2:
                    continue
                outlinks_a = set(vault.backlinks.outlinks_for(pa))
                outlinks_b = set(vault.backlinks.outlinks_for(pb))
                title_a = next(
                    (p["fm"].get("title", "") for p in all_pages if p["path"] == pa), ""
                )
                title_b = next(
                    (p["fm"].get("title", "") for p in all_pages if p["path"] == pb), ""
                )
                if title_b not in outlinks_a and title_a not in outlinks_b:
                    missing_connections.append({
                        "page_a": pa,
                        "page_b": pb,
                        "shared_tags": shared[:5],
                    })

    all_tags = sorted(tag_pages.keys())
    checked_tag_pairs: set[tuple[str, str]] = set()
    for i, ta in enumerate(all_tags):
        for tb in all_tags[i + 1:]:
            pair = (ta, tb)
            if pair in checked_tag_pairs:
                continue
            checked_tag_pairs.add(pair)
            reason = similar_tag_reason(ta, tb)
            if reason:
                similar_tags.append({
                    "tag_a": ta, "tag_b": tb, "reason": reason,
                })

    counts = []
    if missing_frontmatter:
        counts.append(f"{len(missing_frontmatter)} missing frontmatter")
    if stale_imports:
        counts.append(f"{len(stale_imports)} stale import(s)")
    if hub_candidates:
        counts.append(f"{len(hub_candidates)} hub candidate(s)")
    if orphan_pages:
        counts.append(f"{len(orphan_pages)} orphan page(s)")
    if missing_summaries:
        counts.append(f"{len(missing_summaries)} missing summary(ies)")
    if broken_links:
        counts.append(f"{len(broken_links)} broken link(s)")
    if missing_connections:
        counts.append(f"{len(missing_connections)} missing connection(s)")
    if similar_tags:
        counts.append(f"{len(similar_tags)} similar tag pair(s)")

    total = sum([
        len(missing_frontmatter), len(stale_imports), len(hub_candidates),
        len(orphan_pages), len(missing_summaries), len(broken_links),
        len(missing_connections), len(similar_tags),
    ])
    summary = (
        f"{total} issue(s) found: {', '.join(counts)}"
        if counts else "0 issues found"
    )

    return {
        "missing_frontmatter": missing_frontmatter,
        "stale_imports": stale_imports,
        "hub_candidates": hub_candidates,
        "orphan_pages": orphan_pages,
        "missing_summaries": missing_summaries,
        "broken_links": broken_links,
        "missing_connections": missing_connections,
        "similar_tags": similar_tags,
        "summary": summary,
    }


def save_audit_report(vault: Vault, report: dict) -> Path:
    """Persist an audit report to ``.meta/audit-report.json``."""
    import json as _json
    path = vault.meta_dir / "audit-report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


# ---- Tag similarity helpers ----

def similar_tag_reason(ta: str, tb: str) -> str | None:
    """Return the reason two tags are similar, or None."""
    if ta == tb:
        return None

    ta_nohyp = ta.replace("-", "")
    tb_nohyp = tb.replace("-", "")
    if ta_nohyp == tb_nohyp:
        return "hyphen variant"

    if _is_plural_pair(ta, tb):
        return "plural"

    if ta in tb or tb in ta:
        if len(ta) >= 2 and len(tb) >= 2:
            return "substring"

    if len(ta) > 3 and len(tb) > 3:
        dist = _edit_distance(ta, tb)
        if dist <= 2:
            return f"edit distance {dist}"

    return None


def _is_plural_pair(a: str, b: str) -> bool:
    if a == b:
        return False
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if long == short + "s":
        return True
    if long == short + "es":
        return True
    if short.endswith("y") and long == short[:-1] + "ies":
        return True
    return False


def _edit_distance(a: str, b: str) -> int:
    if len(a) < len(b):
        return _edit_distance(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(
                prev[j + 1] + 1,
                curr[j] + 1,
                prev[j] + (0 if ca == cb else 1),
            ))
        prev = curr
    return prev[-1]


def _days_since(date_str: str, today_str: str) -> int | None:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        t = datetime.strptime(today_str, "%Y-%m-%d")
        return (t - d).days
    except (ValueError, TypeError):
        return None
