# LoopForge 🎬 🔄

> High-performance video looping engine for creating long, seamless ambient and meditation videos (30 minutes to several hours) from short animations.

---

## Architecture & Technology Stack

- **Backend**: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0, PostgreSQL 16, Celery 5 + Redis 7.
- **Frontend**: Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS v4, Lucide Icons.
- **FFmpeg Engine**: Subprocess process-group isolation, stream-looping with exact PCM WAV synchronization.
- **Infrastructure**: Docker Compose (`api`, `worker`, `beat`, `redis`, `postgres`, `web`).

---

## Seamless Looping Pipeline (Zero Click, Zero Drift)

Gaps and clicks in standard video looping stem from AAC encoder padding, non-integral audio/video frame durations, and re-encoding artifacts accumulated over dozens of iterations. LoopForge implements a five-step pipeline:

1. **Exact Stream Probing (`ffprobe`)**:
   Measures exact video stream duration $V$ down to microseconds and determines loop count:
   $$\text{loops} = \left\lceil \frac{\text{target\_seconds}}{V} \right\rceil$$
2. **Video Extraction**:
   Extracts video-only track without audio:
   ```bash
   ffmpeg -i input.mp4 -an -c:v copy video_only.mp4
   ```
3. **Exact PCM Audio Trim & Pad**:
   Extracts audio to uncompressed PCM WAV 48 kHz stereo, trimmed and padded to exactly match $V$:
   ```bash
   ffmpeg -i input.mp4 -vn -af "atrim=0:V,apad=whole_dur=V" -ar 48000 -ac 2 -c:a pcm_s16le audio.wav
   ```
4. **Synchronized Stream Loop & Single AAC Encode**:
   Loops both inputs independently using `-stream_loop (loops - 1)` and encodes audio only once:
   ```bash
   ffmpeg -stream_loop (loops-1) -i video_only.mp4 -stream_loop (loops-1) -i audio.wav -map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k -t TARGET -movflags +faststart -progress pipe:1 -nostats output.mp4
   ```
5. **Output Verification**:
   Validates with `ffprobe` that both streams exist and output duration is within $\pm 1.0$ second of target. Automatically falls back from **Fast Mode** (`-c:v copy`) to **Safe Mode** (`-c:v libx264 -crf 18 -preset veryfast -pix_fmt yuv420p`) if stream-copy fails verification.

---

## Getting Started

### 1. Prerequisites
- [Docker](https://docs.docker.com/get-docker/) & [Docker Compose](https://docs.docker.com/compose/)
- Alternatively for local dev: Python 3.11+, Node.js 18+, FFmpeg 6+, PostgreSQL, Redis.

### 2. Configuration
Copy the environment template:
```bash
cp .env.example .env
```
Default admin credentials:
- **Username**: `admin`
- **Password**: `loopforge2026`

### 3. Start the Stack with Docker Compose
```bash
docker compose up --build -d
```
All services will start up:
- **Frontend Dashboard**: [http://localhost:3001](http://localhost:3001) (or port configured via `WEB_PORT` in `.env`)
- **Backend API Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health Check**: [http://localhost:8000/api/health](http://localhost:8000/api/health)


To view logs:
```bash
docker compose logs -f api worker
```

To stop:
```bash
docker compose down
```

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/auth/login` | Authenticate single admin (sets httpOnly JWT cookie) |
| `POST` | `/api/auth/logout` | Clear authentication cookie |
| `GET` | `/api/auth/me` | Current authenticated session details |
| `POST` | `/api/jobs` | Stream upload video clip, validate, and enqueue job |
| `GET` | `/api/jobs` | List jobs with status filter, search, and pagination |
| `GET` | `/api/jobs/{id}` | Get status, live progress, and queue position |
| `POST` | `/api/jobs/{id}/cancel` | Kill process group & revoke Celery task |
| `POST` | `/api/jobs/{id}/retry` | Retry failed or cancelled job |
| `DELETE` | `/api/jobs/{id}` | Delete job and disk files |
| `POST` | `/api/jobs/batch-delete` | Batch delete selected jobs |
| `GET` | `/api/jobs/{id}/stream` | In-browser preview streaming (HTTP Range support) |
| `GET` | `/api/jobs/{id}/download` | Direct file download (HTTP Range support) |
| `GET` | `/api/system` | Free disk space and job queue counts |
| `GET` | `/api/health` | Healthcheck (PostgreSQL + Redis status) |

---

## Automated Retention Cleanup
A scheduled Celery Beat task runs periodically to remove uploads and outputs older than `RETENTION_DAYS` (default 14 days) to prevent disk exhaustion.

---

## Running Backend Tests Locally
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest tests/ -v
```
