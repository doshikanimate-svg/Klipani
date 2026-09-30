#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ ! -d "$ROOT_DIR/.venv" ]]; then
  echo "Не найдено Python-окружение .venv. См. README.md, раздел Установка."
  exit 1
fi
if [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
  echo "Не найдены frontend-зависимости. Выполните: cd frontend && npm install"
  exit 1
fi
source "$ROOT_DIR/.venv/bin/activate"
cd "$ROOT_DIR"
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!
trap 'kill "$BACKEND_PID" 2>/dev/null || true' EXIT INT TERM
cd "$ROOT_DIR/frontend"
echo "Frontend: http://localhost:3000"
echo "Backend:  http://localhost:8000"
npm run dev
