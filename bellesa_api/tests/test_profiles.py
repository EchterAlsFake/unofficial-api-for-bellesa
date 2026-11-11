import pytest

from .conftest import HOST, collect, deferred, rendered_state


@pytest.mark.parametrize(
    "handle,kind,expected_count",
    [
        ("lily-starfire", "performer", 3),
        ("kenna-james", "performer", 57),
        ("belle-says", "channel", 4),
        ("zero-to-hero", "channel", 30),
        ("erikalust", "channel", 24),
    ],
)
async def test_complete_profile_reconciliation(
    client, handle, kind, expected_count, record_property
):
    path = f"/performers/{handle}" if kind == "performer" else "/videos"
    query = {} if kind == "performer" else {"providers": handle}
    first = await rendered_state(client.core, path, sort="recent", page=1, **query)
    count = first["pagination"]["total"]
    pages = first["pagination"]["pages"]
    assert count == expected_count
    expected_pages = [[str(v["id"]) for v in first["videos"]]]
    for page in range(2, pages + 1):
        state = await rendered_state(
            client.core, path, sort="recent", page=page, **query
        )
        assert state["pagination"]["page"] == page
        expected_pages.append([str(v["id"]) for v in state["videos"]])
    expected = [vid for page in expected_pages for vid in page]
    assert len(expected) == len(set(expected)) == count
    obj = await (
        client.get_performer(f"{HOST}/performers/{handle}", load_html=True)
        if kind == "performer"
        else client.get_channel(f"{HOST}/channels/{handle}")
    )
    assert obj.name and obj.profile_id
    if handle == "lily-starfire":
        assert obj.video_count == 7  # Cross-site count; free listing exposes three.
    items = await collect(obj.videos(pages="all", iterator_config=deferred()))
    assert [v.video_id for v in items] == expected
    for page in range(1, pages + 1):
        items = await collect(
            obj.videos(pages=1, start_page=page, iterator_config=deferred())
        )
        assert [v.video_id for v in items] == expected_pages[page - 1]
    assert not await collect(
        obj.videos(pages="all", start_page=pages + 1, iterator_config=deferred())
    )
    assert not await collect(
        obj.videos(pages=1, start_page=pages + 2, iterator_config=deferred())
    )
    beyond = await rendered_state(
        client.core, path, sort="recent", page=pages + 1, **query
    )
    assert beyond["videos"] == []
    record_property(
        "reconciliation",
        f"{kind}/{handle}: expected={count}, actual={len(expected)}, pages={pages}",
    )


@pytest.mark.parametrize("sort", ["recent", "longest", "popular"])
async def test_profile_sort(client, sort):
    obj = await client.get_performer(f"{HOST}/performers/kenna-james")
    expected = await rendered_state(client.core, "/performers/kenna-james", sort=sort)
    items = await collect(obj.get_videos(sort=sort, iterator_config=deferred()))
    assert [v.video_id for v in items] == [str(v["id"]) for v in expected["videos"]]
    channel = await client.get_channel(f"{HOST}/channels/zero-to-hero")
    expected = await rendered_state(
        client.core, "/videos", providers="zero-to-hero", sort=sort
    )
    items = await collect(channel.videos(sort=sort, iterator_config=deferred()))
    assert [v.video_id for v in items] == [str(v["id"]) for v in expected["videos"]]


@pytest.mark.parametrize(
    "load_api,load_html", [(True, False), (False, True), (True, True), (False, False)]
)
async def test_performer_sources(client, load_api, load_html):
    obj = await client.get_performer(
        f"{HOST}/performers/lacy-lennon", load_api=load_api, load_html=load_html
    )
    if not load_api and not load_html:
        assert obj.loaded_sources == frozenset()
        await obj.load_fields("name")
    assert obj.name == "Lacy Lennon"
    assert obj.gender == "female" and obj.thumbnail_url and obj.hero_url
