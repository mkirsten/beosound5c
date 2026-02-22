"""
Players — monitors for external playback devices.

A player does NOT provide content.  It watches a playback device (e.g. a Sonos
speaker) and reports what's happening — track info, artwork, volume, playback
state — to the UI via WebSocket.  The content might come from any source: CD
via AirPlay, Spotify Connect, someone's phone, etc.

A source and a player can coexist: cd.py (source) sends audio to Sonos, while
sonos.py (player) watches the Sonos and tells the UI what's playing.

Each BS5c has ONE configured player.  Sources set player="remote" to play on
it natively, or player="local" to play locally and stream via AirPlay.

Current players:
  sonos.py      — Sonos speaker monitoring (artwork, metadata, volume reporting)
  bluesound.py  — BlueSound speaker monitoring (STUB — not yet implemented)
"""
