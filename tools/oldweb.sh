#!/bin/bash
# web.sh - Start kiosk browser for BeoSound5C UI
export DISPLAY=:0

# Turn off screen blanking
xset s off
xset s noblank
xset -dpms

# Clear Chromium cache before starting
rm -rf ~/.cache/chromium/Default/Cache/*
rm -rf ~/.cache/chromium/Default/Code\ Cache/*
rm -rf ~/.cache/chromium/Default/Service\ Worker/*

# Launch Chromium in kiosk mode with cache disabled

chromium-browser \
  --disable-application-cache \
  --disable-cache \
  --disable-offline-load-stale-cache \
  --disk-cache-size=0 \
  --media-cache-size=0 \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-translate \
  --disable-features=TranslateUI \
  --disable-suggestions-service \
  --disable-popup-blocking \
  --no-first-run \
  --start-maximized \
  --kiosk \
  --enable-logging \
  --v=0 \
  --no-sandbox \
  --enable-logging=stderr \
  "http://localhost:8000/index.html"
