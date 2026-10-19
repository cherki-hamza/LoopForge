import os
import math
import tempfile
import subprocess
import pytest
from app.services.ffmpeg_pipeline import (
    calculate_loop_count,
    build_video_only_cmd,
    build_audio_wav_cmd,
    build_loop_merge_cmd,
    verify_output_duration,
    probe_media,
    validate_clip,
    run_pipeline,
    MediaInfo,
    FFmpegPipelineError,
)


def test_calculate_loop_count():
    # 30 min (1800s) with 30s clip -> 60 loops
    assert calculate_loop_count(1800.0, 30.0) == 60
    # 1 hr (3600s) with 45s clip -> 80 loops
    assert calculate_loop_count(3600.0, 45.0) == 80
    # 1800s with 35.5s clip -> ceil(50.704) = 51 loops
    assert calculate_loop_count(1800.0, 35.5) == 51
    # Target smaller than clip -> 1 loop
    assert calculate_loop_count(20.0, 45.0) == 1
    # Negative / zero checks
    with pytest.raises(ValueError):
        calculate_loop_count(0.0, 30.0)
    with pytest.raises(ValueError):
        calculate_loop_count(1800.0, 0.0)


def test_build_video_only_cmd():
    fast_cmd = build_video_only_cmd("in.mp4", "out.mp4", mode="fast")
    assert fast_cmd == ["ffmpeg", "-y", "-i", "in.mp4", "-an", "-c:v", "copy", "out.mp4"]

    safe_cmd = build_video_only_cmd("in.mp4", "out.mp4", mode="safe")
    assert "-c:v" in safe_cmd
    assert "libx264" in safe_cmd
    assert "-crf" in safe_cmd
    assert "18" in safe_cmd
    assert "-preset" in safe_cmd
    assert "veryfast" in safe_cmd


def test_build_audio_wav_cmd():
    cmd = build_audio_wav_cmd("in.mp4", "audio.wav", duration=42.123456)
    assert "-vn" in cmd
    assert "-af" in cmd
    idx = cmd.index("-af")
    assert cmd[idx + 1] == "atrim=0:42.123456,apad=whole_dur=42.123456"
    assert "-ar" in cmd and cmd[cmd.index("-ar") + 1] == "48000"
    assert "-ac" in cmd and cmd[cmd.index("-ac") + 1] == "2"
    assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "pcm_s16le"


def test_build_loop_merge_cmd():
    # fast mode
    cmd_fast = build_loop_merge_cmd("v.mp4", "a.wav", "final.mp4", loops=5, target_seconds=120.0, mode="fast")
    assert "-stream_loop" in cmd_fast
    assert cmd_fast[cmd_fast.index("-stream_loop") + 1] == "4"  # loops - 1
    assert "-map" in cmd_fast
    assert "-c:v" in cmd_fast and cmd_fast[cmd_fast.index("-c:v") + 1] == "copy"
    assert "-c:a" in cmd_fast and cmd_fast[cmd_fast.index("-c:a") + 1] == "aac"
    assert "-b:a" in cmd_fast and cmd_fast[cmd_fast.index("-b:a") + 1] == "192k"
    assert "-t" in cmd_fast and cmd_fast[cmd_fast.index("-t") + 1] == "120.000000"
    assert "-movflags" in cmd_fast and "+faststart" in cmd_fast

    # safe mode
    cmd_safe = build_loop_merge_cmd("v.mp4", "a.wav", "final.mp4", loops=3, target_seconds=60.0, mode="safe")
    assert cmd_safe[cmd_safe.index("-stream_loop") + 1] == "2"
    assert "libx264" in cmd_safe
    assert "yuv420p" in cmd_safe


def test_validate_clip():
    valid = MediaInfo(
        video_duration=30.0,
        audio_duration=30.0,
        has_video=True,
        has_audio=True,
        video_codec="h264",
        audio_codec="aac",
        bitrate=2000000,
    )
    validate_clip(valid, max_duration=120.0)

    # Missing video
    with pytest.raises(FFmpegPipelineError, match="no video stream"):
        validate_clip(MediaInfo(30.0, 30.0, False, True, None, "aac", 2000000))

    # Missing audio
    with pytest.raises(FFmpegPipelineError, match="no audio stream"):
        validate_clip(MediaInfo(30.0, 30.0, True, False, "h264", None, 2000000))

    # Exceeding duration
    with pytest.raises(FFmpegPipelineError, match="exceeds maximum"):
        validate_clip(MediaInfo(150.0, 150.0, True, True, "h264", "aac", 2000000), max_duration=120.0)


def test_verify_output_duration_mock(tmp_path):
    # Non-existent file
    valid, dur, reason = verify_output_duration(str(tmp_path / "does_not_exist.mp4"), target_seconds=60.0)
    assert not valid
    assert "does not exist" in reason


def test_real_ffmpeg_pipeline_execution(tmp_path):
    """Create a 2-second synthetic video with audio, run pipeline for 6 seconds (3 loops), and verify."""
    input_clip = tmp_path / "test_input.mp4"
    output_clip = tmp_path / "test_output.mp4"

    # Generate synthetic 2-second test video with sine wave audio
    gen_cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        str(input_clip)
    ]
    subprocess.run(gen_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Probe input
    info = probe_media(str(input_clip))
    assert info.has_video is True
    assert info.has_audio is True
    assert 1.9 <= info.video_duration <= 2.1

    # Run pipeline for 6 seconds target
    progress_calls = []

    def on_progress(pct, eta):
        progress_calls.append((pct, eta))

    actual_mode, out_dur = run_pipeline(
        input_path=input_clip,
        final_output_path=output_clip,
        target_seconds=6.0,
        mode="fast",
        temp_dir_base=tmp_path,
        on_progress=on_progress,
    )

    assert os.path.exists(output_clip)
    assert abs(out_dur - 6.0) <= 0.5
    assert len(progress_calls) > 0

    # Probe final output
    out_info = probe_media(str(output_clip))
    assert out_info.has_video is True
    assert out_info.has_audio is True
    assert abs(out_info.video_duration - 6.0) <= 0.5
