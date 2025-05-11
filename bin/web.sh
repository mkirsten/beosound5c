#!/bin/bash
# web.sh - Start kiosk browser for BeoSound5C UI
# Points to integrated.py web server instead of file://

# Target screen to enable start of programs with UI from ssh sessions
export DISPLAY=:0

# Turn off screen blanking/power management
xset s off
xset -dpms
xset s noblank

# Hide mouse cursor after idle time
unclutter -idle 0.5 -root &
  
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
    "http://localhost:8000/index.html"