#!/bin/bash
set -e

PORT=${PORT:-8000}
echo "Starting uvicorn on port $PORT"

exec uvicorn main:app --host 0.0.0.0 --port $PORT
