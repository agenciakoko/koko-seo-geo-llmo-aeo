#!/usr/bin/env python3
"""Crawl a small set of internal URLs and summarize sitewide SEO health signals."""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque

from lite_audit_utils import (
    ai_citation_readiness,
    check_canonical,
    fetch_llms_txt_status,
    fetch_robots_ai_status,
    fetch_snapshot,
    normalize_internal_links,
    probe_staging_hosts,
    sitemap_profile,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a lightweight crawl over a small set of internal pages.",
    )
    parser.add_argument("url", help="Root URL to crawl")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=5,
        help="Maximum number of internal pages to include",
    )
    parser.add_argument(
        "--skip-staging",
        action="store_true",
        help="Do not probe hlg./dev./stg. hostnames for open staging copies",
    )
    args = parser.parse_args()

    max_pages = max(2, min(args.max_pages, 12))
    queue = deque([args.url])
    visited: set[str] = set()
    pages = []
    llms_status = fetch_llms_txt_status(args.url)
    robots_status = fetch_robots_ai_status(args.url)
    sitemap = sitemap_profile(args.url)
    staging = [] if args.skip_staging else probe_staging_hosts(args.url)

    while queue and len(pages) < max_pages:
        url = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        try:
            snapshot = fetch_snapshot(url)
        except Exception as exc:  # pragma: no cover - network behavior
            pages.append(
                {
                    "url": url,
                    "status_code": 0,
                    "error": str(exc),
                }
            )
            continue

        pages.append(
            {
                "url": snapshot.final_url,
                "status_code": snapshot.status_code,
                "page_type": snapshot.page_type,
                "title": snapshot.title,
                "word_count": snapshot.word_count,
                "h1_count": snapshot.h1_count,
                "meta_description_present": bool(snapshot.meta_description),
                "schema_count": snapshot.schema_count,
                "images": {
                    "total": snapshot.image_count,
                    "missing_alt": snapshot.images_missing_alt,
                    "empty_alt": snapshot.images_empty_alt,
                },
                "robots_directives": snapshot.robots_directives,
                "bot_challenge": snapshot.bot_challenge,
                "canonical_check": check_canonical(snapshot.final_url, snapshot.canonical)
                if snapshot.status_code == 200
                else {},
                "structured_data_invalid_urls": snapshot.invalid_ld_urls,
                "ai_citation_readiness": ai_citation_readiness(snapshot, llms_status, robots_status),
                "top_terms": snapshot.top_terms[:8],
            }
        )

        for link in normalize_internal_links(snapshot.final_url, snapshot.links):
            if link not in visited and link not in queue and len(visited) + len(queue) < 50:
                queue.append(link)

    broken_pages = [page["url"] for page in pages if page.get("status_code", 0) >= 400 or page.get("status_code") == 0]
    thin_pages = [page["url"] for page in pages if page.get("word_count", 0) and page.get("word_count", 0) < 250]
    missing_meta = [page["url"] for page in pages if page.get("status_code") == 200 and not page.get("meta_description_present")]
    weak_structure = [page["url"] for page in pages if page.get("status_code") == 200 and page.get("h1_count", 0) != 1]
    noindex_pages = [
        page["url"]
        for page in pages
        if any(d.endswith(("noindex", "none")) for d in page.get("robots_directives") or [])
    ]
    missing_alt_pages = [page["url"] for page in pages if page.get("images", {}).get("missing_alt")]
    weak_ai_readiness = [
        page["url"]
        for page in pages
        if page.get("status_code") == 200 and page.get("ai_citation_readiness", {}).get("score", 0) < 55
    ]

    challenged = [page["url"] for page in pages if page.get("bot_challenge")]
    canonical_loops = [page["url"] for page in pages if (page.get("canonical_check") or {}).get("loop")]
    bad_ld_urls = [page["url"] for page in pages if page.get("structured_data_invalid_urls")]
    open_staging = [item["host"] for item in staging if item.get("open")]

    top_issues = []
    if broken_pages:
        top_issues.append(f"{len(broken_pages)} page(s) returned 4xx/0 status and need immediate review.")
    if thin_pages:
        top_issues.append(f"{len(thin_pages)} page(s) look thin for their likely purpose.")
    if missing_meta:
        top_issues.append(f"{len(missing_meta)} page(s) are missing a meta description.")
    if weak_structure:
        top_issues.append(f"{len(weak_structure)} page(s) do not have exactly one H1.")
    if noindex_pages:
        top_issues.insert(0, f"{len(noindex_pages)} page(s) ask not to be indexed (meta robots or X-Robots-Tag).")
    if missing_alt_pages:
        top_issues.append(f"{len(missing_alt_pages)} page(s) have images without an alt attribute.")
    if weak_ai_readiness:
        top_issues.append(f"{len(weak_ai_readiness)} page(s) have weak AI citation readiness signals.")
    if robots_status.get("blocked_search_bots"):
        top_issues.insert(0, "robots.txt blocks AI or search crawlers: " + ", ".join(robots_status["blocked_search_bots"]) + ".")
    if canonical_loops:
        top_issues.insert(0, f"{len(canonical_loops)} page(s) point their canonical to a URL that redirects back: the pair cancels out.")
    if bad_ld_urls:
        top_issues.append(f"{len(bad_ld_urls)} page(s) have structured-data URL fields that are not URLs.")
    if sitemap.get("bulk_lastmod"):
        top_issues.append(
            f"{int(sitemap['top_lastmod_share'] * 100)}% of dated sitemap URLs share lastmod {sitemap['top_lastmod']}: "
            "a bulk stamp, not a freshness signal."
        )
    if sitemap.get("errors") and not sitemap.get("urls"):
        top_issues.insert(0, "The sitemap could not be read: " + "; ".join(sitemap["errors"][:2]) + ".")
    if open_staging:
        top_issues.insert(0, "Staging copies answer in public without noindex: " + ", ".join(open_staging) + ". Close them (password or X-Robots-Tag) and remove indexed URLs.")
    if robots_status.get("behind_challenge"):
        top_issues.insert(0, "robots.txt answers with an anti-bot challenge: crawlers outside the CDN's verified list cannot read it.")
    if challenged:
        top_issues.insert(0, f"{len(challenged)} page(s) answered with an anti-bot challenge instead of content. Re-collect them with a real browser on a residential connection before scoring.")
    if not top_issues:
        top_issues.append("No major structural issues were detected in the sampled pages.")

    payload = {
        "mode": "sitewide-lite",
        "root_url": args.url,
        "pages_crawled": len(pages),
        "summary": {
            "broken_pages": broken_pages,
            "thin_pages": thin_pages,
            "missing_meta_description": missing_meta,
            "weak_h1_structure": weak_structure,
            "weak_ai_citation_readiness": weak_ai_readiness,
            "noindex_pages": noindex_pages,
            "pages_with_images_missing_alt": missing_alt_pages,
            "bot_challenge_pages": challenged,
            "canonical_loops": canonical_loops,
            "structured_data_invalid_urls": bad_ld_urls,
            "sitemap": sitemap,
            "staging_hosts": staging,
            "robots_ai": robots_status,
            "llms_txt": llms_status,
            "top_issues": top_issues,
        },
        "pages": pages,
    }
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
