# GPU Video Render Service (FastAPI + FFmpeg)

## Build
docker build -t video-server:latest .

## Run (CPU)
docker run --rm -p 8080:8080 \
  -e OUTPUT_DIR=/tmp/outputs \
  -v "$PWD/outputs":/tmp/outputs \
  video-server:latest

## Run (GPU / NVENC)
# Requires NVIDIA Container Toolkit on host
docker run --rm --gpus all -p 8080:8080 \
  -e OUTPUT_DIR=/tmp/outputs \
  -v "$PWD/outputs":/tmp/outputs \
  video-server:latest


## Env (optional)
- S3_BUCKET, S3_REGION, S3_PREFIX, PUBLIC_BASE_URL  # enable upload to S3

## API
POST /render
{
  "payload_url": "https://your-cdn/payload.json",
  "output_filename": "demo.mp4"
}
or
{
  "payload": { ... your JSON ... },
  "output_filename": "demo.mp4"
}

GET /jobs/{id}   # -> { status, output_url, logs? }
GET /healthz
