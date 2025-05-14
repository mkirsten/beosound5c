#!/bin/bash

BASE_DIR=~/beosound5c
LOG_DIR=/dev/shm/beosound5c
mkdir -p "$LOG_DIR"

echo "🔪 Killing old processes..."
sudo pkill -f "python3 .*server.py" || true
sudo pkill -f "python3 .*sniffer.py" || true
sleep 2
sudo pkill -9 -f "python3 .*server.py" || true
sudo pkill -9 -f "python3 .*sniffer.py" || true

echo "🧪 Post-kill process list:"
pgrep -af python || echo "✅ Clean"

echo "📥 Pulling latest code..."
cd "$BASE_DIR" || exit 1
git pull > "$LOG_DIR/git.log" 2>&1

echo "🌐 Starting HTTP server..."
cd "$BASE_DIR/web" || exit 1
python3 -m http.server 8000 >> "$LOG_DIR/http.log" 2>&1 &
sleep 1

echo "🔧 Starting server.py..."
cd "$BASE_DIR/hw" || exit 1
if ! pgrep -f "python3 .*server.py" > /dev/null; then
  sudo python3 server.py >> "$LOG_DIR/server.log" 2>&1 &
fi
sleep 1

echo "🛰️  Starting sniffer.py..."
if ! pgrep -f "python3 .*sniffer.py" > /dev/null; then
  sudo python3 sniffer.py >> "$LOG_DIR/sniffer.log" 2>&1 &
fi
sleep 1

echo "⌛ Launching web.sh..."
sleep 1

echo "🖥️  Starting web.sh..."
cd "$BASE_DIR/bin" || exit 1
./web.sh > "$LOG_DIR/websh.log" 2>&1 &
