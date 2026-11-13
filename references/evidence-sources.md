# Evidence Sources

Use the strongest evidence source available and make the source visible in the report.
For this public skill, assume no integrations by default.

## Evidence Priority

1. The page itself
   URL, copy, headings, sections, links, schema, metadata, visible proof
2. Technical rendering evidence
   Render audit, indexed HTML exposure, mobile CWV
3. External ecosystem evidence
   PageSpeed or CrUX, SERP APIs, crawl indexes, manual comparisons

## Recommended Sources In This Repo

These work without accounts or API keys:

- `python3 scripts/smoke_test_url.py <url>`: page signals, `robots.txt` access per AI crawler, the training opt-out signal, Cloudflare in front of the site, meta robots and `X-Robots-Tag`, image alt counts, `llms.txt`, structured data types, author and dates, and Lighthouse through `npx` when Node is available
- `python3 scripts/sitewide_lite_audit.py <home> --max-pages 5`
- `python3 scripts/compare_pages.py <url-a> <url-b>`

If the host environment has its own render or Core Web Vitals tools, use them as stronger technical evidence.

## External APIs

Only bring external APIs in when they add a new evidence class or when the user explicitly asks for a deeper audit.

Useful examples:

- Google PageSpeed Insights or CrUX
  Use when you need external field data or lab performance outside the current tooling.
- Google Search Console
  The generative AI report (impressions in AI Overviews, AI Mode and Discover AI, since 31/08/2026) and the "Search generative AI" control in Settings.
- Bing Webmaster Tools
  The AI performance report shows when Copilot cites the site, which pages, and the queries behind each citation.
- SERP APIs
  Use when the user wants competitive benchmarking, SERP feature visibility, or intent gap analysis.
- Crawl or corpus APIs
  Use when the user wants broader mention visibility or retrieval-surface research.

## Sampling AI Answers

When the user wants GEO evidence, ask the same questions to Google (organic + AI Overview), Google AI Mode, ChatGPT and Perplexity from the target country and language, and record the cited domains per engine. Label it as a dated sample: answers vary between runs. A SERP API with AI add-ons does this in one run; budget it before running, because AI-engine add-ons cost far more than the plain SERP.

## Pages Behind an Anti-Bot Challenge

If `bot_challenge` is true, the scripts parsed the challenge page, not the content. Re-collect with a real browser on a residential connection (a browser automation service with residential proxies in the target country works). Do not score on the challenge page.

## Source Handling Rules

- Never merge inferred metrics with source-backed metrics as if they were the same thing.
- If live data is stale, say that it may no longer represent current performance.
- If a source is missing, write `not available` instead of approximating it.
- If the audit is content-only, avoid citing technical sources you did not actually collect.

## Confidence Rules

Use:

- `high` when the audit includes live page evidence plus technical evidence
- `medium` when the audit includes live page evidence but only partial technical evidence
- `low` when the audit is based only on pasted content, screenshots, or partial context
