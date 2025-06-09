#!/usr/bin/env bash
# Kill potential conflicting X instances
sudo pkill X || true

# Wait for X to be ready (so xset doesn’t silently fail)
export DISPLAY=:0
export XAUTHORITY=/home/kirsten/.Xauthority
for i in $(seq 1 30); do
  xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 && break
  sleep 1
done

# Turn off screensaver, screen blank and DPMS completely
xset s off          # disable screen saver
xset s noblank      # don’t blank the video device
xset -dpms          # disable DPMS (Energy Star) features
xset dpms 0 0 0     # set standby, suspend, off timers to 0 (never)

# Clear Chromium cache before starting
rm -rf ~/.cache/chromium/Default/Cache/*
rm -rf ~/.cache/chromium/Default/Code\ Cache/*
rm -rf ~/.cache/chromium/Default/Service\ Worker/*

xinit /usr/bin/chromium-browser \
  --disable-application-cache \
  --disable-cache \
  --disable-offline-load-stale-cache \
  --disk-cache-size=0 \
  --media-cache-size=0 \
  --noerrdialogs \
  --disable-infobars \
  --start-fullscreen \
  --window-size=1024,768 \
  --window-position=0,0 \
  --kiosk http://localhost:8000 \
  --disable-translate \
  --disable-session-crashed-bubble \
  --ignore-certificate-errors \
  --disable-features=IsolateOrigins,site-per-process \
  --disable-extensions \
  --disable-dev-shm-usage \
  --disable-features=TranslateUI \
  --enable-features=OverlayScrollbar \
  --overscroll-history-navigation=0 \
  --disable-features=MediaRouter \
  --disable-features=InfiniteSessionRestore \
  -- :0
