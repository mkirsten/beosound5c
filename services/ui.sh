#!/usr/bin/env bash
# BeoSound 5c UI Service
# Runs Chromium in kiosk mode with crash recovery

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPLASH_IMAGE="${SCRIPT_DIR}/../plymouth/splashscreen-red.png"
export SPLASH_IMAGE  # Export for ui-session.sh

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

# Chromium profile. On a standard install sd-hardening symlinks this to
# tmpfs, so cookies and login state are wiped on every reboot — the kiosk
# doesn't need them. It only lives on the SD card if sd-hardening was
# skipped.
CHROMIUM_DATA_DIR="$HOME/.config/chromium"
export CHROMIUM_DATA_DIR  # Export for ui-session.sh
# If the profile dir is a symlink (sd-hardening → tmpfs), recreate the
# symlink target on each boot — tmpfs is wiped on power cycle.
if [ -L "$CHROMIUM_DATA_DIR" ]; then
  mkdir -p "$(readlink "$CHROMIUM_DATA_DIR")/Default"
else
  mkdir -p "$CHROMIUM_DATA_DIR/Default"
fi

# Redirect GPU/shader/code caches from SD to tmpfs.
# HTTP/media caches are capped at 10MB each via --disk-cache-size /
# --media-cache-size (NOTE: 0 means "Chromium decides", not "off").
# --disable-component-update stops Chromium's background component
# downloads (crx cache, WasmTtsEngine, on-device models — ~75MB+) which
# a kiosk never needs and which otherwise slowly fill the 200MB tmpfs
# that sd-hardening mounts on /tmp.
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
xinit /bin/bash "$SCRIPT_DIR/ui-session.sh" -- :0 vt7 &
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
