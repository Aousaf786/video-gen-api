# Base with CUDA runtime (adjust to your GPU driver compatibility)
FROM nvidia/cuda:12.3.2-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

# System deps (FFmpeg, Python, fonts)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg python3 python3-pip ca-certificates fonts-dejavu-core curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps
COPY requirements.txt /app/
RUN pip3 install --no-cache-dir -r requirements.txt

# App code
COPY app /app/app

# Non-root (optional)
RUN useradd -m runner && chown -R runner:runner /app
USER runner

ENV PORT=8080 \
    OUTPUT_DIR=/tmp/outputs

EXPOSE 8080

# Uvicorn server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
