import pytest

from .conftest import collect, deferred, rendered_state


@pytest.mark.parametrize("sort", ["relevance", "recent", "longest", "popular"])
async def test_search(client, sort):
    state = await rendered_state(
        client.core, "/search", q="romance", type="videos", sort=sort
    )
    items = await collect(
        client.search("romance", sort=sort, iterator_config=deferred())
    )
    assert len(items) == 24
    assert [v.video_id for v in items] == [str(v["id"]) for v in state["data"]]


@pytest.mark.parametrize(
    "kind,query,handle",
    [("performer", "lacy lennon", "lacy-lennon"), ("channel", "zero", "zero-to-hero")],
)
async def test_typed_search(client, kind, query, handle):
    items = await collect(client.search(query, kind=kind, pages="all"))
    assert items and any(v.url.endswith("/" + handle) for v in items)
    assert all(v.name for v in items)


async def test_search_pagination_and_empty(client):
    state = await rendered_state(
        client.core, "/search", q="romance", type="videos", page=2
    )
    items = await collect(
        client.search("romance", start_page=2, iterator_config=deferred())
    )
    assert len(items) == 24
    assert [v.video_id for v in items] == [str(v["id"]) for v in state["data"]]
    empty = await rendered_state(
        client.core, "/search", q="zzqvxtnrmplkc9384756", type="videos"
    )
    assert empty["data"] == [] and empty["pagination"]["total"] == 0
    assert not await collect(
        client.search("zzqvxtnrmplkc9384756", iterator_config=deferred())
    )
    assert not await collect(
        client.search("romance", start_page=10000, iterator_config=deferred())
    )


async def test_default_search_loads_metadata(client):
    items = await collect(client.search("lily josh"))
    assert items and items[0].title
    assert all(v.loaded_sources == frozenset({"api"}) for v in items)
