import pytest

from bellesa_api import Client as ClientBorrower

from .conftest import HOST, collect, deferred, rendered_state


@pytest.mark.parametrize("sort", ["recent", "longest", "popular"])
async def test_video_sorts(client, sort):
    state = await rendered_state(client.core, "/videos", sort=sort)
    items = await collect(client.get_videos(sort=sort, iterator_config=deferred()))
    assert len(items) == 24
    assert [v.video_id for v in items] == [str(v["id"]) for v in state["videos"]]


@pytest.mark.parametrize(
    "options,query",
    [
        ({"categories": ["sensual"]}, {"categories": "sensual"}),
        ({"channels": ["zero-to-hero"]}, {"providers": "zero-to-hero"}),
        ({"performers": ["kenna-james"]}, {"performers": "kenna-james"}),
        ({"tags": ["romance"]}, {"tags": "romance"}),
        ({"min_duration": 300}, {"min_duration": 5}),
        ({"max_duration": 300}, {"max_duration": 5}),
        (
            {"min_duration": 300, "max_duration": 600},
            {"min_duration": 5, "max_duration": 10},
        ),
        (
            {"channels": ["zero-to-hero", "belle-says"]},
            {"providers": "zero-to-hero,belle-says"},
        ),
    ],
)
async def test_video_filters(client, options, query):
    state = await rendered_state(client.core, "/videos", sort="recent", **query)
    items = await collect(client.get_videos(**options, iterator_config=deferred()))
    assert items
    assert [v.video_id for v in items] == [str(v["id"]) for v in state["videos"]]


async def test_categories(client):
    categories = await client.get_categories()
    assert len(categories) == 19
    category = await client.get_category(f"{HOST}/videos/categories/sensual")
    assert category.name == "Sensual" and category.description
    state = await rendered_state(
        client.core, "/videos/categories/sensual", sort="recent"
    )
    items = await collect(category.videos(iterator_config=deferred()))
    assert len(items) == 24
    assert [v.video_id for v in items] == [str(v["id"]) for v in state["videos"]]


@pytest.mark.parametrize("gender", ["female", "male", "all"])
async def test_performer_directory(client, gender):
    state = await rendered_state(client.core, "/performers", gender=gender, page=2)
    items = await collect(
        client.get_performers(gender=gender, start_page=2, iterator_config=deferred())
    )
    assert len(items) == 12
    assert [v.url.rsplit("/", 1)[1] for v in items] == [
        v["handle"] for v in state["performers"]
    ]


async def test_partial_stream_and_core_ownership(client):
    async with ClientBorrower(client.core) as borrowed:
        async with borrowed.get_videos(pages=2) as stream:
            first = (await anext(stream)).unwrap()
            assert first.title
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    assert "__INITIAL_DATA__" in await client.core.fetch_text(HOST)


@pytest.mark.parametrize("mode", ["html", "fields"])
async def test_listing_load_controls(client, mode):
    from base_api import IteratorConfig, ResultOrder

    config = IteratorConfig(order=ResultOrder.ORIGINAL)
    if mode == "html":
        config.load_specific_sources = ("html",)
    else:
        config.load_specific_fields = ("title",)
    expected = await rendered_state(client.core, "/videos", sort="recent")
    async with client.get_videos(iterator_config=config) as stream:
        item = (await anext(stream)).unwrap()
        assert item.video_id == str(expected["videos"][0]["id"])
        assert item.title == expected["videos"][0]["title"]
        assert item.loaded_sources == frozenset({"html" if mode == "html" else "api"})
