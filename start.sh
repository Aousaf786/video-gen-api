#!/bin/bash
set -e

# Start nginx in the background
nginx -g "daemon off;" &

# Start uvicorn in the foreground
exec uvicorn app.main:app --host 0.0.0.0 --port 8080