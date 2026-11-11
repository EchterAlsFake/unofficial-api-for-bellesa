from __future__ import annotations

import argparse
import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from typing import ClassVar, Literal
from urllib.parse import urlencode, urljoin

import m3u8
from base_api import (
    BaseCore,
    BaseMedia,
    CachePolicy,
    DownloadConfigHLS,
    DownloadConfigRAW,
    ErrorMode,
    Helper,
    IteratorConfig,
    NotFound,
    ResultOrder,
    ScrapeStream,
    VideoUnavailable,
    media_field,
    update_url_query,
)
from base_api.modules.logger import get_logger
from base_api.modules.provider import (
    download_errors,
    fetch_content,
    prepare_download_config,
)
from base_api.modules.static_functions import (
    available_qualities,
    choose_quality_from_list,
    collect_variants,
)

from .modules import consts
from .modules.sorting import ORDER_FIELDS, Sort

logger = get_logger(__name__)
PageCount = int | Literal["all"]


async def _fetch(core: BaseCore, url: str, *, owner=None, get_json=False):
    return await fetch_content(core, url, logger=logger, owner=owner, get_json=get_json)


def _sources(load_api: bool, load_html: bool) -> tuple[str, ...]:
    return tuple(
        s for s, enabled in (("api", load_api), ("html", load_html)) if enabled
    )


def _sort_query(sort: Sort | str) -> dict:
    return {f"order[{ORDER_FIELDS[Sort(sort)]}]": "DESC"}


def _endpoint(path: str, **params) -> str:
    return consts.API + path + ("?" + urlencode(params) if params else "")


def _listing[MediaT: BaseMedia](
    core: BaseCore,
    url: str,
    cls: type[MediaT],
    kind: str,
    *,
    pages: PageCount = 1,
    start_page: int = 1,
    iterator_config: IteratorConfig | None = None,
) -> ScrapeStream[MediaT]:
    if (
        isinstance(start_page, bool)
        or not isinstance(start_page, int)
        or start_page < 1
    ):
        raise ValueError("start_page must be a positive integer")
    if pages != "all" and (
        isinstance(pages, bool) or not isinstance(pages, int) or pages < 1
    ):
        raise ValueError('pages must be a positive integer or "all"')

    # Request one listing header to bound pages=all, including exact page-size multiples.
    # Helper owns all page/item scheduling, retries, loading, ordering, and cleanup.
    async def generate():
        response = await core.request(update_url_query(url, page=start_page))
        data = response.json()
        if not isinstance(data, list):
            raise ValueError("Expected a JSON listing array")
        if not data:
            return
        try:
            last_page = int(response.headers["x-pagination-pages-total"])
            actual_page = int(response.headers["x-pagination-page"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Missing or invalid Bellesa pagination headers") from error
        if actual_page != start_page:
            raise ValueError("Bellesa returned a different page than requested")
        end = last_page if pages == "all" else min(last_page, start_page + pages - 1)
        urls = [update_url_query(url, page=p) for p in range(start_page, end + 1)]
        config = (
            iterator_config
            if iterator_config is not None
            else IteratorConfig(
                order=ResultOrder.ORIGINAL,
                page_error_mode=ErrorMode.RAISE,
                item_error_mode=ErrorMode.RAISE,
                load_specific_sources=("api",),
            )
        )
        async with Helper(core=core, constructor=cls).iterator(
            target_page_urls=urls,
            item_extractor=partial(consts.listing_items, kind=kind),
            iterator_config=config,
        ) as stream:
            async for result in stream:
                yield result

    return ScrapeStream(generate())


@dataclass(slots=True, kw_only=True, repr=False)
class Video(BaseMedia):
    url: str
    core: BaseCore
    video_id: str | None = None
    title: str | None = media_field("api", "html")
    title_tag: str | None = media_field("api", "html")
    description: str | None = media_field("api", "html")
    description_html: str | None = media_field("api", "html")
    thumbnail_url: str | None = media_field("api", "html")
    duration: int | None = media_field("api", "html")
    length: str | None = media_field("api", "html")
    publish_date: str | None = media_field("api", "html")
    posted_on: int | None = media_field("api", "html")
    views: int | None = media_field("api", "html")
    likes: int | None = media_field("api", "html")
    tags: list[str] | None = media_field("api", "html")
    categories: list[str] | None = media_field("api", "html")
    category_urls: list[str] | None = media_field("api", "html")
    primary_category: str | None = media_field("api", "html")
    pornstars: list[str] | None = media_field("api", "html")
    pornstars_urls: list[str] | None = media_field("api", "html")
    channel_urls: list[str] | None = media_field("api", "html")
    author: str | None = media_field("api", "html")
    author_link: str | None = media_field("api", "html")
    advertised_qualities: list[int] | None = media_field("api", "html")
    m3u8_base_url: str | None = media_field("api", "html")
    preview_video_url: str | None = media_field("api", "html")
    source_id: str | None = media_field("api", "html")
    preview_source_id: str | None = media_field("api", "html")
    trailer_source_id: str | None = media_field("api", "html")
    access: dict | None = media_field("api", "html")
    highlights: list[dict] | None = media_field("api", "html")
    canonical_url: str | None = media_field("html")
    og_title: str | None = media_field("html")
    available_qualities: list[int] | None = media_field("media")
    direct_sources: dict[int, str] | None = media_field("media")
    renditions: list[dict] | None = media_field("media")
    content_url: str | None = media_field("media")

    loader_methods: ClassVar[dict[str, str]] = {
        "api": "_load_api",
        "html": "_load_html",
        "media": "_load_media",
    }

    def __post_init__(self) -> None:
        self.url = consts.normalize_url(self.url, "video")
        actual_id = consts.identity(self.url, "video")
        if self.video_id is not None and str(self.video_id) != actual_id:
            raise ValueError("video_id and URL disagree")
        self.video_id = actual_id

    async def _load_api(self) -> dict[str, object]:
        data = await _fetch(
            self.core, _endpoint(f"/videos/{self.video_id}"), owner=self, get_json=True
        )
        return consts.video_fields(data, self.video_id)

    async def _load_html(self) -> dict[str, object]:
        content = await _fetch(self.core, self.url, owner=self)
        data = consts.initial_data(content).get("video")
        fields = consts.video_fields(data, self.video_id)
        canonical = consts.meta(content, "og:url")
        if canonical and consts.identity(canonical, "video") != self.video_id:
            raise NotFound("HTML canonical identity does not match requested video")
        fields.update(
            canonical_url=canonical, og_title=consts.meta(content, "og:title")
        )
        return fields

    async def _load_media(self) -> dict[str, object]:
        await self.load_fields("m3u8_base_url")
        if not self.m3u8_base_url:
            raise VideoUnavailable("No public player source is available", url=self.url)
        content = await self.core.fetch_text(
            self.m3u8_base_url, cache_policy=CachePolicy.BYPASS
        )
        if not content.lstrip().startswith("#EXTM3U"):
            raise VideoUnavailable(
                "Player source did not return an HLS playlist", url=self.url
            )
        variants = collect_variants(m3u8.loads(content))
        if not variants:
            raise VideoUnavailable("No public HLS renditions available", url=self.url)
        sources, renditions = {}, []
        for variant in variants:
            uri = urljoin(self.m3u8_base_url, variant["uri"])
            match = re.search(r"/([0-9]+)\.mp4/", uri)
            if not match or not variant["quality"]:
                raise ValueError("Unexpected Bellesa rendition URL or dimensions")
            label = int(match[1])
            quality = variant["quality"]
            direct = f"{consts.STREAM_HOST}/v/{self.source_id}/{label}.mp4"
            sources[quality] = direct
            width, height = variant["resolution"]
            renditions.append(
                {
                    "quality": quality,
                    "source_quality": label,
                    "width": width,
                    "height": height,
                    "bandwidth": variant["bandwidth"],
                    "fps": variant["frame_rate"],
                    "codecs": variant["raw"].stream_info.codecs,
                    "hls_url": uri,
                    "direct_url": direct,
                }
            )
        qualities = available_qualities(variants)
        return {
            "available_qualities": qualities,
            "direct_sources": sources,
            "renditions": renditions,
            "content_url": sources[max(sources)],
        }

    @download_errors()
    async def download(self, configuration: DownloadConfigHLS | DownloadConfigRAW):
        await self.load_fields("title", "access")
        access = self.access or {}
        if access.get("public") != 1:
            raise VideoUnavailable("Video is not publicly accessible", url=self.url)
        await self.load_sources("media")
        config = prepare_download_config(configuration, self.title)
        if isinstance(config, DownloadConfigHLS):
            config.m3u8_base_url = self.m3u8_base_url
            return await self.core.download(configuration=config)
        if isinstance(config, DownloadConfigRAW):
            selected = choose_quality_from_list(self.direct_sources, config.quality)
            return await self.core.legacy_download(
                self.direct_sources[selected], config
            )
        raise TypeError("Expected DownloadConfigHLS or DownloadConfigRAW")

    async def refresh_media(self) -> Video:
        """Create fresh metadata and media state without mutating shared headers."""
        fresh = Video(url=self.url, core=self.core)
        self.core.cache.invalidate_url(_endpoint(f"/videos/{self.video_id}"))
        await fresh.load_sources("api", "media")
        return fresh

    async def get_author(self, load_api: bool = True) -> Channel | None:
        await self.load_fields("author_link")
        return (
            await Client(self.core).get_channel(self.author_link, load_api=load_api)
            if self.author_link
            else None
        )

    async def get_performers(self, load_api: bool = True) -> list[Performer]:
        await self.load_fields("pornstars_urls")
        client = Client(self.core)
        return [
            await client.get_performer(url, load_api=load_api)
            for url in self.pornstars_urls
        ]

    def related_videos(
        self,
        *,
        pages: PageCount = 1,
        start_page: int = 1,
        iterator_config: IteratorConfig | None = None,
    ) -> ScrapeStream[Video]:
        async def generate():
            await self.load_fields("author_link")
            if not self.author_link:
                return
            handle = consts.identity(self.author_link, "channel")
            url = _endpoint(
                "/videos",
                **{
                    "filter[source]": "bellesa",
                    "filter[provider]": handle,
                    "filter[id]": f"!={self.video_id}",
                    "limit": 6,
                },
            )
            async with _listing(
                self.core,
                url,
                Video,
                "video",
                pages=pages,
                start_page=start_page,
                iterator_config=iterator_config,
            ) as stream:
                async for result in stream:
                    yield result

        return ScrapeStream(generate())


@dataclass(slots=True, kw_only=True, repr=False)
class Channel(BaseMedia):
    url: str
    core: BaseCore
    profile_id: str | None = media_field("api")
    name: str | None = media_field("api")
    description: str | None = media_field("api")
    thumbnail_url: str | None = media_field("api")
    logo_url: str | None = media_field("api")
    website_url: str | None = media_field("api")
    loader_methods: ClassVar[dict[str, str]] = {"api": "_load_api"}

    def __post_init__(self) -> None:
        self.url = consts.normalize_url(self.url, "channel")

    async def _load_api(self) -> dict[str, object]:
        handle = consts.identity(self.url, "channel")
        data = await _fetch(
            self.core, _endpoint(f"/providers/{handle}"), owner=self, get_json=True
        )
        return consts.profile_fields(data, handle, performer=False)

    def videos(
        self,
        *,
        sort: Sort | str = Sort.RECENT,
        pages: PageCount = 1,
        start_page: int = 1,
        iterator_config: IteratorConfig | None = None,
    ) -> ScrapeStream[Video]:
        url = _endpoint(
            "/videos",
            **{
                "filter[source]": "bellesa",
                "filter[provider]": consts.identity(self.url, "channel"),
                "limit": 24,
                **_sort_query(sort),
            },
        )
        return _listing(
            self.core,
            url,
            Video,
            "video",
            pages=pages,
            start_page=start_page,
            iterator_config=iterator_config,
        )

    get_videos = videos


@dataclass(slots=True, kw_only=True, repr=False)
class Performer(BaseMedia):
    url: str
    core: BaseCore
    profile_id: str | None = media_field("api", "html")
    name: str | None = media_field("api", "html")
    description: str | None = media_field("api", "html")
    thumbnail_url: str | None = media_field("api", "html")
    logo_url: str | None = media_field("api", "html")
    gender: str | None = media_field("api", "html")
    hero_url: str | None = media_field("api", "html")
    video_count: int | None = media_field("api", "html")
    loader_methods: ClassVar[dict[str, str]] = {
        "api": "_load_api",
        "html": "_load_html",
    }

    def __post_init__(self) -> None:
        self.url = consts.normalize_url(self.url, "performer")

    async def _load_api(self) -> dict[str, object]:
        handle = consts.identity(self.url, "performer")
        data = await _fetch(
            self.core, _endpoint(f"/performers/{handle}"), owner=self, get_json=True
        )
        return consts.profile_fields(data, handle, performer=True)

    async def _load_html(self) -> dict[str, object]:
        content = await _fetch(self.core, self.url, owner=self)
        data = consts.initial_data(content).get("performer")
        return consts.profile_fields(
            data, consts.identity(self.url, "performer"), performer=True
        )

    def videos(
        self,
        *,
        sort: Sort | str = Sort.RECENT,
        pages: PageCount = 1,
        start_page: int = 1,
        iterator_config: IteratorConfig | None = None,
    ) -> ScrapeStream[Video]:
        url = _endpoint(
            "/videos",
            **{
                "filter[source]": "bellesa",
                "filter[performer]": consts.identity(self.url, "performer"),
                "limit": 9,
                **_sort_query(sort),
            },
        )
        return _listing(
            self.core,
            url,
            Video,
            "video",
            pages=pages,
            start_page=start_page,
            iterator_config=iterator_config,
        )

    get_videos = videos


@dataclass(slots=True, kw_only=True, repr=False)
class Category(BaseMedia):
    url: str
    core: BaseCore
    category_id: str | None = media_field("api")
    name: str | None = media_field("api")
    description: str | None = media_field("api")
    loader_methods: ClassVar[dict[str, str]] = {"api": "_load_api"}

    def __post_init__(self) -> None:
        self.url = consts.normalize_url(self.url, "category")

    async def _load_api(self) -> dict[str, object]:
        data = await _fetch(self.core, _endpoint("/videos/categories"), get_json=True)
        handle = consts.identity(self.url, "category")
        for category in data:
            if category["handle"] == handle:
                return {
                    "category_id": str(category["id"]),
                    "name": category["name"],
                    "description": consts.plain_text(category.get("description")),
                }
        raise NotFound(f"Category not found: {handle}")

    def videos(
        self,
        *,
        sort: Sort | str = Sort.RECENT,
        pages: PageCount = 1,
        start_page: int = 1,
        iterator_config: IteratorConfig | None = None,
    ) -> ScrapeStream[Video]:
        return Client(self.core).get_videos(
            categories=[consts.identity(self.url, "category")],
            sort=sort,
            pages=pages,
            start_page=start_page,
            iterator_config=iterator_config,
        )

    get_videos = videos


class Client:
    def __init__(self, core: BaseCore | None = None) -> None:
        self.core = core if core is not None else BaseCore()
        self._owns_core = core is None

    async def __aenter__(self) -> Client:
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_core:
            await self.core.close()

    async def get_video(
        self, url: str, load_html: bool = False, load_api: bool = True
    ) -> Video:
        obj = Video(url=url, core=self.core)
        await obj.load_sources(*_sources(load_api, load_html))
        return obj

    async def get_performer(
        self, url: str, load_html: bool = False, load_api: bool = True
    ) -> Performer:
        obj = Performer(url=url, core=self.core)
        await obj.load_sources(*_sources(load_api, load_html))
        return obj

    async def get_channel(self, url: str, load_api: bool = True) -> Channel:
        obj = Channel(url=url, core=self.core)
        if load_api:
            await obj.load_sources("api")
        return obj

    async def get_category(self, url: str, load_api: bool = True) -> Category:
        obj = Category(url=url, core=self.core)
        if load_api:
            await obj.load_sources("api")
        return obj

    async def get_categories(self) -> list[Category]:
        data = await _fetch(self.core, _endpoint("/videos/categories"), get_json=True)
        result = []
        for record in data:
            obj = await self.get_category(
                f"{consts.HOST}/videos/categories/{record['handle']}"
            )
            result.append(obj)
        return result

    def get_videos(
        self,
        *,
        sort: Sort | str = Sort.RECENT,
        categories: Sequence[str] = (),
        channels: Sequence[str] = (),
        performers: Sequence[str] = (),
        tags: Sequence[str] = (),
        min_duration: int | None = None,
        max_duration: int | None = None,
        pages: PageCount = 1,
        start_page: int = 1,
        iterator_config: IteratorConfig | None = None,
    ) -> ScrapeStream[Video]:
        params = {"filter[source]": "bellesa", "limit": 24, **_sort_query(sort)}
        for name, values in (
            ("categories", categories),
            ("filter[provider]", channels),
            ("filter[performer]", performers),
            ("filter[tags]", tags),
        ):
            if isinstance(values, str):
                raise TypeError("Filters take sequences of handles/tags, not a string")
            if values:
                params[name] = (
                    "or:" if name in {"filter[provider]", "filter[performer]"} else ""
                ) + ",".join(values)
        for bound in (min_duration, max_duration):
            if bound is not None and (
                isinstance(bound, bool) or not isinstance(bound, int) or bound < 0
            ):
                raise ValueError("Duration bounds must be nonnegative integer seconds")
        if (
            min_duration is not None
            and max_duration is not None
            and min_duration > max_duration
        ):
            raise ValueError("min_duration must be <= max_duration")
        if min_duration is not None and max_duration is not None:
            params["filter[duration]"] = f"{min_duration}-{max_duration}"
        elif min_duration is not None:
            params["filter[duration]"] = f">={min_duration}"
        elif max_duration is not None:
            params["filter[duration]"] = f"<={max_duration}"
        return _listing(
            self.core,
            _endpoint("/videos", **params),
            Video,
            "video",
            pages=pages,
            start_page=start_page,
            iterator_config=iterator_config,
        )

    def get_performers(
        self,
        *,
        gender: Literal["female", "male", "all"] = "female",
        pages: PageCount = 1,
        start_page: int = 1,
        iterator_config: IteratorConfig | None = None,
    ) -> ScrapeStream[Performer]:
        if gender not in {"female", "male", "all"}:
            raise ValueError("gender must be female, male, or all")
        params = {"filter[source]": "bellesa", "limit": 12, "order[videos]": "DESC"}
        if gender != "all":
            params["filter[gender]"] = gender
        return _listing(
            self.core,
            _endpoint("/performers", **params),
            Performer,
            "performer",
            pages=pages,
            start_page=start_page,
            iterator_config=iterator_config,
        )

    def search(
        self,
        query: str,
        *,
        kind: Literal["video", "performer", "channel"] = "video",
        sort: Sort | str = Sort.RELEVANCE,
        pages: PageCount = 1,
        start_page: int = 1,
        iterator_config: IteratorConfig | None = None,
    ) -> ScrapeStream[Video | Performer | Channel]:
        if not isinstance(query, str) or len(query.strip()) < 3:
            raise ValueError("Bellesa search requires at least three characters")
        routes = {
            "video": ("videos", Video),
            "performer": ("performers", Performer),
            "channel": ("providers", Channel),
        }
        if kind not in routes:
            raise ValueError("kind must be video, performer, or channel")
        route, cls = routes[kind]
        url = _endpoint(
            f"/{route}/search",
            sources="bellesa",
            q=query.strip(),
            limit=24,
            **_sort_query(sort),
        )
        return _listing(
            self.core,
            url,
            cls,
            kind,
            pages=pages,
            start_page=start_page,
            iterator_config=iterator_config,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Download a public Bellesa video")
    parser.add_argument("url")
    parser.add_argument("--quality", default="best")
    parser.add_argument("--output", default=".")
    parser.add_argument("--format", choices=("hls", "mp4"), default="hls")
    args = parser.parse_args()

    async def run():
        async with Client() as client:
            video = await client.get_video(args.url)
            config_type = (
                DownloadConfigHLS if args.format == "hls" else DownloadConfigRAW
            )
            options = {"quality": args.quality, "path": args.output}
            if args.format == "hls":
                options["remux"] = True
            await video.download(config_type(**options))

    asyncio.run(run())
