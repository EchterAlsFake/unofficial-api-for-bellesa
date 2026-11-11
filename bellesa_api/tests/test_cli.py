import asyncio
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from .conftest import HOST


@pytest.mark.parametrize("fmt", ["hls", "mp4"])
async def test_cli_live_download(fmt):
    with TemporaryDirectory(prefix="bellesa-cli-") as directory:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "bellesa_api",
            f"{HOST}/videos/7054",
            "--quality",
            "worst",
            "--format",
            fmt,
            "--output",
            directory,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        assert proc.returncode == 0, (stdout.decode(), stderr.decode())
        outputs = list(Path(directory).glob("*.mp4"))
        assert len(outputs) == 1
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(outputs[0])],
            check=True,
            capture_output=True,
            text=True,
        )
        info = json.loads(result.stdout)
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        assert (video["width"], video["height"]) == (640, 360)
        assert any(s["codec_type"] == "audio" for s in info["streams"])
