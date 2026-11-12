#!/usr/bin/env bash
set -e

echo "=== Setting up FORGE IT ==="

# Backend setup
if [ -d "backend" ]; then
  echo "Setting up backend Python environment..."
  python3 -m venv backend/venv
  source backend/venv/bin/activate
  if [ -f "backend/requirements.txt" ]; then
    pip install -r backend/requirements.txt
  fi
fi

# Frontend setup
if [ -d "frontend" ]; then
  echo "Installing frontend dependencies..."
  cd frontend
  if [ -f "package.json" ]; then
    npm install
  fi
  cd ..
fi

echo "=== Setup completed successfully ==="

