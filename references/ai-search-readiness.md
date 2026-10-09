# AI Search Readiness

This skill now treats AI search readiness as a practical layer inside the audit, even without third-party APIs.

## What It Checks

- `robots.txt` access for search crawlers (Googlebot, Bingbot, OAI-SearchBot, Claude-SearchBot, PerplexityBot)
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
- `robots.txt` blocking OAI-SearchBot, Claude-SearchBot, PerplexityBot, Googlebot or Bingbot

## About llms.txt

Google says its AI features need no special files ([source](https://developers.google.com/search/docs/appearance/ai-features)), and no major answer engine has shown that it reads `llms.txt` in production retrieval. Some coding agents and assistants do read it. Treat it as optional documentation for agents, never as a fix for visibility.
