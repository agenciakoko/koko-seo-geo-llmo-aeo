#!/usr/bin/env python3
"""Shared helpers for lightweight HTML audits without third-party dependencies."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from urllib.error import HTTPError, URLError
from urllib.robotparser import RobotFileParser

import json
import re
import socket


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
    image_count: int = 0
    images_missing_alt: int = 0
    images_empty_alt: int = 0
    robots_directives: list[str] | None = None
    bot_challenge: bool = False
    invalid_ld_urls: list[str] = field(default_factory=list)


# Crawlers that decide whether a page can show up (and be cited) in a search or answer product.
SEARCH_BOTS = {
    "Googlebot": "Google Search, AI Overviews and AI Mode",
    "Bingbot": "Bing and Copilot",
    "OAI-SearchBot": "ChatGPT search",
    "Claude-SearchBot": "Claude search",
    "PerplexityBot": "Perplexity",
    "Applebot": "Siri, Spotlight and Safari",
    "Amzn-SearchBot": "Alexa",
    "meta-webindexer": "Meta AI",
}
# Crawlers that collect content for model training. Blocking them is a business choice, not a visibility fix.
TRAINING_BOTS = {
    "GPTBot": "OpenAI model training",
    "ClaudeBot": "Anthropic model training",
    "Google-Extended": "Gemini training and grounding (not Google Search)",
    "Applebot-Extended": "Apple model training",
    "CCBot": "Common Crawl",
    "Amazonbot": "Amazon model training",
    "meta-externalagent": "Meta model training",
}
# Fetchers triggered by a user inside the assistant. OpenAI, Perplexity, Amazon and Meta say robots.txt
# may not apply to them; Anthropic says Claude-User respects it.
USER_FETCHERS = {
    "ChatGPT-User": "a ChatGPT user asked about the page",
    "Claude-User": "a Claude user asked about the page",
    "Perplexity-User": "a Perplexity user asked about the page",
    "Amzn-User": "an Alexa user asked about the page",
    "meta-externalfetcher": "a Meta AI user asked about the page",
}
# Ad review crawlers: blocking them only matters if the site advertises on that surface.
AD_BOTS = {
    "OAI-AdsBot": "ChatGPT ads landing-page review",
}
# One robots.txt group that opts out of AI training across vendors (IETF draft, 30/10/2026).
# `User-agent: *` does not cover it: only an explicit group counts.
TRAINING_SIGNAL_AGENT = "__usage_ai-training"
# Structured-data properties that must hold a URL. A template that writes JSON text or a bare name here
# breaks the link between the page and the document (seen in Nov 2026: a leaflet `url` holding a JSON string).
URL_KEYS = ("url", "@id", "contentUrl", "sameAs", "logo")
# Hostnames that usually serve a staging or test copy of the site.
STAGING_PREFIXES = ("hlg", "dev", "stg", "staging", "homolog", "hml", "qa", "teste", "test", "beta", "preview")
CHALLENGE_TITLE = re.compile(r"just a moment|um momento|attention required|access denied|checking your browser", re.I)


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
        self.image_count = 0
        self.images_missing_alt = 0
        self.images_empty_alt = 0
        self.robots_directives: list[str] = []
        self.invalid_ld_urls: list[str] = []

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
            for key in URL_KEYS:
                value = node.get(key)
                if isinstance(value, str) and value.strip() and not value.strip().startswith(("http://", "https://", "/", "#")):
                    self.invalid_ld_urls.append(f"{key}: {value.strip()[:90]}")
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
        if tag == "meta" and (attr_map.get("name") or "").lower() in {"robots", "googlebot", "bingbot"}:
            for directive in (attr_map.get("content") or "").lower().split(","):
                if directive.strip():
                    self.robots_directives.append(f"{attr_map['name'].lower()}: {directive.strip()}")
        if tag == "img" and self._ignore_depth == 0:
            self.image_count += 1
            if "alt" not in attr_map:
                self.images_missing_alt += 1
            elif not (attr_map.get("alt") or "").strip():
                self.images_empty_alt += 1
            elif self._in_h1:
                # an image inside the H1 gives the heading its text (Google reads the alt)
                self._current_h1.append(attr_map["alt"].strip())
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


def is_bot_challenge(status_code: int, headers, html: str = "") -> bool:
    """True when a CDN answers with an anti-bot challenge instead of the page (Cloudflare, Akamai and the like)."""
    mitigated = str((headers or {}).get("cf-mitigated", "") or "").lower()
    if mitigated == "challenge":
        return True
    if status_code in (403, 429, 503):
        head = html[:6000]
        return bool(CHALLENGE_TITLE.search(head) or "challenge-platform" in head or "_cf_chl_opt" in head)
    return False


def fetch_snapshot(url: str, timeout: int = 20) -> PageSnapshot:
    """Fetch and parse a page. A 4xx/5xx answer is parsed too, so a challenge page is reported instead of crashing."""
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            final_url = response.geturl()
            status_code = getattr(response, "status", 200)
            headers = response.headers
            html = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        final_url, status_code, headers = exc.geturl() or url, exc.code, exc.headers or {}
        try:
            html = exc.read().decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - network behavior
            html = ""
    x_robots = (headers or {}).get("X-Robots-Tag", "") or ""
    challenge = is_bot_challenge(status_code, headers, html)

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
        image_count=parser.image_count,
        images_missing_alt=parser.images_missing_alt,
        images_empty_alt=parser.images_empty_alt,
        robots_directives=parser.robots_directives
        + [f"x-robots-tag: {d.strip().lower()}" for d in x_robots.split(",") if d.strip()],
        bot_challenge=challenge,
        invalid_ld_urls=parser.invalid_ld_urls,
    )


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401 - keep the 3xx visible
        return None


def check_canonical(final_url: str, canonical: str, timeout: int = 10) -> dict:
    """Follow one hop from the canonical. A canonical that redirects back to the page is a loop: both URLs cancel out."""
    if not canonical:
        return {"canonical": "", "self": False, "loop": False, "note": "no canonical"}
    target = urljoin(final_url, canonical)
    if target.rstrip("/") == final_url.rstrip("/"):
        return {"canonical": target, "self": True, "loop": False}
    opener = build_opener(_NoRedirect)
    status, location = 0, ""
    try:
        with opener.open(Request(target, headers={"User-Agent": USER_AGENT}), timeout=timeout) as response:
            status = getattr(response, "status", 200)
    except HTTPError as exc:
        status, location = exc.code, urljoin(target, (exc.headers or {}).get("Location", "") or "")
    except URLError:
        status = 0
    def bare(u: str) -> str:  # compare without scheme and trailing slash; path case matters
        return re.sub(r"^https?://", "", u).rstrip("/")

    loop = 300 <= status < 400 and bare(location) == bare(final_url)
    return {"canonical": target, "self": False, "status": status, "redirects_to": location, "loop": loop}


def probe_staging_hosts(url: str, timeout: int = 8) -> list[dict]:
    """Look for staging copies (hlg., dev., stg. ...) that answer in public and do not say noindex."""
    host = urlparse(url).netloc.split(":")[0]
    base = host[4:] if host.startswith("www.") else host
    found = []
    for prefix in STAGING_PREFIXES:
        name = f"{prefix}.{base}"
        try:
            socket.gethostbyname(name)
        except OSError:
            continue
        item = {"host": name, "status": 0, "open": False}
        try:
            snap = fetch_snapshot(f"https://{name}/", timeout=timeout)
            noindex = any(d.endswith(("noindex", "none")) for d in snap.robots_directives or [])
            item.update(
                status=snap.status_code,
                bot_challenge=snap.bot_challenge,
                noindex=noindex,
                canonical=snap.canonical,
                open=snap.status_code == 200 and not noindex and not snap.bot_challenge,
            )
        except Exception as exc:  # pragma: no cover - network behavior
            item["error"] = str(exc)[:120]
        found.append(item)
    return found


def sitemap_profile(url: str, timeout: int = 20) -> dict:
    """Count sitemap URLs and how their lastmod dates spread. One date on most URLs means a bulk stamp, not freshness."""
    parsed = urlparse(url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    candidates = []
    try:
        with urlopen(Request(root + "/robots.txt", headers={"User-Agent": USER_AGENT}), timeout=timeout) as response:
            for line in response.read().decode("utf-8", errors="replace").splitlines():
                if line.lower().startswith("sitemap:"):
                    candidates.append(line.split(":", 1)[1].strip())
    except Exception:  # pragma: no cover - network behavior
        pass
    candidates = candidates or [root + "/sitemap.xml"]
    urls, lastmods, fetched, errors = 0, Counter(), [], []
    queue = list(candidates)
    while queue and len(fetched) < 6:
        sm = queue.pop(0)
        try:
            with urlopen(Request(sm, headers={"User-Agent": USER_AGENT}), timeout=timeout) as response:
                xml = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            errors.append(f"{sm} -> {exc.code}")
            continue
        except Exception as exc:  # pragma: no cover - network behavior
            errors.append(f"{sm} -> {exc}")
            continue
        fetched.append(sm)
        if "<sitemapindex" in xml:
            queue.extend(re.findall(r"<loc>\s*(.*?)\s*</loc>", xml))
            continue
        for block in re.findall(r"<url>(.*?)</url>", xml, re.S):
            urls += 1
            match = re.search(r"<lastmod>\s*(\d{4}-\d{2}-\d{2})", block)
            lastmods[match.group(1) if match else "none"] += 1
    dated = sum(v for k, v in lastmods.items() if k != "none")
    top_date, top_count = next(((k, v) for k, v in lastmods.most_common() if k != "none"), ("", 0))
    share = round(top_count / dated, 2) if dated else 0.0
    return {
        "sitemaps": fetched,
        "errors": errors,
        "urls": urls,
        "with_lastmod": dated,
        "top_lastmod": top_date,
        "top_lastmod_share": share,
        "bulk_lastmod": dated >= 50 and share >= 0.5,
    }


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
    headers = {}
    challenge_html = ""
    try:
        with urlopen(request, timeout=timeout) as response:
            status_code = getattr(response, "status", 200)
            headers = response.headers
            lines = response.read().decode("utf-8", errors="replace").splitlines()
    except HTTPError as exc:
        status_code, lines, headers = exc.code, [], exc.headers or {}
        try:
            challenge_html = exc.read().decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - network behavior
            challenge_html = ""
    except URLError:
        return {"url": robots_url, "present": False, "status_code": 0, "note": "robots.txt could not be fetched"}
    behind_challenge = status_code >= 400 and is_bot_challenge(status_code, headers, challenge_html)

    rules = RobotFileParser()
    rules.parse(lines)
    if status_code in (401, 403):
        rules.disallow_all = True  # same reading Google applies to an unauthorized robots.txt
    target = url

    named = {
        match.group(1).strip().lower()
        for match in (re.match(r"\s*user-agent\s*:\s*(\S+)", line, re.I) for line in lines)
        if match
    }

    def allowed(bots: dict) -> dict:
        result = {name: rules.can_fetch(name, target) for name in bots}
        # Apple: when robots.txt does not name Applebot, it follows the rules given to Googlebot
        if "Applebot" in result and "applebot" not in named:
            result["Applebot"] = rules.can_fetch("Googlebot", target)
        return result

    search = allowed(SEARCH_BOTS)
    training = allowed(TRAINING_BOTS)
    fetchers = allowed(USER_FETCHERS)
    ads = allowed(AD_BOTS)
    signal_present = TRAINING_SIGNAL_AGENT in named
    text = "\n".join(lines)
    server = str(headers.get("Server", "")).lower() if headers else ""
    return {
        "url": robots_url,
        "present": status_code == 200 and bool(lines),
        "status_code": status_code,
        "search_bots": search,
        "training_bots": training,
        "user_fetchers": fetchers,
        "ad_bots": ads,
        "blocked_search_bots": [name for name, ok in search.items() if not ok],
        "blocked_training_bots": [name for name, ok in training.items() if not ok],
        "ai_training_signal": {
            "present": signal_present,
            "training_allowed": rules.can_fetch(TRAINING_SIGNAL_AGENT, target) if signal_present else None,
        },
        "behind_challenge": behind_challenge,
        "behind_challenge_note": (
            "robots.txt itself answers with an anti-bot challenge: crawlers outside the CDN's verified list cannot read "
            "the rules or the sitemap. Google (11/11/2026) says generic bot protection breaks crawling; allow verified bots."
            if behind_challenge
            else ""
        ),
        "cloudflare": {
            "proxied": bool(headers and (headers.get("CF-RAY") or server == "cloudflare")),
            "bot_preference_sync": "BEGIN Cloudflare Bot Preference Sync" in text,
            "note": "Since 15/09/2026 Cloudflare's Block options for AI crawlers also block Googlebot, Bingbot and "
            "Applebot by IP. robots.txt cannot show that: check the dashboard or Search Console URL inspection.",
        },
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
        signals.append("robots.txt lets the search crawlers of Google, Bing, ChatGPT, Claude, Perplexity, Apple, Amazon and Meta in.")

    directives = snapshot.robots_directives or []
    if any(d.endswith("noindex") or d.endswith("none") for d in directives):
        issues.append("The page asks not to be indexed (" + "; ".join(directives) + "): it cannot be shown or cited.")
    if any("ai-grounding:disallow" in d for d in directives):
        issues.append("The page opts out of AI grounding on Bing (ai-grounding:disallow): Copilot answers will not use it.")

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
