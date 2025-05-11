#!/bin/bash
# web.sh - Start kiosk browser for BeoSound5C UI
# Points to integrated.py web server instead of file://

# Target screen to enable start of programs with UI from ssh sessions
export DISPLAY=:0

# Set up logging
LOGFILE="$HOME/beosound5c_browser.log"
echo "$(date) - Starting BeoSound5C browser" >> $LOGFILE

# Turn off screen blanking/power management
xset s off
xset -dpms
xset s noblank

# Hide mouse cursor after idle time
unclutter -idle 0.5 -root &

# Function to check if web server is running
check_server() {
  curl -s http://localhost:8000 > /dev/null
  return $?
}

# Wait for web server to start (max 30 seconds)
SERVER_READY=0
for i in {1..30}; do
  echo "Checking if web server is running (attempt $i)..." >> $LOGFILE
  if check_server; then
    SERVER_READY=1
    echo "Web server is running!" >> $LOGFILE
    break
  fi
  sleep 1
done

if [ $SERVER_READY -eq 0 ]; then
  echo "ERROR: Web server not available after 30 seconds, starting anyway..." >> $LOGFILE
fi

# Launch browser with error handling and restart logic
while true; do
  echo "$(date) - Starting Chromium browser..." >> $LOGFILE
  
  # Launch Chromium in kiosk mode
  chromium-browser \
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
    "http://localhost:8000/index.html" &
  
  BROWSER_PID=$!
  echo "Browser started with PID $BROWSER_PID" >> $LOGFILE
  
  # Wait for browser to exit
  wait $BROWSER_PID
  
  # If we get here, the browser has crashed or exited
  EXIT_CODE=$?
  echo "$(date) - Browser exited with code $EXIT_CODE, restarting in 5 seconds..." >> $LOGFILE
  sleep 5
done
