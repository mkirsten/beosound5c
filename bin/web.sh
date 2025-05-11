#!/bin/bash
export DISPLAY=:0

# Hide mouse cursor after 0.5s idle
unclutter -idle 0.5 -root &

# Launch Chromium in kiosk mode
chromium-browser \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --kiosk \
  "http://localhost:8000/index.html"
