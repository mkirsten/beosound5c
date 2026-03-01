#!/bin/bash
# Service registry — single source of truth for BeoSound 5c service names,
# descriptions, and optional-source metadata.
# Sourced by install-services.sh and status-services.sh.

# All service unit files (for install / copy / final status)
ALL_SERVICES=(
    "beo-http.service"
    "beo-player-sonos.service"
    "beo-player-bluesound.service"
    "beo-input.service"
    "beo-router.service"
    "beo-masterlink.service"
    "beo-bluetooth.service"
    "beo-source-cd.service"
    "beo-source-spotify.service"
    "beo-source-apple-music.service"
    "beo-source-tidal.service"
    "beo-source-plex.service"
    "beo-source-usb.service"
    "beo-source-news.service"
    "beo-ui.service"
    "beo-notify-failure@.service"
    "beo-health.service"
    "beo-health.timer"
)

# User-facing services (for status display — excludes infra-only units)
STATUS_SERVICES=(
    "beo-http.service"
    "beo-player-sonos.service"
    "beo-player-bluesound.service"
    "beo-input.service"
    "beo-router.service"
    "beo-masterlink.service"
    "beo-bluetooth.service"
    "beo-source-cd.service"
    "beo-source-spotify.service"
    "beo-source-apple-music.service"
    "beo-source-tidal.service"
    "beo-source-plex.service"
    "beo-source-usb.service"
    "beo-source-news.service"
    "beo-ui.service"
)

# Service descriptions (for status display)
declare -A SERVICE_DESC
SERVICE_DESC["beo-http.service"]="HTTP Web Server (Port 8000)"
SERVICE_DESC["beo-player-sonos.service"]="Sonos Player (Port 8766)"
SERVICE_DESC["beo-player-bluesound.service"]="BlueSound Player (Port 8766)"
SERVICE_DESC["beo-input.service"]="Hardware Input Server (Port 8765)"
SERVICE_DESC["beo-router.service"]="Event Router (Port 8770)"
SERVICE_DESC["beo-masterlink.service"]="MasterLink Sniffer"
SERVICE_DESC["beo-bluetooth.service"]="Bluetooth Remote Service"
SERVICE_DESC["beo-source-cd.service"]="CD Source (Port 8769)"
SERVICE_DESC["beo-source-usb.service"]="USB File Source (Port 8773)"
SERVICE_DESC["beo-source-spotify.service"]="Spotify Source (Port 8771)"
SERVICE_DESC["beo-source-apple-music.service"]="Apple Music Source (Port 8774)"
SERVICE_DESC["beo-source-tidal.service"]="TIDAL Source (Port 8777)"
SERVICE_DESC["beo-source-plex.service"]="Plex Source (Port 8778)"
SERVICE_DESC["beo-source-news.service"]="News Source (Port 8776)"
SERVICE_DESC["beo-ui.service"]="Chromium UI Kiosk"

# Optional sources: menu_key|service|emoji|label
# Used by install-services.sh to start/skip based on config.json menu
OPTIONAL_SOURCES=(
    "CD|beo-source-cd.service|💿|CD source"
    "SPOTIFY|beo-source-spotify.service|🎵|Spotify source"
    "APPLE MUSIC|beo-source-apple-music.service|🍎|Apple Music source"
    "TIDAL|beo-source-tidal.service|🎵|TIDAL source"
    "PLEX|beo-source-plex.service|🎵|Plex source"
    "USB|beo-source-usb.service|💾|USB source"
    "NEWS|beo-source-news.service|📰|News source"
)
