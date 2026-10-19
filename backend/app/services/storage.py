import os
import shutil
from pathlib import Path
from typing import Tuple, Optional, Generator, BinaryIO
from fastapi import UploadFile, HTTPException, status
from app.core.config import settings


def get_disk_usage(path: Optional[str] = None) -> Tuple[int, int, int]:
    """Return (total_bytes, used_bytes, free_bytes) for the given path."""
    target_path = path or settings.STORAGE_DIR
    os.makedirs(target_path, exist_ok=True)
    usage = shutil.disk_usage(target_path)
    return usage.total, usage.used, usage.free


def estimate_output_size(input_file_size: int, input_duration: float, target_duration: float) -> int:
    """Estimate the resulting output size in bytes based on input bitrate and target duration."""
    if input_duration <= 0:
        # Default fallback: 4 Mbps video + 192k audio ~= 524 KB/s
        bytes_per_sec = 524_000
    else:
        bytes_per_sec = input_file_size / input_duration

    # 15% safety margin + 10MB minimum
    estimated = int(bytes_per_sec * target_duration * 1.15)
    return max(10 * 1024 * 1024, estimated)


def check_disk_space_available(estimated_output_bytes: int) -> bool:
    """Check if free disk space is enough for estimated output + 500MB safety buffer."""
    _, _, free_bytes = get_disk_usage()
    buffer_bytes = 500 * 1024 * 1024  # 500 MB
    return free_bytes >= (estimated_output_bytes + buffer_bytes)


async def stream_upload_to_disk(upload_file: UploadFile, destination_path: Path, max_bytes: int) -> int:
    """
    Stream upload directly to disk in 1MB chunks without holding the full file in memory.
    Raises 413 if file exceeds max_bytes.
    """
    total_bytes = 0
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(destination_path, "wb") as buffer:
            while chunk := await upload_file.read(1024 * 1024):  # 1 MB chunks
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"File exceeds maximum allowed size of {max_bytes // (1024 * 1024)} MB."
                    )
                buffer.write(chunk)
    except Exception:
        # If interrupted or errored, remove partially uploaded file
        if destination_path.exists():
            try:
                destination_path.unlink()
            except OSError:
                pass
        raise

    return total_bytes


def parse_range_header(range_header: str, file_size: int) -> Tuple[int, int]:
    """Parse HTTP Range header e.g. 'bytes=0-1024' or 'bytes=500-'."""
    if not range_header or not range_header.startswith("bytes="):
        return 0, file_size - 1

    range_val = range_header.replace("bytes=", "").strip()
    parts = range_val.split("-")
    start_str, end_str = parts[0], parts[1] if len(parts) > 1 else ""

    if start_str and end_str:
        start = int(start_str)
        end = int(end_str)
    elif start_str:
        start = int(start_str)
        end = file_size - 1
    elif end_str:
        end = file_size - 1
        start = max(0, file_size - int(end_str))
    else:
        start = 0
        end = file_size - 1

    start = max(0, min(start, file_size - 1))
    end = max(start, min(end, file_size - 1))
    return start, end


def range_file_iterator(file_path: Path, start: int, end: int, chunk_size: int = 64 * 1024) -> Generator[bytes, None, None]:
    """Yield bytes in chunks from start to end inclusive."""
    with open(file_path, "rb") as f:
        f.seek(start)
        remaining = (end - start) + 1
        while remaining > 0:
            read_size = min(chunk_size, remaining)
            data = f.read(read_size)
            if not data:
                break
            remaining -= len(data)
            yield data
