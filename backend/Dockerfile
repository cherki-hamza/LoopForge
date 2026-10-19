FROM python:3.12-slim

# Install system dependencies including FFmpeg and ffprobe
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Verify ffmpeg & ffprobe installations
RUN ffmpeg -version && ffprobe -version

WORKDIR /app

# Copy dependencies first for Docker caching
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . /app

# Ensure storage directories exist
RUN mkdir -p /app/storage/uploads /app/storage/outputs /app/storage/temp

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
