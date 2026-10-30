# AI Search Readiness

This skill now treats AI search readiness as a practical layer inside the audit, even without third-party APIs.

## What It Checks

- `robots.txt` access for search crawlers (Googlebot, Bingbot, OAI-SearchBot, Claude-SearchBot, PerplexityBot, Applebot, Amzn-SearchBot, meta-webindexer)
- the training opt-out group `User-agent: __usage_ai-training`, reported as a business decision
- page-level `noindex` and Bing `ai-grounding:disallow` (meta robots or `X-Robots-Tag`)
- whether the site sits behind Cloudflare, whose AI-crawler "Block" also blocks Googlebot since 15/09/2026
- images without `alt`, and content photos with an empty one
- author and date declared in structured data (articles)
- quoteable short paragraph blocks
- list structure that improves chunkability
- question-led or FAQ-style headings
- structured data presence
- `llms.txt` presence, reported as information only (not scored)
- clean heading structure
- enough textual substance for passage-level reuse

## Why It Matters

Pages that are easy to crawl are not always easy to cite.

The goal here is not to pretend we can fully measure AI visibility from outside. The goal is to identify whether the page is structured in a way that improves reuse by answer engines and LLM-driven search products.

## Heuristic Score

The `ai_citation_readiness` heuristic is not a ranking metric.

Use it as:

- a packaging score
- a retrieval-friendliness score
- a prompt for what to restructure next

## Good Signs

- search crawlers allowed in `robots.txt`
- exactly one H1
- multiple short paragraphs with standalone meaning
- FAQ framing
- list blocks
- schema present
- canonical present
- article with named author, `datePublished` and `dateModified`

## Bad Signs

- dense unbroken paragraphs
- no list structure
- no FAQ or answer-first blocks
- no schema
- no canonical
- `robots.txt` blocking any search crawler (OAI-SearchBot, Claude-SearchBot, PerplexityBot, Googlebot, Bingbot, Applebot, Amzn-SearchBot, meta-webindexer)
- `ai-grounding:disallow` on a page that should appear in Copilot
- Cloudflare "Block" enabled for AI crawlers (it takes Googlebot with it)
- the site excluded from "Search generative AI" in Search Console

## About llms.txt

Google says its AI features need no special files and that Google Search does not use `llms.txt` ([source](https://developers.google.com/search/docs/fundamentals/ai-optimization-guide)), and no major answer engine has shown that it reads the file in production retrieval. Some coding agents and assistants do read it. Treat it as optional documentation for agents, never as a fix for visibility.
