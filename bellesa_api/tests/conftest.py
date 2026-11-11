"""Live network fixtures; no mocked transport or response replays."""

import json
import re
from urllib.parse import urlencode

import pytest
from base_api import BaseCore, IteratorConfig, ResultOrder
from base_api.modules.config import RuntimeConfig

from bellesa_api import Client

HOST = "https://www.bellesa.co"
API = HOST + "/api/rest/v1"


@pytest.fixture
async def client():
    config = RuntimeConfig()
    config.pages_concurrency = 2
    config.videos_concurrency = 3
    config.max_workers_download = 4
    async with BaseCore(config) as core:
        async with Client(core) as result:
            yield result


def deferred():
    return IteratorConfig(order=ResultOrder.ORIGINAL)


async def collect(stream):
    async with stream:
        return [result.unwrap() async for result in stream]


async def rendered_state(core, path, **query):
    """Independent extraction of the SSR listing response used by the rendered tab."""
    url = HOST + path + ("?" + urlencode(query) if query else "")
    text = await core.fetch_text(url)
    match = re.search(r"window\.__INITIAL_DATA__\s*=\s*(.*?);</script>", text, re.S)
    assert match, url
    state = json.loads(match[1])
    assert isinstance(state, dict), url
    return state
