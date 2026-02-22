"""
Sources — things that provide content to play.

A source registers with the router, gets a menu item in the UI, and receives
forwarded button/remote events when it is the active source.  Sources handle
their own playback (e.g. mpv for CD, Spotify Connect for Spotify) and typically
output audio to the Sonos speaker via AirPlay or the network.

Current sources:
  cd.py       — CD/DVD playback via mpv, metadata from MusicBrainz
  spotify.py  — (stub) Spotify Connect browsing and playback
  usb.py      — (stub) USB file browsing and playback
"""
