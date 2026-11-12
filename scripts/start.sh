#!/usr/bin/env bash
set -e

echo "=== Starting FORGE IT services ==="

# Run backend in background
if [ -d "backend" ]; then
  echo "Starting backend server on port 8000..."
  (
    source backend/venv/bin/activate 2>/dev/null || true
    cd backend
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
  ) &
fi

# Run frontend in foreground
if [ -d "frontend" ]; then
  echo "Starting frontend dev server..."
  cd frontend
  npm run dev
fi

