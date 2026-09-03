#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Cloud journal monitor for Zotero.

Default upstream discovery:
  Crossref journal endpoint by ISSN -> optional abstract enrichment
  via OpenAlex -> optional publisher landing-page metadata.

Output:
  docs/tropical-cyclone.xml
  docs/machine-learning.xml
  docs/low-altitude-economy.xml
  docs/all-matched.xml
  docs/index.html
  docs/status.json

State:
  state/articles.json

The generated XML files are ordinary RSS 2.0 feeds that Zotero can subscribe to.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
import yaml
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET


DC = "http://purl.org/dc/elements/1.1/"
CONTENT = "http://purl.org/rss/1.0/modules/content/"
PRISM = "http://prismstandard.org/namespaces/basic/2.0/"
ATOM = "http://www.w3.org/2005/Atom"

ET.register_namespace("dc", DC)
ET.register_namespace("content", CONTENT)
ET.register_namespace("prism", PRISM)
ET.register_namespace("atom", ATOM)

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
DASH_RE = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2212]")
SPACE_RE = re.compile(r"\s+")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().replace(microsecond=0).isoformat()


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        value = " ".join(str(x) for x in value if x)
    s = html.unescape(str(value))
    s = BeautifulSoup(s, "html.parser").get_text(" ", strip=True)
    s = SPACE_RE.sub(" ", s).strip()
    return s


def normalize_for_match(s: str) -> str:
    """
    Preserve the user's actual phrases, while normalizing typography:
    - case-insensitive
    - Unicode dashes -> ASCII hyphen
    - collapse whitespace
    """
    s = html.unescape(s or "")
    s = DASH_RE.sub("-", s)
    s = SPACE_RE.sub(" ", s)
    return s.casefold().strip()


def keyword_hit(keyword: str, text: str) -> bool:
    return normalize_for_match(keyword) in normalize_for_match(text)


def channel_matches(channel: dict, title: str, abstract: str) -> tuple[bool, list[str]]:
    fields = channel.get("fields", ["title", "abstract"])
    parts = []
    if "title" in fields:
        parts.append(title or "")
    if "abstract" in fields:
        parts.append(abstract or "")
    text = "\n".join(parts)

    hits = []
    for kw in channel.get("keywords", []) or []:
        if keyword_hit(str(kw), text):
            hits.append(str(kw))

    mode = str(channel.get("mode", "any")).lower()
    if mode == "all":
        ok = bool(channel.get("keywords")) and len(hits) == len(channel.get("keywords", []))
    else:
        ok = bool(hits)

    excludes = channel.get("exclude", []) or []
    if any(keyword_hit(str(kw), text) for kw in excludes):
        ok = False

    return ok, hits


def extract_doi(value: Any) -> str:
    if not value:
        return ""
    m = DOI_RE.search(str(value))
    if not m:
        return ""
    return m.group(0).rstrip('.,;:)]}>\'"').lower()


def make_key(doi: str, title: str, journal: str, published: str) -> str:
    if doi:
        return "doi:" + doi.lower()
    raw = f"{title}|{journal}|{published}".encode("utf-8", errors="ignore")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def parse_crossref_date(item: dict) -> str:
    for key in ("published-online", "published-print", "published", "issued", "created"):
        obj = item.get(key)
        if not isinstance(obj, dict):
            continue
        parts = obj.get("date-parts")
        if parts and isinstance(parts, list) and parts[0]:
            p = parts[0]
            try:
                y = int(p[0])
                m = int(p[1]) if len(p) > 1 else 1
                d = int(p[2]) if len(p) > 2 else 1
                return datetime(y, m, d, tzinfo=timezone.utc).isoformat()
            except Exception:
                pass
        if key == "created" and obj.get("date-time"):
            return str(obj["date-time"])
    return iso_now()


def parse_iso_date(value: str) -> datetime:
    try:
        s = (value or "").replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return utcnow()


def crossref_authors(item: dict) -> list[str]:
    out = []
    for a in item.get("author", []) or []:
        if not isinstance(a, dict):
            continue
        name = " ".join(x for x in [a.get("given", ""), a.get("family", "")] if x).strip()
        if name:
            out.append(name)
    return out


def make_session(settings: dict) -> requests.Session:
    email = str(settings.get("contact_email", "")).strip()
    ua = "WeatherZoteroRSS/2.0"
    if email:
        ua += f" (mailto:{email})"

    s = requests.Session()
    s.headers.update({
        "User-Agent": ua,
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


def crossref_recent_works(
    session: requests.Session,
    issn: str,
    from_date: str,
    until_date: str,
    timeout: int,
    mailto: str = "",
    rows: int = 1000,
) -> list[dict]:
    url = f"https://api.crossref.org/journals/{quote(issn)}/works"
    filters = f"from-pub-date:{from_date},until-pub-date:{until_date},type:journal-article"
    params = {
        "filter": filters,
        "rows": min(max(int(rows), 1), 1000),
        "sort": "published",
        "order": "desc",
    }
    if mailto:
        params["mailto"] = mailto

    r = session.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    payload = r.json()
    return (((payload or {}).get("message") or {}).get("items") or [])


def openalex_abstract(session: requests.Session, doi: str, timeout: int, mailto: str = "") -> str:
    if not doi:
        return ""
    # OpenAlex supports DOI as an external identifier for a single work.
    url = f"https://api.openalex.org/works/https://doi.org/{doi}"
    params = {"mailto": mailto} if mailto else None
    r = session.get(url, params=params, timeout=timeout)
    if not r.ok:
        return ""
    data = r.json() or {}
    inv = data.get("abstract_inverted_index") or {}
    if not inv:
        return ""

    words = []
    for word, positions in inv.items():
        for p in positions:
            words.append((int(p), word))
    words.sort(key=lambda x: x[0])
    return clean_text(" ".join(w for _, w in words))


def publisher_page_abstract(session: requests.Session, url: str, timeout: int) -> str:
    if not url:
        return ""
    r = session.get(url, timeout=timeout, allow_redirects=True)
    if not r.ok:
        return ""
    ct = (r.headers.get("Content-Type") or "").lower()
    if "html" not in ct and "<html" not in r.text[:1000].lower():
        return ""

    soup = BeautifulSoup(r.text, "html.parser")
    keys = {
        "citation_abstract",
        "dc.description",
        "dcterms.abstract",
        "description",
        "og:description",
        "twitter:description",
    }
    vals = []
    for tag in soup.find_all("meta"):
        key = (tag.get("name") or tag.get("property") or "").lower()
        if key in keys and tag.get("content"):
            text = clean_text(tag.get("content"))
            if text:
                vals.append(text)
    return max(vals, key=len, default="")


def article_from_crossref(item: dict, journal_cfg: dict) -> dict:
    titles = item.get("title", []) or []
    title = clean_text(titles[0]) if titles else ""
    abstract = clean_text(item.get("abstract", ""))
    doi = extract_doi(item.get("DOI", ""))
    url = clean_text(item.get("URL", "")) or (f"https://doi.org/{doi}" if doi else "")
    published = parse_crossref_date(item)

    return {
        "key": make_key(doi, title, journal_cfg.get("name", ""), published),
        "doi": doi,
        "title": title,
        "abstract": abstract,
        "link": url,
        "authors": crossref_authors(item),
        "published": published,
        "journal": journal_cfg.get("canonical_title") or journal_cfg.get("name", ""),
        "journal_label": journal_cfg.get("name", ""),
        "issn": journal_cfg.get("issn", ""),
        "first_seen": iso_now(),
        "last_seen": iso_now(),
        "enrichment_attempts": 0,
        "last_enrichment_attempt": "",
        "matched_channels": {},
    }


def match_all_channels(article: dict, channels: dict) -> dict[str, list[str]]:
    matched = {}
    for slug, cfg in channels.items():
        if not cfg.get("enabled", True):
            continue
        ok, hits = channel_matches(cfg, article.get("title", ""), article.get("abstract", ""))
        if ok:
            matched[slug] = hits
    return matched


def enrich_if_needed(
    session: requests.Session,
    article: dict,
    channels: dict,
    settings: dict,
) -> None:
    """
    If title/available abstract already matches a channel, we do not need an
    extra network request for filtering. Otherwise, when the abstract is
    missing/very short, try OpenAlex and then the publisher page.
    """
    existing = match_all_channels(article, channels)
    article["matched_channels"] = existing
    if existing:
        return

    min_chars = int(settings.get("min_abstract_chars", 80))
    if len(article.get("abstract", "")) >= min_chars:
        return

    max_attempts = int(settings.get("max_enrichment_attempts", 2))
    if int(article.get("enrichment_attempts", 0)) >= max_attempts:
        return

    timeout = int(settings.get("request_timeout_seconds", 25))
    delay = float(settings.get("request_delay_seconds", 0.10))
    mailto = str(settings.get("contact_email", "")).strip()

    article["enrichment_attempts"] = int(article.get("enrichment_attempts", 0)) + 1
    article["last_enrichment_attempt"] = iso_now()

    abstract = ""
    if settings.get("use_openalex", True) and article.get("doi"):
        try:
            abstract = openalex_abstract(session, article["doi"], timeout, mailto)
        except Exception:
            abstract = ""
        time.sleep(delay)

    if len(abstract) < min_chars and settings.get("use_publisher_page", True):
        try:
            abstract2 = publisher_page_abstract(session, article.get("link", ""), timeout)
            if len(abstract2) > len(abstract):
                abstract = abstract2
        except Exception:
            pass
        time.sleep(delay)

    if len(abstract) > len(article.get("abstract", "")):
        article["abstract"] = abstract

    article["matched_channels"] = match_all_channels(article, channels)


def infer_site_base_url(settings: dict) -> str:
    explicit = str(settings.get("site_base_url", "")).strip().rstrip("/")
    if explicit:
        return explicit

    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    if "/" in repo:
        owner, name = repo.split("/", 1)
        if name.lower() == f"{owner.lower()}.github.io":
            return f"https://{owner}.github.io"
        return f"https://{owner}.github.io/{name}"
    return "."


def rss_item(parent: ET.Element, article: dict, channel_label: str, hits: list[str]) -> None:
    item = ET.SubElement(parent, "item")
    ET.SubElement(item, "title").text = article.get("title") or "(untitled)"
    ET.SubElement(item, "link").text = article.get("link") or (
        f"https://doi.org/{article['doi']}" if article.get("doi") else ""
    )
    guid = ET.SubElement(item, "guid", {"isPermaLink": "false"})
    guid.text = article.get("key", "")

    abstract = article.get("abstract", "")
    ET.SubElement(item, "description").text = abstract
    ET.SubElement(item, f"{{{CONTENT}}}encoded").text = abstract

    dt = parse_iso_date(article.get("published", ""))
    ET.SubElement(item, "pubDate").text = format_datetime(dt)

    if article.get("doi"):
        ET.SubElement(item, f"{{{DC}}}identifier").text = f"doi:{article['doi']}"
        ET.SubElement(item, f"{{{PRISM}}}doi").text = article["doi"]

    if article.get("journal"):
        ET.SubElement(item, f"{{{PRISM}}}publicationName").text = article["journal"]
        ET.SubElement(item, "category").text = article["journal"]

    ET.SubElement(item, "category").text = channel_label
    for kw in hits:
        ET.SubElement(item, "category").text = f"keyword:{kw}"

    for author in article.get("authors", []) or []:
        ET.SubElement(item, f"{{{DC}}}creator").text = author


def write_feed(path: Path, articles: list[dict], slug: str, label: str, base_url: str) -> None:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = label
    ET.SubElement(channel, "link").text = f"{base_url}/{slug}.xml" if base_url != "." else f"{slug}.xml"
    ET.SubElement(channel, "description").text = f"Filtered journal articles for: {label}"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(utcnow())
    self_link = ET.SubElement(channel, f"{{{ATOM}}}link", {
        "rel": "self",
        "type": "application/rss+xml",
        "href": f"{base_url}/{slug}.xml" if base_url != "." else f"{slug}.xml",
    })

    for article in articles:
        hits = (article.get("matched_channels") or {}).get(slug, [])
        rss_item(channel, article, label, hits)

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def write_index(path: Path, channels: dict, base_url: str, status: dict) -> None:
    links = []
    for slug, cfg in channels.items():
        if not cfg.get("enabled", True):
            continue
        url = f"{base_url}/{slug}.xml" if base_url != "." else f"{slug}.xml"
        kws = " / ".join(html.escape(str(x)) for x in cfg.get("keywords", []) or [])
        links.append(
            f'<li><a href="{html.escape(url)}">{html.escape(cfg.get("label", slug))}</a>'
            f'<br><small>{kws}</small></li>'
        )

    all_url = f"{base_url}/all-matched.xml" if base_url != "." else "all-matched.xml"
    body = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zotero Journal RSS</title>
<style>
body{{font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:860px;margin:40px auto;padding:0 20px;line-height:1.6}}
code{{background:#f3f3f3;padding:2px 5px;border-radius:4px}}
li{{margin:16px 0}}
small{{color:#555}}
</style>
</head>
<body>
<h1>Zotero Journal RSS</h1>
<p>最后生成：<code>{html.escape(status["generated_at"])}</code></p>
<p>监控期刊：{status["journal_count"]}；缓存文章：{status["cached_article_count"]}；本次抓取：{status["fetched_this_run"]}。</p>
<h2>频道</h2>
<ul>
{''.join(links)}
<li><a href="{html.escape(all_url)}">All matched / 全部命中文献</a></li>
</ul>
<p><a href="status.json">status.json</a></p>
</body>
</html>"""
    path.write_text(body, encoding="utf-8")


def prune_state(articles: dict, retention_days: int) -> dict:
    cutoff = utcnow() - timedelta(days=retention_days)
    kept = {}
    for key, a in articles.items():
        dt = parse_iso_date(a.get("published", a.get("first_seen", "")))
        if dt >= cutoff:
            kept[key] = a
    return kept


def run(config_dir: Path) -> int:
    settings_doc = load_yaml(config_dir / "settings.yaml")
    settings = settings_doc.get("settings", {}) or {}
    journals = (load_yaml(config_dir / "journals.yaml").get("journals") or [])
    channels = (load_yaml(config_dir / "channels.yaml").get("channels") or {})

    state_path = config_dir / str(settings.get("state_file", "state/articles.json"))
    output_dir = config_dir / str(settings.get("output_dir", "docs"))
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    state = load_json(state_path, {"version": 1, "articles": {}})
    articles = state.get("articles", {}) or {}

    session = make_session(settings)
    timeout = int(settings.get("request_timeout_seconds", 25))
    mailto = str(settings.get("contact_email", "")).strip()
    lookback_days = int(settings.get("crossref_lookback_days", 35))
    rows = int(settings.get("crossref_rows_per_journal", 1000))
    delay = float(settings.get("request_delay_seconds", 0.10))

    today = utcnow().date()
    from_date = (today - timedelta(days=lookback_days)).isoformat()
    until_date = today.isoformat()

    fetched_this_run = 0
    journal_results = []
    errors = []

    for j in journals:
        if not j.get("enabled", True):
            continue
        name = j.get("name", "")
        issn = str(j.get("issn", "")).strip()
        if not issn:
            errors.append({"journal": name, "error": "Missing ISSN"})
            continue

        try:
            items = crossref_recent_works(
                session, issn, from_date, until_date, timeout, mailto, rows
            )
            journal_results.append({"journal": name, "issn": issn, "items": len(items), "ok": True})
            print(f"[OK] {name}: {len(items)} recent Crossref items")
        except Exception as e:
            errors.append({"journal": name, "error": str(e)})
            journal_results.append({"journal": name, "issn": issn, "items": 0, "ok": False})
            print(f"[ERROR] {name}: {e}", file=sys.stderr)
            continue

        for item in items:
            a = article_from_crossref(item, j)
            key = a["key"]
            fetched_this_run += 1

            if key in articles:
                old = articles[key]
                # Accept newly deposited Crossref abstract/metadata.
                if len(a.get("abstract", "")) > len(old.get("abstract", "")):
                    old["abstract"] = a["abstract"]
                if not old.get("authors") and a.get("authors"):
                    old["authors"] = a["authors"]
                if not old.get("link") and a.get("link"):
                    old["link"] = a["link"]
                old["matched_channels"] = match_all_channels(old, channels)

                # A recent article that still has no useful abstract gets a limited retry.
                if not old["matched_channels"] and len(old.get("abstract", "")) < int(settings.get("min_abstract_chars", 80)):
                    enrich_if_needed(session, old, channels, settings)
                articles[key] = old
            else:
                enrich_if_needed(session, a, channels, settings)
                articles[key] = a

        time.sleep(delay)

    articles = prune_state(articles, int(settings.get("state_retention_days", 365)))
    state["articles"] = articles
    save_json(state_path, state)

    feed_days = int(settings.get("feed_history_days", 180))
    feed_cutoff = utcnow() - timedelta(days=feed_days)
    max_items = int(settings.get("max_items_per_channel", 300))
    base_url = infer_site_base_url(settings)

    enabled_channels = {k: v for k, v in channels.items() if v.get("enabled", True)}
    all_matched = {}
    channel_counts = {}

    for slug, cfg in enabled_channels.items():
        selected = []
        for a in articles.values():
            if slug not in (a.get("matched_channels") or {}):
                continue
            if parse_iso_date(a.get("published", "")) < feed_cutoff:
                continue
            selected.append(a)

        selected.sort(key=lambda x: parse_iso_date(x.get("published", "")), reverse=True)
        selected = selected[:max_items]
        channel_counts[slug] = len(selected)
        for a in selected:
            all_matched[a["key"]] = a
        write_feed(output_dir / f"{slug}.xml", selected, slug, cfg.get("label", slug), base_url)
        print(f"[WRITE] {slug}.xml: {len(selected)}")

    union = list(all_matched.values())
    union.sort(key=lambda x: parse_iso_date(x.get("published", "")), reverse=True)
    union = union[:max_items]
    write_feed(output_dir / "all-matched.xml", union, "all-matched", "All matched articles", base_url)

    missing_abs = sum(
        1 for a in articles.values()
        if len(a.get("abstract", "")) < int(settings.get("min_abstract_chars", 80))
    )

    status = {
        "generated_at": iso_now(),
        "site_base_url": base_url,
        "journal_count": sum(1 for j in journals if j.get("enabled", True)),
        "cached_article_count": len(articles),
        "fetched_this_run": fetched_this_run,
        "missing_or_short_abstract_count": missing_abs,
        "channel_counts": channel_counts,
        "journal_results": journal_results,
        "errors": errors,
    }
    save_json(output_dir / "status.json", status)
    write_index(output_dir / "index.html", enabled_channels, base_url, status)
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")

    print(f"[DONE] Generated {len(enabled_channels)} channel feeds + all-matched.xml")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config-dir",
        default=str(Path(__file__).resolve().parent),
        help="Directory containing settings.yaml, journals.yaml and channels.yaml",
    )
    args = parser.parse_args()
    return run(Path(args.config_dir).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
