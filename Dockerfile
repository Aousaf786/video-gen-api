# Base with CUDA runtime (adjust to your GPU driver compatibility)
FROM nvidia/cuda:12.3.2-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

# System deps (FFmpeg, Python, fonts)
RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx ffmpeg python3 python3-pip ca-certificates fonts-dejavu-core curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Ensure persistent assets directory exists
RUN mkdir -p /workspace/assets

# Python deps
COPY requirements.txt /app/
RUN pip3 install --no-cache-dir -r requirements.txt

# App code
COPY app /app/app
COPY assets /app/assets

# Nginx config (replaces default site config)
COPY nginx.conf /etc/nginx/sites-enabled/default

# Startup script
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh


ENV PORT=8080 \
    OUTPUT_DIR=/workspace/assets/

EXPOSE 8080 80


# Uvicorn server
CMD ["/app/start.sh"]
