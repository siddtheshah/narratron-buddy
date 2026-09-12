"""Tests for TikTok draft publishing and conversion endpoints."""

import io
import json
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, UploadFile

from api_server import tiktok


def _request():
    return SimpleNamespace(cookies={}, base_url="https://narratron.test/")


def test_upload_transcoded_draft_requires_authentication():
    with patch.object(tiktok, "get_current_user", return_value=None):
        with pytest.raises(HTTPException) as exc:
            tiktok.upload_transcoded_draft(_request(), UploadFile(io.BytesIO(b"webm"), filename="clip.webm"))
        assert exc.value.status_code == 401


def test_upload_transcoded_draft_checks_ffmpeg_availability():
    with patch.object(tiktok, "get_current_user", return_value={"id": 42}), \
         patch.dict(tiktok._TOKENS, {42: {"access_token": "token_abc", "expires_at": time.time() + 3600}}), \
         patch("shutil.which", return_value=None):
        with pytest.raises(HTTPException) as exc:
            tiktok.upload_transcoded_draft(_request(), UploadFile(io.BytesIO(b"webm"), filename="clip.webm"))
        assert exc.value.status_code == 503
        assert "MP4 conversion is unavailable" in exc.value.detail


def test_upload_transcoded_draft_invokes_ffmpeg_with_even_dimensions_filter():
    calls = []

    def fake_subprocess_run(cmd, **kwargs):
        calls.append(cmd)
        # Create the target file so os.path.getsize succeeds
        target_path = cmd[-1]
        with open(target_path, "wb") as f:
            f.write(b"mp4_content_bytes")
        return MagicMock(returncode=0)

    init_response = io.BytesIO(json.dumps({
        "data": {"publish_id": "pub_123", "upload_url": "https://tiktok.test/upload"}
    }).encode("utf-8"))

    put_response = io.BytesIO(b"")

    with patch.object(tiktok, "get_current_user", return_value={"id": 42}), \
         patch.dict(tiktok._TOKENS, {42: {"access_token": "tok_123", "expires_at": time.time() + 3600}}), \
         patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
         patch("subprocess.run", side_effect=fake_subprocess_run), \
         patch("api_server.tiktok.urlopen", side_effect=[init_response, put_response]):
        clip = UploadFile(io.BytesIO(b"input_webm_bytes"), filename="clip.webm")
        result = tiktok.upload_transcoded_draft(_request(), clip)

    assert result == {"publish_id": "pub_123", "transcoded": True}
    assert len(calls) == 1
    ffmpeg_cmd = calls[0]
    assert "-vf" in ffmpeg_cmd
    vf_index = ffmpeg_cmd.index("-vf")
    assert ffmpeg_cmd[vf_index + 1] == "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    assert "-pix_fmt" in ffmpeg_cmd
    assert "yuv420p" in ffmpeg_cmd


def test_upload_transcoded_draft_handles_conversion_failure():
    def fake_subprocess_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(
            returncode=1,
            cmd=cmd,
            stderr=b"[libx264 @ 0x123] width not divisible by 2 (1919x1079)\nConversion failed!\n"
        )

    with patch.object(tiktok, "get_current_user", return_value={"id": 42}), \
         patch.dict(tiktok._TOKENS, {42: {"access_token": "tok_123", "expires_at": time.time() + 3600}}), \
         patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
         patch("subprocess.run", side_effect=fake_subprocess_run):
        clip = UploadFile(io.BytesIO(b"input_webm_bytes"), filename="clip.webm")
        with pytest.raises(HTTPException) as exc:
            tiktok.upload_transcoded_draft(_request(), clip)
        assert exc.value.status_code == 422
        assert "Could not convert this clip" in exc.value.detail
        assert "Conversion failed!" in exc.value.detail


def test_upload_transcoded_draft_handles_timeout():
    def fake_subprocess_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=120)

    with patch.object(tiktok, "get_current_user", return_value={"id": 42}), \
         patch.dict(tiktok._TOKENS, {42: {"access_token": "tok_123", "expires_at": time.time() + 3600}}), \
         patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
         patch("subprocess.run", side_effect=fake_subprocess_run):
        clip = UploadFile(io.BytesIO(b"input_webm_bytes"), filename="clip.webm")
        with pytest.raises(HTTPException) as exc:
            tiktok.upload_transcoded_draft(_request(), clip)
        assert exc.value.status_code == 422
        assert "timed out" in exc.value.detail
