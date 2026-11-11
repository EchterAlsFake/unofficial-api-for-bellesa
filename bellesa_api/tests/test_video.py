from datetime import datetime

import pytest
from base_api import DataNotLoadedError, MediaLoadError, NotFound, is_resource_gone

from .conftest import API, HOST, collect, deferred


@pytest.mark.parametrize(
    "video_id,title,duration,description_present",
    [
        (11157, "Episode 110: Lily & Josh", 934, False),
        (1094, "Shower Seduction", 619, True),
        (2773, "Training Ritual", 894, True),
        (7054, "Aiden Ashley's House Party", 31, True),
    ],
)
@pytest.mark.parametrize(
    "load_api,load_html", [(True, False), (False, True), (True, True)]
)
async def test_metadata(
    client, video_id, title, duration, description_present, load_api, load_html
):
    url = f"{HOST}/videos/{video_id}"
    raw = await client.core.fetch_text(f"{API}/videos/{video_id}")
    import json

    expected = json.loads(raw)
    video = await client.get_video(url, load_api=load_api, load_html=load_html)
    assert video.video_id == str(video_id)
    assert video.title == expected["title"] == title
    assert video.duration == expected["media"][0]["duration"] == duration
    assert bool(video.description) == description_present
    assert video.description_html == expected["description"]
    assert video.thumbnail_url == expected["image"]
    assert video.tags == [t.strip() for t in expected["tags"].split(",") if t.strip()]
    assert video.views > 0 and isinstance(video.likes, int)
    assert (
        datetime.fromisoformat(video.publish_date).timestamp() == expected["posted_on"]
    )
    assert video.categories == [c["name"] for c in expected["categories"]]
    assert video.pornstars == [p["name"] for p in expected["performers"]]
    assert video.source_id == expected["media"][0]["source"]
    assert video.loaded_sources == frozenset(
        s for s, enabled in (("api", load_api), ("html", load_html)) if enabled
    )
    assert not video.is_field_loaded("available_qualities")
    if load_html:
        assert str(video_id) in video.canonical_url
        assert title in video.og_title
    else:
        assert "canonical_url" not in video.to_dict()


@pytest.mark.parametrize("order", [("html", "api"), ("api", "html")])
async def test_deferred_sources(client, order):
    video = await client.get_video(f"{HOST}/videos/1094", load_api=False)
    assert video.loaded_sources == frozenset()
    assert "title" not in video.to_dict()
    with pytest.raises(DataNotLoadedError):
        _ = video.title
    for source in order:
        await video.load_sources(source)
    assert video.title == "Shower Seduction"
    assert video.description_html and video.canonical_url
    assert video.loaded_sources == frozenset({"api", "html"})
    await video.load_fields("available_qualities")
    assert video.available_qualities == [480, 600, 960]
    assert [r["source_quality"] for r in video.renditions] == [360, 480, 720]
    assert all(r["codecs"] for r in video.renditions)


async def test_deferred_fields_and_relationships(client):
    video = await client.get_video(f"{HOST}/videos/2773", load_api=False)
    await video.load_fields("title")
    assert video.loaded_sources == frozenset({"api"})
    assert video.title == "Training Ritual"
    author = await video.get_author()
    assert author.name == "Bellesa Films"
    performers = await video.get_performers()
    assert [p.name for p in performers] == ["Lacy Lennon", "Ramon Nomar"]
    related = await collect(video.related_videos(iterator_config=deferred()))
    assert len(related) == 6
    assert "2773" not in [v.video_id for v in related]
    for item in related:
        await item.load_fields("author_link")
        assert item.author_link == video.author_link
    fresh = await video.refresh_media()
    assert fresh.video_id == video.video_id
    assert fresh.available_qualities


@pytest.mark.parametrize("host", ["bellesa.co", "www.bellesa.co"])
async def test_hosts(client, host):
    video = await client.get_video(f"https://{host}/videos/7054", load_html=True)
    assert video.video_id == "7054" and video.title == "Aiden Ashley's House Party"
    assert video.url.startswith(f"https://{host}/")
    assert video.canonical_url.startswith(HOST)
    await video.load_sources("media")
    response = await client.core.request(video.content_url, method="HEAD")
    assert response.headers["content-type"].startswith("video/")


@pytest.mark.parametrize("load_api,load_html", [(True, False), (False, True)])
async def test_missing_video(client, load_api, load_html):
    with pytest.raises(MediaLoadError) as caught:
        await client.get_video(
            f"{HOST}/videos/999999999", load_api=load_api, load_html=load_html
        )

    assert isinstance(caught.value.original_error, NotFound)
    assert is_resource_gone(caught.value)


async def test_missing_resolution_labels(client):
    import json

    import m3u8

    raw = json.loads(await client.core.fetch_text(f"{API}/videos/2013"))
    assert raw["resolutions"] is None
    video = await client.get_video(f"{HOST}/videos/2013")
    assert video.title == raw["title"]
    assert video.advertised_qualities == []
    await video.load_sources("media")
    master = m3u8.loads(await client.core.fetch_text(video.m3u8_base_url))
    assert len(master.playlists) == 3
    assert video.available_qualities == [
        min(p.stream_info.resolution) for p in master.playlists
    ]
    assert [r["source_quality"] for r in video.renditions] == [360, 480, 720]
