#!/usr/bin/env bash
# BeoSound 5c UI Service
# Runs Chromium in kiosk mode with crash recovery

# Kill potential conflicting X instances
sudo pkill X || true

# Clear Chromium cache before starting (prevents stale state)
rm -rf ~/.cache/chromium/Default/Cache/*
rm -rf ~/.cache/chromium/Default/Code\ Cache/*
rm -rf ~/.cache/chromium/Default/Service\ Worker/*
rm -rf ~/.config/chromium/Singleton*

# Also clear any crash recovery state that might show dialogs
rm -rf ~/.config/chromium/Default/Preferences.bak
rm -rf ~/.config/chromium/Default/Session*
rm -rf ~/.config/chromium/Default/Current*

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "=== BeoSound 5c UI Service Starting ==="

# Start X with a wrapper that includes crash recovery
xinit /bin/bash -c '
  # Hide cursor
  unclutter -idle 0.1 -root &

  # Disable screen blanking within X session
  xset s off
  xset s noblank
  xset -dpms

  log() {
    echo "[$(date "+%Y-%m-%d %H:%M:%S")] $*"
  }

  log "X session started, launching Chromium with crash recovery..."

  # Crash recovery loop - restart Chromium if it exits
  CRASH_COUNT=0
  MAX_CRASHES=10
  CRASH_RESET_TIME=300  # Reset crash count after 5 minutes of stability

  while true; do
    START_TIME=$(date +%s)
    log "Starting Chromium (crash count: $CRASH_COUNT)"

    /usr/bin/chromium-browser \
      --force-dark-mode \
      --enable-features=WebUIDarkMode \
      --disable-application-cache \
      --disable-cache \
      --disable-offline-load-stale-cache \
      --disk-cache-size=0 \
      --media-cache-size=0 \
      --kiosk \
      --app=http://localhost:8000 \
      --start-fullscreen \
      --window-size=1024,768 \
      --window-position=0,0 \
      --noerrdialogs \
      --disable-infobars \
      --disable-translate \
      --disable-session-crashed-bubble \
      --disable-features=TranslateUI \
      --no-first-run \
      --disable-default-apps \
      --disable-component-extensions-with-background-pages \
      --disable-background-networking \
      --disable-sync \
      --ignore-certificate-errors \
      --disable-features=IsolateOrigins,site-per-process \
      --disable-extensions \
      --disable-dev-shm-usage \
      --enable-features=OverlayScrollbar \
      --overscroll-history-navigation=0 \
      --disable-features=MediaRouter \
      --disable-features=InfiniteSessionRestore \
      --disable-pinch \
      --disable-gesture-typing \
      --disable-hang-monitor \
      --disable-prompt-on-repost

    EXIT_CODE=$?
    END_TIME=$(date +%s)
    RUN_TIME=$((END_TIME - START_TIME))

    log "Chromium exited with code $EXIT_CODE after ${RUN_TIME}s"

    # If it ran for more than CRASH_RESET_TIME, reset crash count
    if [ $RUN_TIME -gt $CRASH_RESET_TIME ]; then
      CRASH_COUNT=0
      log "Stable run, reset crash count"
    else
      CRASH_COUNT=$((CRASH_COUNT + 1))
      log "Quick exit, crash count now: $CRASH_COUNT"
    fi

    # If too many crashes, wait longer before restart
    if [ $CRASH_COUNT -ge $MAX_CRASHES ]; then
      log "Too many crashes ($CRASH_COUNT), waiting 60s before restart..."
      sleep 60
      CRASH_COUNT=0
    else
      # Brief delay before restart
      sleep 2
    fi

    # Clear any crash state before restarting
    rm -rf ~/.config/chromium/Default/Session* 2>/dev/null
    rm -rf ~/.config/chromium/Singleton* 2>/dev/null

    log "Restarting Chromium..."
  done
' -- :0
