#!/bin/bash

BASE_DIR=~/beosound5c
LOG_DIR=/dev/shm/beosound5c

# Create log folder in memory
mkdir -p "$LOG_DIR"

echo "🔪 Killing old processes..."

# Kill chromium immediately
sudo pkill -9 chromium || true

# Nicely stop python3 processes
sudo pkill -15 python3 || true
sleep 5
# Force-kill any remaining
sudo pkill -f sniffer.py
sudo pkill -f server.py
sudo pkill -9 python3 || true

#echo "🚀 Starting X"
#startx > "$LOG_DIR/startx.log" 2>&1 &

# Wait for X to come up
#sleep 5

echo "📥 Pulling latest code..."
cd "$BASE_DIR" || exit 1
git pull > "$LOG_DIR/git.log" 2>&1

echo "🌐 Starting HTTP server..."
cd "$BASE_DIR/web" || exit 1
python3 -m http.server 8000 >> "$LOG_DIR/http.log" 2>&1 &

echo "🔧 Starting server.py..."
cd "$BASE_DIR/hw" || exit 1
sudo python3 server.py >> "$LOG_DIR/server.log" 2>&1 &

echo "🛰️  Starting sniffer.py..."
sudo python3 sniffer.py >> /dev/shm/beosound5c/sniffer.log 2>&1 &

echo "⌛ Waiting 5 seconds before launching web.sh..."
sleep 5

echo "🖥️  Starting web.sh..."
cd "$BASE_DIR/bin" || exit 1
./web.sh > "$LOG_DIR/websh.log" 2>&1 &

echo "✅ All processes started. Logs in $LOG_DIR"
