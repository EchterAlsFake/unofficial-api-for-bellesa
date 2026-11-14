"""Verified Bellesa routes and extraction helpers (no network transport)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from base_api import NotFound
from selectolax.lexbor import LexborHTMLParser

HOST = "https://www.bellesa.co"
API = HOST + "/api/rest/v1"
ACCEPTED_HOSTS = frozenset({"bellesa.co", "www.bellesa.co"})
STREAM_HOST = "https://s.bellesa.co"


def normalize_url(url: str, kind: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in ACCEPTED_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 80, 443}
    ):
        raise ValueError("Expected a public bellesa.co or www.bellesa.co URL")
    patterns = {
        "video": r"/videos/[1-9][0-9]*(?:/[^/]+)?/?",
        "performer": r"/performers/[a-z0-9]+(?:[-+][a-z0-9]+)*/?",
        "channel": r"/channels/[a-z0-9]+(?:-[a-z0-9]+)*/?",
        "category": r"/videos/categories/[a-z0-9]+(?:-[a-z0-9]+)*/?",
    }
    if not re.fullmatch(patterns[kind], parsed.path):
        raise ValueError(f"Expected an individual Bellesa {kind} URL")
    return urlunsplit(("https", parsed.hostname, parsed.path.rstrip("/"), "", ""))


def identity(url: str, kind: str) -> str:
    path = urlsplit(normalize_url(url, kind)).path
    return (
        path.split("/")[2]
        if kind == "video"
        else path.rsplit("/", 1)[1].replace("+", "-")
    )


def initial_data(content: str) -> dict:
    parser = LexborHTMLParser(content)
    for script in parser.css("script"):
        text = script.text()
        match = re.search(r"window\.__INITIAL_DATA__\s*=\s*", text)
        if match:
            data, _ = json.JSONDecoder().raw_decode(text[match.end() :])
            if isinstance(data, dict):
                return data
            break
    raise NotFound("Page contains no target application data")


def meta(content: str, property_name: str) -> str | None:
    parser = LexborHTMLParser(content)
    node = parser.css_first(
        f'meta[property="{property_name}"], meta[name="{property_name}"]'
    )
    return node.attributes.get("content") if node else None


def plain_text(value: str | None) -> str | None:
    if value is None:
        return None
    return LexborHTMLParser(value).text(separator=" ", strip=True)


def video_fields(data: dict, expected_id: str) -> dict:
    if (
        not isinstance(data, dict)
        or str(data.get("id")) != expected_id
        or not data.get("title")
    ):
        raise NotFound(f"Response does not describe video {expected_id}")
    media = (data.get("media") or [{}])[0]
    source = media.get("source") or media.get("trailer") or media.get("preview")
    if source is not None and not re.fullmatch(r"[a-fA-F0-9]{24}", source):
        raise ValueError("Unexpected player source identity")
    advertised = [
        int(q) for q in (data.get("resolutions") or "").split(",") if q.isdecimal()
    ]
    # The public player uses these three renditions when resolutions is absent.
    player_qualities = advertised or [360, 480, 720]
    stream = (
        f"{STREAM_HOST}/hls/v/{source}/,{','.join(map(str, player_qualities))},.mp4.urlset/master.m3u8"
        if source
        else None
    )
    duration = media.get("duration")
    posted = data.get("posted_on")
    providers = data.get("content_provider") or []
    performers = data.get("performers") or []
    categories = data.get("categories") or []
    description_html = data.get("description")
    return {
        "title": data["title"],
        "title_tag": data.get("title_tag"),
        "description": plain_text(description_html),
        "description_html": description_html,
        "thumbnail_url": data.get("image"),
        "duration": duration,
        "length": f"{duration // 60}:{duration % 60:02}"
        if isinstance(duration, int)
        else None,
        "publish_date": datetime.fromtimestamp(posted, UTC).isoformat()
        if posted is not None
        else None,
        "posted_on": posted,
        "views": data.get("views"),
        "likes": data.get("likes"),
        "tags": [
            tag.strip() for tag in (data.get("tags") or "").split(",") if tag.strip()
        ],
        "categories": [c["name"] for c in categories],
        "category_urls": [
            f"{HOST}/videos/categories/{c['handle']}" for c in categories
        ],
        "primary_category": (data.get("primary_category") or {}).get("name"),
        "pornstars": [p["name"] for p in performers],
        "pornstars_urls": [f"{HOST}/performers/{p['handle']}" for p in performers],
        "channel_urls": [f"{HOST}/channels/{p['handle']}" for p in providers],
        "content_providers": [{"name": p["name"], "url": f"{HOST}/channels/{p['handle']}"} for p in providers],
        "author": providers[0]["name"] if providers else None,
        "author_link": f"{HOST}/channels/{providers[0]['handle']}"
        if providers
        else None,
        "advertised_qualities": advertised,
        "m3u8_base_url": stream,
        "preview_video_url": f"{STREAM_HOST}/v/{source}/preview_62.mp4"
        if source
        else None,
        "source_id": source,
        "preview_source_id": media.get("preview"),
        "trailer_source_id": media.get("trailer"),
        "access": data.get("access"),
        "highlights": data.get("highlights") or [],
    }


def profile_fields(data: dict, expected_handle: str, *, performer: bool) -> dict:
    if (
        not isinstance(data, dict)
        or data.get("handle") != expected_handle
        or not data.get("id")
    ):
        raise NotFound(f"Response does not describe profile {expected_handle}")
    fields = {
        "profile_id": str(data["id"]),
        "name": data.get("name"),
        "description": plain_text(data.get("description")),
        "thumbnail_url": data.get("image"),
        "logo_url": data.get("logo"),
    }
    if performer:
        fields.update(
            gender=data.get("gender"),
            hero_url=data.get("hero"),
            video_count=data.get("videos"),
        )
    else:
        fields.update(website_url=data.get("url"))
    return fields


def listing_items(content: str, *, kind: str):
    data = json.loads(content)
    if not isinstance(data, list):
        raise ValueError("Expected a JSON listing array")
    for record in data:
        if not isinstance(record, dict) or not record.get("id"):
            raise ValueError("Invalid listing record")
        if kind == "video":
            yield {"url": f"{HOST}/videos/{record['id']}"}
        else:
            route = {"performer": "performers", "channel": "channels"}[kind]
            yield {"url": f"{HOST}/{route}/{record['handle']}"}
