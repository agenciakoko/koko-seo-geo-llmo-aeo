#!/usr/bin/env python3
"""Shared helpers for lightweight HTML audits without third-party dependencies."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.robotparser import RobotFileParser

import json
import re


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)

STOP_WORDS = {
    "a", "an", "and", "as", "at", "be", "by", "com", "da", "das", "de", "do", "dos",
    "e", "em", "for", "from", "how", "https", "in", "is", "it", "mais", "na", "no",
    "nos", "não", "o", "of", "on", "or", "para", "por", "pra", "que", "seu", "sua",
    "the", "this", "to", "um", "uma", "ver", "você", "with", "www",
}


@dataclass
class PageSnapshot:
    url: str
    final_url: str
    status_code: int
    title: str
    meta_description: str
    canonical: str
    h1_count: int
    h1_texts: list[str]
    h2_count: int
    h3_count: int
    list_count: int
    short_paragraph_count: int
    faq_hint: bool
    schema_count: int
    links: list[str]
    word_count: int
    top_terms: list[str]
    page_type: str
    schema_types: list[str]
    has_author: bool
    date_modified: str
    date_published: str


# Crawlers that decide whether a page can show up (and be cited) in a search or answer product.
SEARCH_BOTS = {
    "Googlebot": "Google Search, AI Overviews and AI Mode",
    "Bingbot": "Bing and Copilot",
    "OAI-SearchBot": "ChatGPT search",
    "Claude-SearchBot": "Claude search",
    "PerplexityBot": "Perplexity",
}
# Crawlers that collect content for model training. Blocking them is a business choice, not a visibility fix.
TRAINING_BOTS = {
    "GPTBot": "OpenAI model training",
    "ClaudeBot": "Anthropic model training",
    "Google-Extended": "Gemini training and grounding (not Google Search)",
    "Applebot-Extended": "Apple model training",
    "CCBot": "Common Crawl",
}
# Fetchers triggered by a user inside the assistant. Vendors say robots.txt may not apply to them.
USER_FETCHERS = {
    "ChatGPT-User": "a ChatGPT user asked about the page",
    "Claude-User": "a Claude user asked about the page",
    "Perplexity-User": "a Perplexity user asked about the page",
}


class AuditHTMLParser(HTMLParser):
    """Collect a minimal set of page signals from raw HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.meta_description = ""
        self.canonical = ""
        self.h1_count = 0
        self.h1_texts: list[str] = []
        self.h2_count = 0
        self.h3_count = 0
        self.list_count = 0
        self.links: list[str] = []
        self.schema_count = 0
        self._in_title = False
        self._in_h1 = False
        self._in_h2 = False
        self._in_h3 = False
        self._in_p = False
        self._ignore_depth = 0
        self._text_chunks: list[str] = []
        self._current_h1: list[str] = []
        self._paragraphs: list[str] = []
        self._current_paragraph: list[str] = []
        self._faq_hint = False
        self._in_ldjson = False
        self._ldjson_chunks: list[str] = []
        self.schema_types: list[str] = []
        self.has_author = False
        self.date_modified = ""
        self.date_published = ""

    def _read_ldjson(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            kind = node.get("@type")
            for item in kind if isinstance(kind, list) else [kind]:
                if isinstance(item, str) and item not in self.schema_types:
                    self.schema_types.append(item)
            if node.get("author"):
                self.has_author = True
            if isinstance(node.get("dateModified"), str) and not self.date_modified:
                self.date_modified = node["dateModified"]
            if isinstance(node.get("datePublished"), str) and not self.date_published:
                self.date_published = node["datePublished"]
            stack.extend(value for value in node.values() if isinstance(value, (dict, list)))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self._ignore_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "h1":
            self._in_h1 = True
            self.h1_count += 1
            self._current_h1 = []
        if tag == "h2":
            self._in_h2 = True
            self.h2_count += 1
        if tag == "h3":
            self._in_h3 = True
            self.h3_count += 1
        if tag == "p":
            self._in_p = True
            self._current_paragraph = []
        if tag in {"ul", "ol"}:
            self.list_count += 1
        if tag == "meta" and attr_map.get("name", "").lower() == "description":
            self.meta_description = (attr_map.get("content") or "").strip()
        if tag == "link" and attr_map.get("rel", "").lower() == "canonical":
            self.canonical = (attr_map.get("href") or "").strip()
        if tag == "a" and attr_map.get("href"):
            self.links.append(attr_map["href"])
        if (
            tag == "script"
            and (attr_map.get("type") or "").lower() == "application/ld+json"
        ):
            self.schema_count += 1
            self._in_ldjson = True
            self._ldjson_chunks = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_ldjson:
            self._in_ldjson = False
            self._read_ldjson("".join(self._ldjson_chunks))
        if tag in {"script", "style", "noscript"} and self._ignore_depth > 0:
            self._ignore_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag == "h1":
            self._in_h1 = False
            text = " ".join(self._current_h1).strip()
            if text:
                self.h1_texts.append(text)
            self._current_h1 = []
        if tag == "h2":
            self._in_h2 = False
        if tag == "h3":
            self._in_h3 = False
        if tag == "p":
            self._in_p = False
            paragraph = " ".join(self._current_paragraph).strip()
            if paragraph:
                self._paragraphs.append(paragraph)
            self._current_paragraph = []

    def handle_data(self, data: str) -> None:
        if self._in_ldjson:
            self._ldjson_chunks.append(data)
            return
        if self._ignore_depth > 0:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title = f"{self.title} {text}".strip()
        if self._in_h1:
            self._current_h1.append(text)
        if self._in_h2 or self._in_h3:
            lowered = text.lower()
            if any(token in lowered for token in ("faq", "pergunta", "question", "perguntas")):
                self._faq_hint = True
        if self._in_p:
            self._current_paragraph.append(text)
        self._text_chunks.append(text)

    @property
    def visible_text(self) -> str:
        return " ".join(self._text_chunks)

    @property
    def short_paragraph_count(self) -> int:
        return sum(1 for paragraph in self._paragraphs if 40 <= len(paragraph) <= 240)

    @property
    def faq_hint(self) -> bool:
        return self._faq_hint


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-zA-ZÀ-ÿ0-9]{3,}", text.lower())
    return [token for token in tokens if token not in STOP_WORDS]


def _top_terms(text: str, limit: int = 12) -> list[str]:
    counts = Counter(_tokenize(text))
    return [term for term, _ in counts.most_common(limit)]


def _word_count(text: str) -> int:
    return len(_tokenize(text))


def infer_page_type(url: str, word_count: int) -> str:
    parsed = urlparse(url)
    path = parsed.path.lower()
    if path in {"", "/"}:
        return "landing-page"
    if "/blog/" in path or "/blog" == path.rstrip("/"):
        return "blog"
    if any(token in path for token in ("/contact", "/contato", "/pricing", "/preco")):
        return "other"
    if any(token in path for token in ("/services", "/servicos", "/demo", "/product", "/produto")):
        return "landing-page"
    if word_count >= 1200:
        return "blog"
    if word_count <= 350:
        return "other"
    return "landing-page"


def fetch_snapshot(url: str, timeout: int = 20) -> PageSnapshot:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        final_url = response.geturl()
        status_code = getattr(response, "status", 200)
        html = response.read().decode("utf-8", errors="replace")

    parser = AuditHTMLParser()
    parser.feed(html)
    visible_text = parser.visible_text
    word_count = _word_count(visible_text)
    top_terms = _top_terms(" ".join([parser.title, " ".join(parser.h1_texts), visible_text]))

    return PageSnapshot(
        url=url,
        final_url=final_url,
        status_code=status_code,
        title=parser.title,
        meta_description=parser.meta_description,
        canonical=parser.canonical,
        h1_count=parser.h1_count,
        h1_texts=parser.h1_texts,
        h2_count=parser.h2_count,
        h3_count=parser.h3_count,
        list_count=parser.list_count,
        short_paragraph_count=parser.short_paragraph_count,
        faq_hint=parser.faq_hint,
        schema_count=parser.schema_count,
        links=parser.links,
        word_count=word_count,
        top_terms=top_terms,
        page_type=infer_page_type(final_url, word_count),
        schema_types=parser.schema_types,
        has_author=parser.has_author,
        date_modified=parser.date_modified,
        date_published=parser.date_published,
    )


def normalize_internal_links(base_url: str, links: list[str]) -> list[str]:
    base = urlparse(base_url)
    normalized: list[str] = []
    seen: set[str] = set()
    for link in links:
        absolute = urljoin(base_url, link)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc != base.netloc:
            continue
        cleaned = parsed._replace(fragment="", query="").geturl()
        if cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def jaccard_similarity(left: list[str], right: list[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set and not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def dump_json(payload: dict) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


def fetch_llms_txt_status(url: str, timeout: int = 10) -> dict:
    """Check whether llms.txt exists for the given site's origin."""
    parsed = urlparse(url)
    llms_url = f"{parsed.scheme}://{parsed.netloc}/llms.txt"
    request = Request(llms_url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            content = response.read().decode("utf-8", errors="replace")
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            return {
                "url": llms_url,
                "present": True,
                "status_code": getattr(response, "status", 200),
                "preview": lines[:5],
            }
    except HTTPError as exc:
        return {
            "url": llms_url,
            "present": False,
            "status_code": exc.code,
            "preview": [],
        }
    except URLError:
        return {
            "url": llms_url,
            "present": False,
            "status_code": 0,
            "preview": [],
        }


def fetch_robots_ai_status(url: str, timeout: int = 10) -> dict:
    """Read robots.txt and say which search, training and user-triggered AI crawlers may fetch this URL."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    request = Request(robots_url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            status_code = getattr(response, "status", 200)
            lines = response.read().decode("utf-8", errors="replace").splitlines()
    except HTTPError as exc:
        status_code, lines = exc.code, []
    except URLError:
        return {"url": robots_url, "present": False, "status_code": 0, "note": "robots.txt could not be fetched"}

    rules = RobotFileParser()
    rules.parse(lines)
    if status_code in (401, 403):
        rules.disallow_all = True  # same reading Google applies to an unauthorized robots.txt
    target = url

    def allowed(bots: dict) -> dict:
        return {name: rules.can_fetch(name, target) for name in bots}

    search = allowed(SEARCH_BOTS)
    training = allowed(TRAINING_BOTS)
    fetchers = allowed(USER_FETCHERS)
    return {
        "url": robots_url,
        "present": status_code == 200 and bool(lines),
        "status_code": status_code,
        "search_bots": search,
        "training_bots": training,
        "user_fetchers": fetchers,
        "blocked_search_bots": [name for name, ok in search.items() if not ok],
        "blocked_training_bots": [name for name, ok in training.items() if not ok],
    }


def ai_citation_readiness(
    snapshot: PageSnapshot,
    llms_status: dict | None = None,
    robots_status: dict | None = None,
) -> dict:
    """Heuristic score for AI citation friendliness without external APIs.

    llms.txt is reported but not scored: Google says AI features need no special files or markup,
    and no major answer engine has shown that it reads the file in production retrieval.
    """
    score = 0
    signals: list[str] = []
    issues: list[str] = []

    if robots_status and robots_status.get("blocked_search_bots"):
        blocked = robots_status["blocked_search_bots"]
        where = ", ".join(SEARCH_BOTS[name] for name in blocked)
        issues.append(f"robots.txt blocks {', '.join(blocked)}: the page cannot be retrieved or cited in {where}.")
    elif robots_status and "search_bots" in robots_status:
        score += 10
        signals.append("robots.txt lets the search crawlers of Google, Bing, ChatGPT, Claude and Perplexity in.")

    if snapshot.h1_count == 1:
        score += 10
        signals.append("Exactly one H1 supports clean passage framing.")
    else:
        issues.append("Heading structure is weaker because the page does not have exactly one H1.")

    if snapshot.word_count >= 350:
        score += 15
        signals.append("The page has enough textual substance to support answer extraction.")
    else:
        issues.append("The page is relatively thin for rich answer extraction.")

    if snapshot.short_paragraph_count >= 3:
        score += 15
        signals.append("Multiple short paragraphs look quoteable and retrieval-friendly.")
    else:
        issues.append("There are few short standalone blocks that can be quoted cleanly.")

    if snapshot.schema_count > 0:
        score += 10
        signals.append("Structured data adds machine-readable context.")
    else:
        issues.append("No structured data was detected (not required for AI features, still useful for rich results).")

    if snapshot.has_author and (snapshot.date_modified or snapshot.date_published):
        score += 5
        signals.append("Structured data names an author and a date, which supports trust and freshness.")
    elif snapshot.page_type == "blog" and urlparse(snapshot.final_url).path.rstrip("/").rsplit("/", 1)[-1] not in {"", "blog"}:
        issues.append("The article does not declare author and date in structured data.")

    if snapshot.list_count > 0:
        score += 10
        signals.append("List structure helps chunkability and snippet extraction.")
    else:
        issues.append("The page would benefit from more list-style answer packaging.")

    if snapshot.faq_hint:
        score += 10
        signals.append("Question-led headings help answer extraction.")
    else:
        issues.append("No question-led or FAQ-style heading was detected.")

    if snapshot.meta_description:
        score += 10
        signals.append("A meta description exists and helps summarize the page intent.")
    else:
        issues.append("Missing meta description weakens summary clarity.")

    if snapshot.canonical:
        score += 5
        signals.append("Canonical tag is present.")
    else:
        issues.append("Missing canonical can weaken URL clarity.")

    notes: list[str] = []
    if robots_status is None:
        notes.append("robots.txt was not checked, so crawler access is unknown and was not scored.")
    if llms_status and llms_status.get("present"):
        notes.append("llms.txt is present. Optional: Google does not use it; some coding agents and assistants read it.")

    score = max(0, min(score, 100))
    level = "strong" if score >= 75 else "mixed" if score >= 50 else "weak"
    return {
        "score": score,
        "level": level,
        "signals": signals[:5],
        "issues": issues[:5],
        "notes": notes,
    }
