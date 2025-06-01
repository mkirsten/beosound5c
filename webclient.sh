#!/usr/bin/env bash
# Kill potential conflicting X instances
sudo pkill X || true

# Turn off screen blanking
xset s off
xset s noblank
xset -dpms

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
