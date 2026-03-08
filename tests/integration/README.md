# Integration Tests

Both suites run against a live BS5c device via SSH. Override the target with `HOST=`.

## test-router-basic.sh

Tests the router's media POST endpoint, WebSocket push, suppression logic, and frontend wiring. No Sonos or specific player config required.

10 tests:
1. POST /router/media returns 200
2. Status=ok when no local source active
3. WebSocket connect receives cached state
4. Cached state contains correct title
5. POST pushes to connected WS clients
6. Media suppressed when local source active
7. Media flows after local source deactivated
8. /router/status returns valid JSON
9. No `<img src="">` in DOM (broken image fix)
10. Frontend media WS connected to router:8770

```bash
./tests/integration/test-router-basic.sh
HOST=beosound5c-kitchen.local ./tests/integration/test-router-basic.sh
```

Prerequisites: `beo-router` + `beo-http` + `beo-ui` running.

## test-media-routing.sh

Tests the full media pipeline: Sonos player -> router -> UI WebSocket. Plays real audio on a Sonos speaker.

12 tests:
1. Player service running, router responding
2. External Sonos play: artwork appears via CDP
3. External Sonos play: title/artist text populated
4. Track change: artwork/title updates
5. Pause: artwork persists (no broken image)
6. Stop: no broken image
7. Navigate away and back: artwork survives DOM rebuild
8. Router restart: UI recovers media state
9. Volume report routing (adapter/player match check)
10. Remote source play: media not suppressed
11. Sonos playing -> local source takeover with suppression
12. Local source active -> external Sonos takeover shows new metadata

```bash
./tests/integration/test-media-routing.sh
./tests/integration/test-media-routing.sh 2 3 5    # specific tests
HOST=beosound5c-kitchen.local ./tests/integration/test-media-routing.sh
```

Prerequisites: `player.type=sonos` in config, Spotify linked to Sonos, speaker idle.

**Warning:** Plays audio on a real Sonos speaker. Do not auto-run.

## test-source-playback.sh

Regression tests for menu navigation, source views, service health, playback commands (play/pause/next/prev/stop), playing view, router, and WebSocket connectivity. Auto-discovers available sources and picks the best one for playback testing (USB > demo > plex > spotify).

~30-40 tests (varies by device config):
- Menu navigation (all configured items)
- Service health (router, player, each source)
- Source view loading via CDP
- Full playback lifecycle (play, next, prev, pause, resume, stop)
- Playing view + router active source verification
- Command acceptance for all registered sources
- Router menu, events, and WebSocket connectivity

```bash
./tests/integration/test-source-playback.sh
HOST=beosound5c-kitchen.local ./tests/integration/test-source-playback.sh
./tests/integration/test-source-playback.sh --json    # machine-readable output
```

Prerequisites: `beo-router` + `beo-player-*` + `beo-http` + `beo-ui` running. At least one source with playable content (USB recommended — no auth needed).

## test-action-timestamps.sh

Tests the action timestamp mechanism that prevents race conditions during source switching. See [docs/action-timestamps.md](../../docs/action-timestamps.md) for how the mechanism works.

Three test suites, selectable by argument:

### common (12 tests, player-agnostic)
Core timestamp logic via direct HTTP calls. Works with any player type.

1. Activate stamps action_ts
2. Newer source wins on sequential switch
3. Stale register("playing") rejected
4. Stale player_play() rejected
5. Stale media update rejected
6. Media from wrong source rejected
7. Rapid A→B switch: B wins
8. A→B→A re-activation gets new timestamp
9. No timestamp (legacy) passes — backward compatibility
10. Player tracks latest action_ts
11. Burst of stale commands all rejected
12. Auto-advance: same action_ts accepted

### local (5 tests, requires local player)
Real playback tests on a local-player device. Requires radio + usb (+ optionally cd, plex).

1. Radio plays, switch to USB — poll doesn't steal back
2. Rapid radio → plex before radio finishes connecting
3. CD disc insert stamps action_ts
4. Cross-player-type switch (local → remote)
5. Radio metadata dropped after switch to USB

### sonos (8 tests, requires Sonos player)
Tests specific to Sonos: external playback detection, playback override timestamp propagation, and stale source rejection after external takeover. Requires Spotify + radio.

1. playback_override updates router latest_action_ts
2. Stale register after override rejected
3. Stale player_play after override rejected
4. Stale media after override rejected
5. Override then fresh activation works
6. Override with no active source
7. Real Spotify → Radio on Sonos (monitor doesn't steal back)
8. Rapid Spotify → Radio before Spotify loads

```bash
./tests/integration/test-action-timestamps.sh              # common + local
./tests/integration/test-action-timestamps.sh common        # common only
./tests/integration/test-action-timestamps.sh local         # local player only
./tests/integration/test-action-timestamps.sh sonos         # Sonos player only
HOST=beosound5c-kitchen.local ./tests/integration/test-action-timestamps.sh sonos
```

Prerequisites: `beo-router` + `beo-player-*` + at least 2 sources running. Lowers volume to 10% during tests and restores afterwards.

**Warning:** Plays audio on a real speaker. Do not auto-run.
