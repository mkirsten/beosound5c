# Action Timestamps: Source Switching Race Prevention

## Problem

When a user rapidly switches between sources (e.g., Radio then Spotify), three race conditions can occur:

1. **Late play command**: A slow source's `player_play()` arrives after the new source has already started playing, overwriting it on the speaker.
2. **Stale poll auto-advance**: Sources that poll player state (Tidal, Plex, Apple Music) detect "stopped" after a switch, auto-advance to the next track, and call `register("playing")` — stealing active status back from the new source.
3. **Stale metadata**: Media updates from the superseded source reach the UI after the new source has taken over.

## Solution

A monotonic timestamp (`action_ts`) is stamped at the moment of user input and flows through the entire chain. Every component compares against the latest known timestamp — **last user action always wins**.

## Flow

```
User presses source button
  Router stamps action_ts = time.monotonic()
  Router stores it as self._latest_action_ts
  Router forwards activate with action_ts to source
    Source stores action_ts, includes it in ALL outgoing calls:
      register("playing", action_ts=X)   → Router rejects if X < latest
      player_play(url=..., action_ts=X)  → Player rejects if X < latest
      post_media_update(..., action_ts=X)→ Router rejects if X < latest
```

## Example

1. Radio activated at ts=100, calls `player_play(ts=100)` — slow, takes 2 seconds
2. Spotify activated at ts=150, calls `player_play(ts=150)` — completes immediately
3. Player accepts Spotify (150 >= latest), updates latest to 150
4. Radio's late `player_play(ts=100)` arrives — **rejected** (100 < 150)
5. Radio's poll calls `register("playing", ts=100)` — **rejected** (100 < 150)

Auto-advance within the *active* source works naturally: source reuses its stored `action_ts`. As long as no newer activation has occurred, the timestamp still passes (100 >= 100).

## Where timestamps are checked

| Component | Method | Rejects when |
|-----------|--------|-------------|
| **Router** | `SourceRegistry.update()` | `action_ts < _latest_action_ts` on state="playing" from non-active source |
| **Router** | `_handle_media_post()` | `_action_ts < _latest_action_ts` |
| **Player** | `_handle_play()` | `action_ts < _latest_action_ts` |

## Where timestamps are stamped

| Location | When | Why |
|----------|------|-----|
| `router.route_event()` | Source button press | Normal user activation |
| `cd._on_disc_change()` | Disc inserted | CD autoplay bypasses `route_event()` |
| `player.notify_router_playback_override()` | External playback detected (Sonos app, Spotify Connect) | External input is also a user action |

## Where timestamps are stored and forwarded

| Component | Field | Set by | Included in |
|-----------|-------|--------|-------------|
| `EventRouter` | `_latest_action_ts` | `route_event()`, `handle_playback_override()` | Compared against incoming timestamps |
| `SourceBase` | `_action_ts` | `handle_activate()`, `cd._on_disc_change()` | `register()`, `player_play()`, `post_media_update()` |
| `PlayerBase` | `_latest_action_ts` | `_handle_play()`, `notify_router_playback_override()` | `broadcast_media_update()` |

## Edge cases

- **A→B→A rapid switch**: A gets ts=100, B gets ts=150, A gets ts=200. A's new commands use ts=200. Any lingering ts=100 commands are rejected.
- **First activation**: `_latest_action_ts` starts at 0. Any positive timestamp passes.
- **Router restart**: `_latest_action_ts` resets to 0. All sources pass. The existing `_resync_in_progress` guard handles multi-source conflicts during resync.
- **No timestamp** (legacy/direct calls): `action_ts=0` is treated as "no opinion" and always passes the check (`if action_ts and action_ts < latest` — the `if action_ts` guard skips the comparison when 0).

## Files

- `services/router.py` — stamps in `route_event()`, compares in `SourceRegistry.update()` and `_handle_media_post()`
- `services/lib/player_base.py` — compares in `_handle_play()`, stamps in `notify_router_playback_override()`
- `services/lib/source_base.py` — stores from `handle_activate()`, includes in `register()`/`player_play()`/`post_media_update()`
- `services/sources/cd.py` — stamps at disc insert in `_on_disc_change()`
