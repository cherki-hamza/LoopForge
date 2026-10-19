import json
import logging
import math
import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List, Tuple, Union

logger = logging.getLogger(__name__)


class FFmpegPipelineError(Exception):
    pass


class FFmpegVerificationError(FFmpegPipelineError):
    pass


class FFmpegCancelledError(FFmpegPipelineError):
    pass


@dataclass
class MediaInfo:
    video_duration: float
    audio_duration: float
    has_video: bool
    has_audio: bool
    video_codec: Optional[str]
    audio_codec: Optional[str]
    bitrate: Optional[int]


def calculate_loop_count(target_seconds: float, clip_duration: float) -> int:
    """Calculate ceil(target_seconds / clip_duration), minimum 1."""
    if clip_duration <= 0:
        raise ValueError("Clip duration must be greater than 0")
    if target_seconds <= 0:
        raise ValueError("Target duration must be greater than 0")
    return max(1, math.ceil(target_seconds / clip_duration))


def probe_media(filepath: Union[str, Path]) -> MediaInfo:
    """Probe media file with ffprobe using argument list."""
    path_str = str(filepath)
    if not os.path.exists(path_str):
        raise FFmpegPipelineError(f"Input file not found: {path_str}")

    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "stream=index,codec_type,codec_name,duration,bit_rate:format=duration,bit_rate",
        "-of", "json",
        path_str,
    ]

    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        data = json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.strip()
        logger.error(f"ffprobe failed: {err_msg}")
        raise FFmpegPipelineError(f"Invalid video format: Unable to read media stream. {err_msg[:200]}")
    except json.JSONDecodeError as e:
        raise FFmpegPipelineError(f"Invalid media format: Failed to parse ffprobe output: {str(e)}")

    streams = data.get("streams", [])
    format_info = data.get("format", {})

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    has_video = video_stream is not None
    has_audio = audio_stream is not None

    format_duration = None
    if "duration" in format_info and format_info["duration"] != "N/A":
        try:
            format_duration = float(format_info["duration"])
        except (ValueError, TypeError):
            pass

    # Video duration
    video_duration = 0.0
    if video_stream:
        dur_str = video_stream.get("duration")
        if dur_str and dur_str != "N/A":
            try:
                video_duration = float(dur_str)
            except (ValueError, TypeError):
                pass
        if video_duration <= 0.0 and format_duration:
            video_duration = format_duration

    # Audio duration
    audio_duration = 0.0
    if audio_stream:
        dur_str = audio_stream.get("duration")
        if dur_str and dur_str != "N/A":
            try:
                audio_duration = float(dur_str)
            except (ValueError, TypeError):
                pass
        if audio_duration <= 0.0 and format_duration:
            audio_duration = format_duration

    # Bitrate
    bitrate = None
    if "bit_rate" in format_info and format_info["bit_rate"] != "N/A":
        try:
            bitrate = int(format_info["bit_rate"])
        except (ValueError, TypeError):
            pass

    return MediaInfo(
        video_duration=video_duration,
        audio_duration=audio_duration,
        has_video=has_video,
        has_audio=has_audio,
        video_codec=video_stream.get("codec_name") if video_stream else None,
        audio_codec=audio_stream.get("codec_name") if audio_stream else None,
        bitrate=bitrate,
    )


def validate_clip(media_info: MediaInfo, max_duration: float = 120.0) -> None:
    """Validate that the clip contains both video and audio streams and does not exceed max_duration."""
    if not media_info.has_video:
        raise FFmpegPipelineError("Invalid video: File contains no video stream.")
    if not media_info.has_audio:
        raise FFmpegPipelineError("Invalid video: File contains no audio stream. Both video and audio are required for seamless looping.")
    if media_info.video_duration <= 0.0:
        raise FFmpegPipelineError("Invalid video: Unable to determine video duration.")
    if media_info.video_duration > max_duration:
        raise FFmpegPipelineError(
            f"Clip duration ({media_info.video_duration:.1f}s) exceeds maximum allowed ({max_duration:.1f}s)."
        )


def build_video_only_cmd(input_path: str, output_path: str, mode: str = "fast") -> List[str]:
    """Step 2: extract video-only file."""
    cmd = ["ffmpeg", "-y", "-i", input_path, "-an"]
    if mode == "safe":
        cmd.extend(["-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p"])
    else:
        cmd.extend(["-c:v", "copy"])
    cmd.append(output_path)
    return cmd


def build_audio_wav_cmd(input_path: str, output_path: str, duration: float) -> List[str]:
    """Step 3: extract audio to PCM WAV 48 kHz stereo, trimmed/padded to exactly V."""
    dur_str = f"{duration:.6f}"
    return [
        "ffmpeg", "-y", "-i", input_path, "-vn",
        "-af", f"atrim=0:{dur_str},apad=whole_dur={dur_str}",
        "-ar", "48000", "-ac", "2",
        "-c:a", "pcm_s16le",
        output_path,
    ]


def build_loop_merge_cmd(
    video_path: str,
    audio_path: str,
    output_path: str,
    loops: int,
    target_seconds: float,
    mode: str = "fast"
) -> List[str]:
    """Step 4: loop both inputs and encode audio once at the end."""
    stream_loops = str(max(0, loops - 1))
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", stream_loops, "-i", video_path,
        "-stream_loop", stream_loops, "-i", audio_path,
        "-map", "0:v", "-map", "1:a",
    ]
    if mode == "safe":
        cmd.extend(["-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p"])
    else:
        cmd.extend(["-c:v", "copy"])

    cmd.extend([
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{target_seconds:.6f}",
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        "-nostats",
        output_path,
    ])
    return cmd


def verify_output_duration(output_path: str, target_seconds: float, tolerance: float = 1.0) -> Tuple[bool, float, Optional[str]]:
    """Step 5: verify output with ffprobe: duration within 1s and both streams present."""
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return False, 0.0, "Output file does not exist or is empty"

    try:
        info = probe_media(output_path)
    except Exception as e:
        return False, 0.0, f"Failed to probe output video: {str(e)}"

    if not info.has_video:
        return False, info.video_duration, "Output video is missing a video stream"
    if not info.has_audio:
        return False, info.video_duration, "Output video is missing an audio stream"

    diff = abs(info.video_duration - target_seconds)
    if diff > tolerance:
        return False, info.video_duration, f"Output duration ({info.video_duration:.2f}s) differs from target ({target_seconds:.2f}s) by {diff:.2f}s (tolerance: {tolerance}s)"

    return True, info.video_duration, None


def kill_process_group(pid: int) -> None:
    """Kill entire process group for a given PID."""
    if not pid:
        return
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)
        time.sleep(0.5)
        # Check if still running
        try:
            os.killpg(pgid, 0)
            # Still alive, force kill
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass
    except OSError:
        pass


def execute_step_with_progress(
    cmd: List[str],
    target_seconds: float,
    on_progress: Optional[Callable[[float, Optional[float]], None]] = None,
    on_pid: Optional[Callable[[int], None]] = None,
    timeout_seconds: Optional[int] = None,
    check_cancelled: Optional[Callable[[], bool]] = None,
) -> None:
    """Execute ffmpeg command, parsing -progress output and reporting percentage and ETA."""
    logger.info(f"Running command: {' '.join(cmd)}")
    start_time = time.time()
    last_report_time = 0.0

    # Start subprocess in its own process group
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        preexec_fn=os.setsid if hasattr(os, "setsid") else None,
    )

    if on_pid:
        on_pid(process.pid)

    progress_data: Dict[str, str] = {}

    try:
        while True:
            # Check cancellation
            if check_cancelled and check_cancelled():
                kill_process_group(process.pid)
                raise FFmpegCancelledError("Job was cancelled by user")

            # Check timeout
            if timeout_seconds and (time.time() - start_time) > timeout_seconds:
                kill_process_group(process.pid)
                raise FFmpegPipelineError(f"Job exceeded timeout of {timeout_seconds} seconds")

            line = process.stdout.readline() if process.stdout else ""
            if not line:
                if process.poll() is not None:
                    break
                time.sleep(0.05)
                continue

            line = line.strip()
            if "=" in line:
                k, v = line.split("=", 1)
                progress_data[k.strip()] = v.strip()

            # When progress line comes in, calculate
            if "out_time_us" in progress_data or "out_time_ms" in progress_data:
                # out_time_us in microseconds
                out_us = progress_data.get("out_time_us") or progress_data.get("out_time_ms")
                try:
                    current_sec = float(out_us) / 1_000_000.0
                    pct = min(99.0, max(0.0, (current_sec / target_seconds) * 100.0))
                    now = time.time()
                    elapsed = now - start_time
                    eta = None
                    if current_sec > 0 and elapsed > 0.5:
                        speed = current_sec / elapsed
                        remaining_sec = max(0.0, target_seconds - current_sec)
                        eta = remaining_sec / speed if speed > 0 else None

                    # Report every 0.5s or on progress
                    if on_progress and (now - last_report_time >= 0.5):
                        on_progress(round(pct, 1), round(eta, 1) if eta else None)
                        last_report_time = now
                except (ValueError, TypeError):
                    pass

        returncode = process.wait()
        if returncode != 0:
            stderr_out = process.stderr.read() if process.stderr else ""
            raise FFmpegPipelineError(f"FFmpeg execution failed with code {returncode}: {stderr_out[-500:]}")

    except Exception:
        kill_process_group(process.pid)
        raise
    finally:
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()


def run_pipeline(
    input_path: Union[str, Path],
    final_output_path: Union[str, Path],
    target_seconds: float,
    mode: str = "fast",
    temp_dir_base: Optional[Union[str, Path]] = None,
    on_progress: Optional[Callable[[float, Optional[float]], None]] = None,
    on_pid: Optional[Callable[[int], None]] = None,
    timeout_seconds: int = 7200,
    check_cancelled: Optional[Callable[[], bool]] = None,
) -> Tuple[str, float]:
    """
    Run full 5-step FFmpeg looping pipeline.
    Returns (actual_mode_used, output_duration).
    Automatically falls back to safe mode if fast mode fails.
    """
    input_str = str(input_path)
    final_output_str = str(final_output_path)
    os.makedirs(os.path.dirname(final_output_str), exist_ok=True)

    # Step 1: Probe exact video-stream duration V and validate
    info = probe_media(input_str)
    validate_clip(info)
    v_duration = info.video_duration
    loops = calculate_loop_count(target_seconds, v_duration)
    logger.info(f"Video duration V={v_duration:.4f}s, target={target_seconds:.2f}s, loops={loops}")

    # Create isolated temp folder for this run
    temp_dir = tempfile.mkdtemp(prefix="loopforge_job_", dir=str(temp_dir_base) if temp_dir_base else None)

    try:
        def try_steps(current_mode: str) -> float:
            video_only_path = os.path.join(temp_dir, f"video_only_{current_mode}.mp4")
            audio_wav_path = os.path.join(temp_dir, "audio.wav")
            candidate_output_path = os.path.join(temp_dir, f"candidate_{current_mode}.mp4")

            # Step 2: Extract video-only
            if not os.path.exists(video_only_path):
                cmd_v = build_video_only_cmd(input_str, video_only_path, mode=current_mode)
                execute_step_with_progress(
                    cmd_v,
                    target_seconds=v_duration,
                    on_pid=on_pid,
                    timeout_seconds=300,
                    check_cancelled=check_cancelled
                )

            # Step 3: Extract audio to PCM WAV 48kHz stereo, trimmed/padded to V
            if not os.path.exists(audio_wav_path):
                cmd_a = build_audio_wav_cmd(input_str, audio_wav_path, duration=v_duration)
                execute_step_with_progress(
                    cmd_a,
                    target_seconds=v_duration,
                    on_pid=on_pid,
                    timeout_seconds=300,
                    check_cancelled=check_cancelled
                )

            # Step 4: Loop both inputs and encode audio once at the end
            cmd_merge = build_loop_merge_cmd(
                video_only_path,
                audio_wav_path,
                candidate_output_path,
                loops=loops,
                target_seconds=target_seconds,
                mode=current_mode
            )
            execute_step_with_progress(
                cmd_merge,
                target_seconds=target_seconds,
                on_progress=on_progress,
                on_pid=on_pid,
                timeout_seconds=timeout_seconds,
                check_cancelled=check_cancelled
            )

            # Step 5: Verify output
            is_valid, out_dur, error_reason = verify_output_duration(candidate_output_path, target_seconds)
            if not is_valid:
                raise FFmpegVerificationError(f"Verification failed in {current_mode} mode: {error_reason}")

            # Copy to final output path atomically
            shutil.move(candidate_output_path, final_output_str)
            return out_dur

        # Attempt in requested mode
        actual_mode = mode
        try:
            out_dur = try_steps(mode)
            if on_progress:
                on_progress(100.0, 0.0)
            return actual_mode, out_dur
        except (FFmpegPipelineError, FFmpegVerificationError) as e:
            if mode == "fast":
                logger.warning(f"Fast mode failed ({e}), falling back to Safe mode (re-encoding video)...")
                actual_mode = "safe"
                out_dur = try_steps("safe")
                if on_progress:
                    on_progress(100.0, 0.0)
                return actual_mode, out_dur
            else:
                raise

    finally:
        # Clean up temp folder completely
        if os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception as e:
                logger.warning(f"Failed to remove temp dir {temp_dir}: {e}")
