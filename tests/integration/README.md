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
