#!/usr/bin/env bash

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Starting Backend, Web, and Telegram Bot..."

cleanup() {
    echo -e "\nStopping all services..."
    kill "$BACKEND_PID" "$WEB_PID" "$BOT_PID" 2>/dev/null
    wait "$BACKEND_PID" "$WEB_PID" "$BOT_PID" 2>/dev/null
    echo "Services stopped."
    exit 0
}

trap cleanup SIGINT SIGTERM

# 1. Start FastAPI Backend
cd "$ROOT_DIR/backend" || exit 1
source venv/bin/activate
uvicorn main:app --port 8000 --reload &
BACKEND_PID=$!
echo "Backend running on http://localhost:8000"

# 2. Start Vite Web Client
cd "$ROOT_DIR/web" || exit 1
npm run dev &
WEB_PID=$!
echo "Web client running on http://localhost:5173"

# 3. Start Telegram Bot
cd "$ROOT_DIR/backend" || exit 1
"$ROOT_DIR/backend/venv/bin/python3" bot.py &
BOT_PID=$!
echo "Telegram Bot running in background"

wait