#!/bin/bash

# Set default port if not provided
PORT=${PORT:-8000}

# Debug output
echo "=== Starting Scrapping Service ==="
echo "PORT: $PORT"
echo "HOST: 0.0.0.0"
echo "==============================="

# Start uvicorn
exec uvicorn main:app --host 0.0.0.0 --port "$PORT"