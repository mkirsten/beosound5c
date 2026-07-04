#!/usr/bin/env bash
# BeoSound 5c UI Service
# Runs Chromium in kiosk mode with crash recovery

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPLASH_IMAGE="${SCRIPT_DIR}/../plymouth/splashscreen-red.png"
export SPLASH_IMAGE  # Export for xinit subshell

# Fast shutdown on SIGTERM/SIGINT — Chromium kiosk has no state to flush,
# and `kill 0; wait` used to hang the cron-restart until systemd's
# TimeoutStopSec expired and SIGKILLed the cgroup anyway.  Kill X/xinit/
# Chromium by name first: Xorg setsid()s and Chromium spawns its own
# process groups, so a group-kill alone leaves them alive and systemd
# still waits out TimeoutStopSec (unit ends "failed (timeout)").  The
# final group-kill takes down this shell itself — SuccessExitStatus=KILL
# in beo-ui.service marks that as a clean exit.
trap 'pkill -KILL -f chromium 2>/dev/null; pkill -KILL xinit 2>/dev/null; pkill -KILL Xorg 2>/dev/null; kill -KILL 0 2>/dev/null; exit 0' SIGTERM SIGINT

# Kill potential conflicting X instances
sudo pkill X || true

# Note: Plymouth handles boot splash now (see /usr/share/plymouth/themes/beosound5c)
# This fbi fallback only runs if Plymouth isn't active
if [ -f "$SPLASH_IMAGE" ] && command -v fbi &>/dev/null && ! pidof plymouthd &>/dev/null; then
  sudo pkill -9 fbi 2>/dev/null || true
  sudo fbi -T 1 -d /dev/fb0 --noverbose -a "$SPLASH_IMAGE" &>/dev/null &
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Splash screen displayed (fbi fallback)"
fi

# Chromium profile — kept on SD so cookies and login state survive reboots.
CHROMIUM_DATA_DIR="$HOME/.config/chromium"
export CHROMIUM_DATA_DIR  # Export for xinit subshell
# If the profile dir is a symlink (e.g. sd-hardening points it to tmpfs),
# recreate the symlink target on each boot — tmpfs is wiped on power cycle.
if [ -L "$CHROMIUM_DATA_DIR" ]; then
  mkdir -p "$(readlink "$CHROMIUM_DATA_DIR")/Default"
else
  mkdir -p "$CHROMIUM_DATA_DIR/Default"
fi

# Redirect GPU/shader/code caches from SD to tmpfs.
# HTTP cache is already disabled via --disable-cache / --disk-cache-size=0.
# Symlinks persist on SD; /tmp target dirs are recreated here each boot.
_cache_to_tmp() {
  local src="$1" dst="$2"
  mkdir -p "$dst"
  if [ ! -L "$src" ]; then
    rm -rf "$src"
    ln -s "$dst" "$src"
  fi
}
_cache_to_tmp "$CHROMIUM_DATA_DIR/GrShaderCache"        /tmp/chromium-gr-shader
_cache_to_tmp "$CHROMIUM_DATA_DIR/ShaderCache"          /tmp/chromium-shader
_cache_to_tmp "$CHROMIUM_DATA_DIR/GraphiteDawnCache"    /tmp/chromium-graphite
_cache_to_tmp "$CHROMIUM_DATA_DIR/Default/GPUCache"     /tmp/chromium-gpu-cache
_cache_to_tmp "$CHROMIUM_DATA_DIR/Default/Code Cache"   /tmp/chromium-code-cache
unset -f _cache_to_tmp

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "=== BeoSound 5c UI Service Starting ==="

# Tell Plymouth to quit but retain the splash image on framebuffer
# This keeps the splash visible until X/Chromium draws over it
if pidof plymouthd &>/dev/null; then
  log "Telling Plymouth to quit with retained splash..."
  sudo plymouth quit --retain-splash || true
  # Wait for Plymouth to fully release the framebuffer before X starts.
  # Without this, X can fail with "Cannot run in framebuffer mode" because
  # Plymouth still holds the display lock.
  sleep 1
fi

# Check that a DRM/KMS device is available before starting X.
# If /dev/dri/card0 is missing, vc4-kms-v3d is likely not loaded in
# /boot/firmware/config.txt — X will fail with a framebuffer mode error.
if ! ls /dev/dri/card* &>/dev/null; then
  log "ERROR: No DRM device found at /dev/dri/card*. X will fail."
  log "Fix: ensure 'dtoverlay=vc4-kms-v3d' is set in /boot/firmware/config.txt"
  log "Also check: sudo usermod -aG video,render \$USER && reboot"
  exit 1
fi

# Outer loop — restart the entire X session if it crashes.
# xinit exits when its client (bash) exits, so any clean exit from the inner
# script (Chromium crash loop) lands here. We sleep briefly and try again.
# The VT-failure reboot and SIGTERM shutdown are handled inside the loop.
#
# xinit runs in the BACKGROUND with a `wait` — bash defers trap execution
# until a foreground command completes, so with xinit in the foreground
# the SIGTERM trap above never ran and every stop waited out systemd's
# TimeoutStopSec before being SIGKILLed.  `wait` is interruptible, so
# backgrounding is what actually makes the fast-shutdown trap work.
while true; do
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

  # Disable BeoRemote pointer devices - they generate unwanted mouse events
  # that make the cursor flash visible. Keyboard devices are separate and
  # remain active. The xorg rule (20-beorc-no-pointer.conf) handles this
  # permanently, but this catches cases where the rule is missing.
  (
    sleep 3  # Wait for X input devices to register
    for id in $(xinput list 2>/dev/null | grep -i "BEORC" | grep "slave  pointer" | grep -oP "id=\K\d+"); do
      xinput float "$id" 2>/dev/null && echo "Floated BEORC pointer device id=$id"
    done
  ) &

  # Disable screen blanking within X session
  xset s off
  xset s noblank
  xset -dpms

  log() {
    echo "[$(date "+%Y-%m-%d %H:%M:%S")] $*"
  }

  # Honor the pre-restart screen state.  X always re-enables HDMI on
  # session start, but if beo-input had backlight off (e.g. HA fired
  # screen_off before our nightly cron-restart), turn HDMI back off so
  # the user does not see a 10-minute window of lit screen at 03:00.
  (
    sleep 2  # wait for X to settle and beo-input HTTP to be reachable
    SCREEN_STATE=$(curl -s --max-time 1 http://localhost:8767/health 2>/dev/null \
      | grep -oE "\"screen\"\\s*:\\s*\"(on|off)\"" \
      | grep -oE "(on|off)$")
    if [ "$SCREEN_STATE" = "off" ]; then
      log "beo-input reports screen=off — re-applying xrandr --off"
      xrandr --output HDMI-1 --off 2>/dev/null
    fi
  ) &

  # Resolution watchdog — HDMI sometimes renegotiates to the lowest mode
  # (typically 320x200) after a power cycle or display sleep, leaving the
  # UI rendered into a tiny viewport. Check every 30s; if HDMI-1 is on
  # but not at 1024x768, force the mode and kick Chromium so it re-lays
  # out. Skips when beo-input has the output deliberately --off.
  (
    while true; do
      sleep 30
      HDMI_LINE=$(xrandr --query 2>/dev/null | grep "^HDMI-1 ")
      # Skip if output is intentionally off (no "WxH+X+Y" geometry).
      echo "$HDMI_LINE" | grep -qE "[0-9]+x[0-9]+\+[0-9]+\+[0-9]+" || continue
      # Skip if already at 1024x768.
      echo "$HDMI_LINE" | grep -q " 1024x768+" && continue
      CURRENT=$(echo "$HDMI_LINE" | grep -oE "[0-9]+x[0-9]+\+[0-9]+\+[0-9]+" | head -1)
      log "Resolution watchdog: HDMI-1 at $CURRENT, forcing 1024x768"
      if xrandr --output HDMI-1 --mode 1024x768 --rate 60 2>/dev/null; then
        sleep 1
        pkill -9 chromium 2>/dev/null
      fi
    done
  ) &

  # Stop crash recovery loop on SIGTERM
  STOPPING=0
  trap "STOPPING=1; pkill -9 chromium 2>/dev/null; exit 0" SIGTERM SIGINT

  log "X session started, launching Chromium with crash recovery..."

  # Detect which port the HTTP server is on.
  # Port 80 is standard (v0.7.2+). Port 8000 is the legacy default — a device
  # upgrading from <0.7.2 via OTA will not have had its service file updated yet,
  # so it falls back gracefully without showing a black screen.
  HTTP_PORT=80
  log "Waiting for HTTP server..."
  for i in {1..30}; do
    if curl -s -o /dev/null -w "%{http_code}" http://localhost/ | grep -q "200"; then
      HTTP_PORT=80
      log "HTTP server ready (port 80)"
      break
    elif curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/ | grep -q "200"; then
      HTTP_PORT=8000
      log "HTTP server ready (port 8000 — legacy, service file not yet updated)"
      break
    fi
    sleep 0.5
  done

  # Wait for router to be ready (menu data comes from here)
  log "Waiting for router..."
  for i in {1..30}; do
    if curl -s -o /dev/null http://localhost:8770/router/menu 2>/dev/null; then
      log "Router ready"
      break
    fi
    [ "$i" -eq 30 ] && log "Router not ready after 15s, starting anyway"
    sleep 0.5
  done

  # Crash recovery loop - restart Chromium if it exits
  CRASH_COUNT=0
  MAX_CRASHES=10
  CRASH_RESET_TIME=300  # Reset crash count after 5 minutes of stability

  while true; do
    # Bail out if X died — outer loop will restart the full X session
    if ! xset q &>/dev/null; then
      log "X server is gone. Exiting inner loop to restart X..."
      exit 1
    fi

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

        log "Window failure count: $WINDOW_FAIL_COUNT"

        if [ "$WINDOW_FAIL_COUNT" -ge 5 ]; then
          log "Too many window failures, giving up (check journalctl -u beo-ui)"
          rm -f "$FAIL_FILE"
          # Show error on screen instead of rebooting
          xmessage -center "beo-ui: Chromium failed to create a window after 5 attempts. Check logs." 2>/dev/null &
          exit 1
        else
          pkill -9 chromium
        fi
      else
        # Window appeared successfully, reset failure count
        rm -f /tmp/beo-ui-window-failures
      fi
    ) &

    # Chromium binary: 'chromium-browser' (Bullseye) or 'chromium' (Bookworm+)
    CHROMIUM_BIN="/usr/bin/chromium-browser"
    [ -x "$CHROMIUM_BIN" ] || CHROMIUM_BIN="/usr/bin/chromium"

    # Wrap in dbus-run-session so Chromium has a session bus to talk to.
    # Without this, on trixie+ Chromium spams the journal every ~33s with
    # "Failed to connect to the bus: Could not parse server address"
    # from its GCM/notification subsystems.  No-op if dbus-run-session
    # is missing (older Debian, dev hosts).
    DBUS_WRAP=()
    if command -v dbus-run-session &>/dev/null; then
      DBUS_WRAP=(dbus-run-session --)
    fi

    "${DBUS_WRAP[@]}" "$CHROMIUM_BIN" \
      --user-data-dir="$CHROMIUM_DATA_DIR" \
      --force-dark-mode \
      --enable-features=WebUIDarkMode \
      --disable-application-cache \
      --disable-cache \
      --disable-offline-load-stale-cache \
      --disk-cache-size=0 \
      --media-cache-size=0 \
      --kiosk \
      --app=http://localhost:${HTTP_PORT} \
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
      --disable-crash-reporter \
      --remote-debugging-port=9222

    EXIT_CODE=$?
    END_TIME=$(date +%s)
    RUN_TIME=$((END_TIME - START_TIME))

    log "Chromium exited with code $EXIT_CODE after ${RUN_TIME}s"

    # Exit if we were told to stop
    [ "$STOPPING" -eq 1 ] && exit 0

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

    # Clear lock/crash files but preserve cookies and login state
    rm -f "$CHROMIUM_DATA_DIR/SingletonLock" "$CHROMIUM_DATA_DIR/SingletonSocket" "$CHROMIUM_DATA_DIR/SingletonCookie"
    rm -rf "$CHROMIUM_DATA_DIR/Crashpad"

    log "Restarting Chromium..."
  done
' -- :0 vt7 &
  wait $!

  # ── Post-xinit check (inside outer restart loop) ──
  # Reboot if Xorg failed to claim the VT — that state can only be cleared by reboot.
  XORG_LOG="$HOME/.local/share/xorg/Xorg.0.log"
  if grep -q "Switching VT failed" "$XORG_LOG" 2>/dev/null; then
    log "ERROR: Xorg could not claim a VT (previous session was hard-killed)."
    log "Rebooting in 5 seconds to recover..."
    sleep 5
    sudo reboot
  fi

  log "X session ended. Restarting in 5 seconds..."
  sleep 5
done
