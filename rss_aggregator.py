#!/usr/bin/env python3
"""Aggregate AI + education news into a MagicMirror-compatible RSS feed."""

from __future__ import annotations

import argparse
import calendar
import hashlib
import html
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import format_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

import feedparser
import requests
from dateutil import parser as date_parser


LOGGER = logging.getLogger("ai-edu-rss")
ROOT = Path(__file__).resolve().parent
DEFAULT_FEEDS = ROOT / "config" / "feeds.json"
DEFAULT_KEYWORDS = ROOT / "config" / "keywords.json"
DEFAULT_OUTPUT = ROOT / "docs" / "aggregated_feed.xml"
USER_AGENT = "AI-Edu-RSS/1.0 (+https://github.com/jlcloudtea/ai-education-news)"
TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
}
TECHNICAL_LEARNING_PHRASES = (
    "machine learning",
    "deep learning",
    "reinforcement learning",
    "self-supervised learning",
    "supervised learning",
    "unsupervised learning",
    "transfer learning",
    "federated learning",
)


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


@dataclass
class Article:
    title: str
    link: str
    description: str
    source_name: str
    source_url: str
    source_group: str
    published: datetime
    source_priority: int
    relevance: int = 0


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def strip_html(value: str | None) -> str:
    extractor = _HTMLTextExtractor()
    extractor.feed(html.unescape(value or ""))
    return re.sub(r"\s+", " ", " ".join(extractor.parts)).strip()


def compile_keyword(keyword: str) -> re.Pattern[str]:
    escaped = re.escape(keyword).replace(r"\ ", r"[\s\-_]+")
    prefix = r"(?<![A-Za-z0-9])" if keyword[0].isalnum() else ""
    suffix = r"(?![A-Za-z0-9])" if keyword[-1].isalnum() else ""
    return re.compile(prefix + escaped + suffix, re.IGNORECASE)


def matching_keywords(text: str, patterns: dict[str, re.Pattern[str]]) -> set[str]:
    return {name for name, pattern in patterns.items() if pattern.search(text)}


def parse_published(entry: feedparser.FeedParserDict) -> datetime | None:
    for field in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(field)
        if parsed:
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)

    for field in ("published", "updated", "created"):
        value = entry.get(field)
        if not value:
            continue
        try:
            parsed_dt = date_parser.parse(value)
            if parsed_dt.tzinfo is None:
                parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
            return parsed_dt.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            continue
    return None


def canonical_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
        filtered_query = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMETERS
        ]
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(filtered_query), ""))
    except ValueError:
        return url.strip()


def normalized_title(title: str) -> str:
    title = html.unescape(title).casefold()
    title = re.sub(r"\s+[-–—|:]\s+[^-–—|:]{2,40}$", "", title)
    return re.sub(r"[^a-z0-9]+", " ", title).strip()


def titles_are_similar(left: str, right: str, threshold: float) -> bool:
    if left == right:
        return True
    if not left or not right:
        return False
    if SequenceMatcher(None, left, right).ratio() >= threshold:
        return True
    left_tokens, right_tokens = set(left.split()), set(right.split())
    union = left_tokens | right_tokens
    return bool(union) and len(left_tokens & right_tokens) / len(union) >= 0.82


def fetch_feed(feed: dict, timeout: int, session: requests.Session) -> list[Article]:
    name, url = feed["name"], feed["url"]
    try:
        response = session.get(url, timeout=timeout)
        response.raise_for_status()
        parsed = feedparser.parse(response.content)
        if parsed.bozo:
            LOGGER.warning("Feed '%s' parsed with warning: %s", name, parsed.bozo_exception)

        articles: list[Article] = []
        for entry in parsed.entries:
            title = strip_html(entry.get("title"))
            link = (entry.get("link") or "").strip()
            published = parse_published(entry)
            if not title or not link or published is None:
                continue
            description = strip_html(entry.get("summary") or entry.get("description") or "")
            articles.append(
                Article(
                    title=title,
                    link=canonical_url(link),
                    description=description,
                    source_name=name,
                    source_url=url,
                    source_group=feed.get("group", name),
                    published=published,
                    source_priority=int(feed.get("priority", 5)),
                )
            )
        LOGGER.info("Fetched %-36s %3d usable items", name, len(articles))
        return articles
    except (requests.RequestException, ValueError, TypeError) as exc:
        LOGGER.error("Feed failed %-37s %s", name, exc)
        return []


def score_article(
    article: Article,
    ai_patterns: dict[str, re.Pattern[str]],
    education_patterns: dict[str, re.Pattern[str]],
    strong_education_patterns: dict[str, re.Pattern[str]],
    negative_patterns: dict[str, re.Pattern[str]],
    hard_exclude_patterns: dict[str, re.Pattern[str]],
    now: datetime,
) -> int | None:
    title = article.title
    description = article.description
    combined = f"{title} {description}"
    if matching_keywords(combined, hard_exclude_patterns):
        return None
    education_title = title
    education_text = combined
    for phrase in TECHNICAL_LEARNING_PHRASES:
        education_title = re.sub(re.escape(phrase), " ", education_title, flags=re.IGNORECASE)
        education_text = re.sub(re.escape(phrase), " ", education_text, flags=re.IGNORECASE)

    title_ai = matching_keywords(title, ai_patterns)
    body_ai = matching_keywords(description, ai_patterns)
    title_education = matching_keywords(education_title, education_patterns)
    body_education = matching_keywords(education_text, education_patterns)
    all_ai = title_ai | body_ai
    all_education = title_education | body_education
    if not all_ai or not all_education:
        return None

    strong_education = matching_keywords(education_text, strong_education_patterns)
    negative_context = matching_keywords(combined, negative_patterns)
    if negative_context and not strong_education:
        return None

    age_hours = max(0.0, (now - article.published).total_seconds() / 3600)
    freshness = 8 if age_hours <= 24 else 5 if age_hours <= 72 else 2
    score = (
        4 * min(len(title_ai), 3)
        + 4 * min(len(title_education), 3)
        + min(len(body_ai), 3)
        + min(len(body_education), 3)
        + article.source_priority
        + freshness
    )
    return score


def prefer_article(candidate: Article, existing: Article) -> bool:
    candidate_quality = (
        candidate.source_priority,
        candidate.relevance,
        min(len(candidate.description), 600),
        candidate.published,
    )
    existing_quality = (
        existing.source_priority,
        existing.relevance,
        min(len(existing.description), 600),
        existing.published,
    )
    return candidate_quality > existing_quality


def deduplicate(articles: Iterable[Article], threshold: float) -> list[Article]:
    kept: list[Article] = []
    seen_urls: dict[str, int] = {}
    normalized_titles: list[str] = []

    for article in sorted(articles, key=lambda item: (item.relevance, item.published), reverse=True):
        url_key = canonical_url(article.link)
        title_key = normalized_title(article.title)
        duplicate_index = seen_urls.get(url_key)
        if duplicate_index is None:
            duplicate_index = next(
                (
                    index
                    for index, existing_title in enumerate(normalized_titles)
                    if titles_are_similar(title_key, existing_title, threshold)
                ),
                None,
            )

        if duplicate_index is not None:
            if prefer_article(article, kept[duplicate_index]):
                old_url = canonical_url(kept[duplicate_index].link)
                seen_urls.pop(old_url, None)
                kept[duplicate_index] = article
                normalized_titles[duplicate_index] = title_key
                seen_urls[url_key] = duplicate_index
            continue

        seen_urls[url_key] = len(kept)
        normalized_titles.append(title_key)
        kept.append(article)

    return kept


def select_articles(articles: list[Article], now: datetime, settings: dict) -> list[Article]:
    preferred_cutoff = now - timedelta(hours=int(settings["preferred_hours"]))
    fallback_cutoff = now - timedelta(days=int(settings["fallback_days"]))
    max_items = int(settings["max_items"])
    eligible = [article for article in articles if fallback_cutoff <= article.published <= now + timedelta(hours=2)]
    preferred = [article for article in eligible if article.published >= preferred_cutoff]
    older = [article for article in eligible if article.published < preferred_cutoff]
    preferred.sort(key=lambda item: (item.relevance, item.source_priority, item.published), reverse=True)
    older.sort(key=lambda item: (item.relevance, item.source_priority, item.published), reverse=True)
    ranked = preferred + older

    group_cap = int(settings.get("max_items_per_source_group", max_items))
    selected: list[Article] = []
    deferred: list[Article] = []
    group_counts: dict[str, int] = {}
    for article in ranked:
        if len(selected) >= max_items:
            break
        if group_counts.get(article.source_group, 0) >= group_cap:
            deferred.append(article)
            continue
        selected.append(article)
        group_counts[article.source_group] = group_counts.get(article.source_group, 0) + 1

    if len(selected) < max_items:
        selected.extend(deferred[: max_items - len(selected)])
    selected.sort(key=lambda item: (item.published, item.relevance), reverse=True)
    return selected


def desired_item_fingerprints(articles: list[Article]) -> list[tuple[str, ...]]:
    fingerprints = []
    for article in articles:
        source_prefix = f"Source: {article.source_name}"
        description = f"{source_prefix} — {article.description}" if article.description else source_prefix
        guid = hashlib.sha256(article.link.encode("utf-8")).hexdigest()
        fingerprints.append(
            (
                article.title,
                article.link,
                description[:1800],
                format_datetime(article.published),
                f"urn:sha256:{guid}",
                article.source_name,
                article.source_url,
            )
        )
    return fingerprints


def existing_item_fingerprints(output: Path) -> list[tuple[str, ...]] | None:
    if not output.exists():
        return None
    try:
        root = ET.parse(output).getroot()
        fingerprints = []
        for item in root.findall("./channel/item"):
            source = item.find("source")
            fingerprints.append(
                (
                    item.findtext("title", default=""),
                    item.findtext("link", default=""),
                    item.findtext("description", default=""),
                    item.findtext("pubDate", default=""),
                    item.findtext("guid", default=""),
                    source.text if source is not None and source.text else "",
                    source.get("url", "") if source is not None else "",
                )
            )
        return fingerprints
    except (ET.ParseError, OSError):
        return None


def add_text(parent: ET.Element, tag: str, value: str, **attributes: str) -> ET.Element:
    node = ET.SubElement(parent, tag, attributes)
    node.text = value
    return node


def write_rss(articles: list[Article], output: Path, site_url: str, now: datetime) -> None:
    if existing_item_fingerprints(output) == desired_item_fingerprints(articles):
        LOGGER.info("Feed items are unchanged; keeping existing %s", output)
        return
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    add_text(channel, "title", "AI Education News")
    add_text(channel, "link", site_url)
    add_text(channel, "description", "Curated AI and Education news for MagicMirror")
    add_text(channel, "language", "en")
    add_text(channel, "lastBuildDate", format_datetime(now))
    add_text(channel, "ttl", "60")

    for article in articles:
        item = ET.SubElement(channel, "item")
        add_text(item, "title", article.title)
        add_text(item, "link", article.link)
        source_prefix = f"Source: {article.source_name}"
        description = f"{source_prefix} — {article.description}" if article.description else source_prefix
        add_text(item, "description", description[:1800])
        add_text(item, "pubDate", format_datetime(article.published))
        guid = hashlib.sha256(article.link.encode("utf-8")).hexdigest()
        add_text(item, "guid", f"urn:sha256:{guid}", isPermaLink="false")
        add_text(item, "source", article.source_name, url=article.source_url)

    ET.indent(rss, space="  ")
    output.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(rss)
    tree.write(output, encoding="utf-8", xml_declaration=True)
    LOGGER.info("Wrote %d articles to %s", len(articles), output)


def aggregate(feeds_path: Path, keywords_path: Path, output: Path, site_url: str) -> int:
    feeds_config = load_json(feeds_path)
    keyword_config = load_json(keywords_path)
    settings = keyword_config["settings"]
    enabled_feeds = [feed for feed in feeds_config["feeds"] if feed.get("enabled", True)]

    ai_patterns = {keyword: compile_keyword(keyword) for keyword in keyword_config["ai_keywords"]}
    education_patterns = {
        keyword: compile_keyword(keyword) for keyword in keyword_config["education_keywords"]
    }
    strong_education_patterns = {
        keyword: compile_keyword(keyword) for keyword in keyword_config["strong_education_keywords"]
    }
    negative_patterns = {
        keyword: compile_keyword(keyword) for keyword in keyword_config["non_education_context"]
    }
    hard_exclude_patterns = {
        keyword: compile_keyword(keyword) for keyword in keyword_config.get("hard_exclude_context", [])
    }

    now = datetime.now(timezone.utc).replace(microsecond=0)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})

    LOGGER.info("Starting aggregation from %d enabled feeds", len(enabled_feeds))
    fetched: list[Article] = []
    for feed in enabled_feeds:
        fetched.extend(fetch_feed(feed, int(settings["request_timeout_seconds"]), session))

    matched: list[Article] = []
    for article in fetched:
        score = score_article(
            article,
            ai_patterns,
            education_patterns,
            strong_education_patterns,
            negative_patterns,
            hard_exclude_patterns,
            now,
        )
        if score is not None:
            article.relevance = score
            matched.append(article)

    unique = deduplicate(matched, float(settings["title_similarity_threshold"]))
    selected = select_articles(unique, now, settings)
    LOGGER.info(
        "Pipeline totals: fetched=%d matched=%d unique=%d selected=%d",
        len(fetched),
        len(matched),
        len(unique),
        len(selected),
    )
    write_rss(selected, output, site_url, now)
    return len(selected)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feeds", type=Path, default=DEFAULT_FEEDS)
    parser.add_argument("--keywords", type=Path, default=DEFAULT_KEYWORDS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--site-url",
        default="https://jlcloudtea.github.io/ai-education-news/",
        help="Public GitHub Pages URL written into the RSS channel.",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    args = parse_args()
    try:
        count = aggregate(args.feeds, args.keywords, args.output, args.site_url)
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        LOGGER.exception("Configuration or output error: %s", exc)
        return 1
    if count == 0:
        LOGGER.warning("No matching articles were selected; a valid empty RSS feed was generated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
