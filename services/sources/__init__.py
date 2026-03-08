"""
Sources — things that provide content to play.

A source registers with the router, gets a menu item in the UI, and receives
forwarded button/remote events when it is the active source.  Sources handle
their own playback (e.g. mpv for CD, Spotify Connect for Spotify) and typically
output audio to the Sonos speaker via AirPlay or the network.

Current sources:
  cd.py       — CD/DVD playback via mpv, metadata from MusicBrainz
  spotify.py  — Spotify Connect browsing and playback (PKCE OAuth)
  usb.py      — USB file browsing and playback via mpv
  plex.py     — Plex music browsing and playback
  news.py     — RSS/TTS news playback via local mpv
  demo.py     — Demo mode with bundled media
"""
