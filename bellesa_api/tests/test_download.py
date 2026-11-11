"""Complete real downloads for every rendition in representative quality sets."""

import asyncio
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import m3u8
import pytest
from base_api import DownloadConfigHLS, DownloadConfigRAW
from base_api.modules.static_functions import choose_quality_from_list

from .conftest import HOST

MATRIX = [
    (7054, [360, 450, 720, 1080, 1440, 2160]),
    (1189, [360, 450, 720]),
    (4651, [360, 450, 720, 1080]),
    (1094, [480, 600, 960]),
]


def probe(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


async def download_and_validate(video, fmt, target, directory):
    expected_quality = choose_quality_from_list(video.available_qualities, target)
    rendition = next(r for r in video.renditions if r["quality"] == expected_quality)
    callbacks = []
    output = Path(directory) / f"{video.video_id}-{fmt}-{target}.mp4"
    options = dict(
        quality=target,
        path=str(output),
        no_title=True,
        callback=lambda done, total: callbacks.append((done, total)),
    )
    if fmt == "hls":
        config = DownloadConfigHLS(**options, remux=True, return_report=True)
    else:
        config = DownloadConfigRAW(**options, max_workers=3)
    original_path = config.path
    result = await video.download(config)
    assert config.path == original_path
    if fmt == "hls":
        assert result["status"] == "completed", result
        assert config.m3u8_base_url is None
    else:
        assert result is True
    assert output.is_file() and output.stat().st_size > 10000
    info = await asyncio.to_thread(probe, output)
    videos = [s for s in info["streams"] if s["codec_type"] == "video"]
    audios = [s for s in info["streams"] if s["codec_type"] == "audio"]
    assert len(videos) == 1 and audios
    assert (videos[0]["width"], videos[0]["height"]) == (
        rendition["width"],
        rendition["height"],
    )
    actual_duration = float(info["format"]["duration"])
    assert abs(actual_duration - video.duration) <= max(3, video.duration * 0.02), (
        video.duration,
        actual_duration,
    )
    assert callbacks and callbacks[-1][0] == callbacks[-1][1]
    return f"{video.video_id}/{fmt}/{target}: {videos[0]['width']}x{videos[0]['height']}, {actual_duration:.3f}s, {output.stat().st_size} bytes, audio"


@pytest.mark.parametrize("video_id,qualities", MATRIX)
async def test_live_quality_matrix(client, video_id, qualities):
    video = await client.get_video(f"{HOST}/videos/{video_id}")
    await video.load_sources("media")
    assert video.available_qualities == qualities
    # Compare against the current real master independently of production normalization.
    master = m3u8.loads(await client.core.fetch_text(video.m3u8_base_url))
    assert [min(p.stream_info.resolution) for p in master.playlists] == qualities
    assert len(video.direct_sources) == len(master.playlists)


@pytest.mark.parametrize("fmt", ["hls", "mp4"])
@pytest.mark.parametrize(
    "video_id,quality", [(vid, q) for vid, qualities in MATRIX for q in qualities]
)
async def test_complete_downloads(client, video_id, quality, fmt, record_property):
    video = await client.get_video(f"{HOST}/videos/{video_id}")
    await video.load_sources("media")
    with TemporaryDirectory(prefix="bellesa-live-") as directory:
        evidence = await download_and_validate(video, fmt, quality, directory)
        record_property("download", evidence)
        print(evidence)


@pytest.mark.parametrize("fmt", ["hls", "mp4"])
@pytest.mark.parametrize("selector", ["best", "half", "worst", 480])
async def test_selectors_and_fallback(client, fmt, selector, record_property):
    video = await client.get_video(f"{HOST}/videos/7054")
    await video.load_sources("media")
    with TemporaryDirectory(prefix="bellesa-selectors-") as directory:
        evidence = await download_and_validate(video, fmt, selector, directory)
        record_property("download", evidence)


@pytest.mark.parametrize("fmt", ["hls", "mp4"])
async def test_concurrent_and_repeat_downloads(client, fmt, record_property):
    videos = [await client.get_video(f"{HOST}/videos/{id}") for id in (7054, 11576)]
    for video in videos:
        await video.load_sources("media")
    with TemporaryDirectory(prefix="bellesa-concurrent-") as directory:
        results = await asyncio.gather(
            *(download_and_validate(v, fmt, "worst", directory) for v in videos)
        )
        fresh = await videos[0].refresh_media()
        repeated = await download_and_validate(fresh, fmt, "half", directory)
        record_property("download", "; ".join([*results, repeated]))


async def test_hls_export_metadata(client):
    video = await client.get_video(f"{HOST}/videos/7054")
    with TemporaryDirectory(prefix="bellesa-metadata-") as directory:
        output = Path(directory) / "export.mp4"
        config = DownloadConfigHLS(
            quality="worst",
            path=str(output),
            no_title=True,
            remux=True,
            return_report=True,
            metadata={"title": video.title, "artist": video.author},
            callback=lambda *_: None,
        )
        result = await video.download(config)
        assert result["status"] == "completed" and result["metadata_written"]
        info = await asyncio.to_thread(probe, output)
        assert info["format"]["tags"]["title"] == video.title
        assert info["format"]["tags"]["artist"] == video.author
        assert config.metadata_written is False
