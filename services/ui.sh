#!/usr/bin/env bash
# BeoSound 5c UI Service
# Runs Chromium in kiosk mode with crash recovery

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPLASH_IMAGE="${SCRIPT_DIR}/../assets/splashscreen-red.png"
export SPLASH_IMAGE  # Export for xinit subshell

# Kill potential conflicting X instances
sudo pkill X || true

# Note: Plymouth handles boot splash now (see /usr/share/plymouth/themes/beosound5c)
# This fbi fallback only runs if Plymouth isn't active
if [ -f "$SPLASH_IMAGE" ] && command -v fbi &>/dev/null && ! pidof plymouthd &>/dev/null; then
  sudo pkill -9 fbi 2>/dev/null || true
  sudo fbi -T 1 -d /dev/fb0 --noverbose -a "$SPLASH_IMAGE" &>/dev/null &
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Splash screen displayed (fbi fallback)"
fi

# Clear Chromium cache before starting (prevents stale state)
rm -rf ~/.cache/chromium/Default/Cache/*
rm -rf ~/.cache/chromium/Default/Code\ Cache/*
rm -rf ~/.cache/chromium/Default/Service\ Worker/*
rm -rf ~/.config/chromium/Singleton*

# Also clear any crash recovery state that might show dialogs
rm -rf ~/.config/chromium/Default/Preferences.bak
rm -rf ~/.config/chromium/Default/Session*
rm -rf ~/.config/chromium/Default/Current*
rm -rf ~/.config/chromium/Crash\ Reports/pending/*

# Patch Chromium preferences to disable crash restore
PREFS_FILE="$HOME/.config/chromium/Default/Preferences"
if [ -f "$PREFS_FILE" ]; then
  # Set exit_type to Normal and exited_cleanly to true
  sed -i 's/"exit_type":"[^"]*"/"exit_type":"Normal"/g; s/"exited_cleanly":false/"exited_cleanly":true/g' "$PREFS_FILE" 2>/dev/null || true
fi

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "=== BeoSound 5c UI Service Starting ==="

# Tell Plymouth to quit but retain the splash image on framebuffer
# This keeps the splash visible until X/Chromium draws over it
if pidof plymouthd &>/dev/null; then
  log "Telling Plymouth to quit with retained splash..."
  sudo plymouth quit --retain-splash || true
fi

# Start X with a wrapper that includes crash recovery
xinit /bin/bash -c '
  # Kill fbi if running (Plymouth already quit with retain-splash)
  sudo pkill -9 fbi 2>/dev/null || true

  # Set X root window to splash image immediately (fills gap while Chromium loads)
  # SPLASH_IMAGE is exported from parent script
  if [ -f "$SPLASH_IMAGE" ] && command -v feh &>/dev/null; then
    feh --bg-scale "$SPLASH_IMAGE" 2>/dev/null &
  fi

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

  # Wait for HTTP server to be ready
  log "Waiting for HTTP server..."
  for i in {1..30}; do
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/ | grep -q "200"; then
      log "HTTP server ready"
      break
    fi
    sleep 0.5
  done

  # Crash recovery loop - restart Chromium if it exits
  CRASH_COUNT=0
  MAX_CRASHES=10
  CRASH_RESET_TIME=300  # Reset crash count after 5 minutes of stability
  REBOOT_THRESHOLD=5    # Reboot system after this many consecutive window failures
  WINDOW_FAIL_COUNT=0

  while true; do
    START_TIME=$(date +%s)
    log "Starting Chromium (crash count: $CRASH_COUNT)"

    # Start window health check in background
    (
      sleep 15  # Give Chromium time to start
      # Check if a real Chromium window exists (not just clipboard)
      if ! xwininfo -root -tree 2>/dev/null | grep -q "Beosound\|localhost"; then
        log "No Chromium window detected after 15s, killing to trigger restart..."

        # Track window failures in a file (persists across restarts)
        FAIL_FILE="/tmp/beo-ui-window-failures"
        if [ -f "$FAIL_FILE" ]; then
          WINDOW_FAIL_COUNT=$(cat "$FAIL_FILE")
        else
          WINDOW_FAIL_COUNT=0
        fi
        WINDOW_FAIL_COUNT=$((WINDOW_FAIL_COUNT + 1))
        echo "$WINDOW_FAIL_COUNT" > "$FAIL_FILE"

        log "Window failure count: $WINDOW_FAIL_COUNT / 5"

        if [ "$WINDOW_FAIL_COUNT" -ge 5 ]; then
          log "Too many window failures, rebooting system..."
          rm -f "$FAIL_FILE"
          sudo reboot
        else
          pkill -9 chromium
        fi
      else
        # Window appeared successfully, reset failure count
        rm -f /tmp/beo-ui-window-failures
      fi
    ) &

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
      --disable-prompt-on-repost \
      --hide-crash-restore-bubble \
      --disable-breakpad \
      --disable-crash-reporter

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
    rm -rf ~/.config/chromium/Crash\ Reports/pending/* 2>/dev/null

    # Patch preferences to prevent crash restore dialog
    PREFS="$HOME/.config/chromium/Default/Preferences"
    if [ -f "$PREFS" ]; then
      sed -i "s/\"exit_type\":\"[^\"]*\"/\"exit_type\":\"Normal\"/g; s/\"exited_cleanly\":false/\"exited_cleanly\":true/g" "$PREFS" 2>/dev/null || true
    fi

    log "Restarting Chromium..."
  done
' -- :0
