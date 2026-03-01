#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# Media Routing Test Suite
#
# Tests the full media pipeline: Sonos player → router → UI WebSocket.
# Runs against a live BS5c device with player.type=sonos.
#
# WARNING: This plays audio on a real Sonos speaker. Do not auto-run.
#
# Usage:
#   ./tests/integration/test-media-routing.sh                    # run all tests
#   ./tests/integration/test-media-routing.sh 2 3 5              # run specific tests
#   HOST=beosound5c-kitchen.local ./tests/integration/...        # override device
#
# Prerequisites:
#   - Device config has player.type=sonos with a reachable Sonos IP
#   - Code deployed and beo-router + beo-player-sonos + beo-ui running
#   - Sonos speaker idle (not grouped, not playing)
# ─────────────────────────────────────────────────────────────────────

HOST="${HOST:-beosound5c-office.kirstenhome}"
ROUTER="http://localhost:8770"
PLAYER="http://localhost:8766"
INPUT="http://localhost:8767"
PASS=0
FAIL=0
SKIP=0

# Spotify track used for playback tests (needs Spotify account linked to Sonos)
SPOTIFY_TRACK_URI="https://open.spotify.com/track/57iy1jpisanBz2RuQTrcvr"
SPOTIFY_TRACK_2_URI="https://open.spotify.com/track/3n3Ppam7vgaVa1iaRUc9Lp"

run_on() { ssh -o ConnectTimeout=5 "$HOST" "$@" 2>/dev/null; }

test_result() {
    local num="$1" name="$2" ok="$3" detail="$4"
    if [ "$ok" = "true" ]; then
        printf "  \033[32mPASS\033[0m  Test %2d: %s\n" "$num" "$name"
        PASS=$((PASS + 1))
    elif [ "$ok" = "skip" ]; then
        printf "  \033[33mSKIP\033[0m  Test %2d: %s — %s\n" "$num" "$name" "$detail"
        SKIP=$((SKIP + 1))
    else
        printf "  \033[31mFAIL\033[0m  Test %2d: %s — %s\n" "$num" "$name" "$detail"
        FAIL=$((FAIL + 1))
    fi
}

# ── Helpers ──

# Get CDP WebSocket URL for the main Chromium page
get_cdp_ws() {
    local targets
    targets=$(run_on "curl -s http://localhost:9222/json" || echo "[]")
    echo "$targets" | python3 -c "
import sys, json
for t in json.load(sys.stdin):
    if t.get('type') == 'page' and ':8000' in t.get('url',''):
        print(t['webSocketDebuggerUrl']); break
" 2>/dev/null
}

# Evaluate JS in the browser via CDP, print the result value
cdp_eval() {
    local ws_url="$1" js="$2"
    run_on "python3 << 'PYEOF'
import json, asyncio, websockets
async def run():
    async with websockets.connect(\"$ws_url\") as ws:
        await ws.send(json.dumps({\"id\":1,\"method\":\"Runtime.evaluate\",
            \"params\":{\"expression\":\"\"\"$js\"\"\",\"returnByValue\":True}}))
        r = json.loads(await ws.recv())
        res = r.get(\"result\",{}).get(\"result\",{})
        v = res.get(\"value\", res.get(\"description\",\"\"))
        if isinstance(v, dict) or isinstance(v, list):
            print(json.dumps(v))
        else:
            print(v)
asyncio.run(run())
PYEOF"
}

# Play a Spotify track on Sonos via ShareLink (reliable metadata + artwork)
sonos_play_spotify() {
    local uri="${1:-$SPOTIFY_TRACK_URI}"
    run_on "python3 -c \"
import soco, time
from soco.plugins.sharelink import ShareLinkPlugin
s = soco.SoCo('$SONOS_IP')
try: s.stop()
except: pass
time.sleep(0.5)
s.clear_queue()
ShareLinkPlugin(s).add_share_link_to_queue('$uri')
s.play_from_queue(0)
time.sleep(3)
info = s.get_current_transport_info()
print(info.get('current_transport_state','UNKNOWN'))
\""
}

sonos_pause() {
    run_on "python3 -c \"import soco; soco.SoCo('$SONOS_IP').pause()\""
}

sonos_stop() {
    run_on "python3 -c \"
import soco
try: soco.SoCo('$SONOS_IP').stop()
except: pass
\""
}

sonos_next() {
    run_on "python3 -c \"import soco; soco.SoCo('$SONOS_IP').next()\"" 2>/dev/null
}

sonos_get_volume() {
    run_on "python3 -c \"import soco; print(soco.SoCo('$SONOS_IP').volume)\""
}

wait_for_media() {
    local secs="${1:-5}"
    sleep "$secs"
}

# ── Resolve Sonos IP from device config ──
resolve_sonos_ip() {
    SONOS_IP=$(run_on "python3 -c \"
import json
with open('/etc/beosound5c/config.json') as f:
    c = json.load(f)
print(c.get('player',{}).get('ip',''))
\"")
    if [ -z "$SONOS_IP" ]; then
        echo "ERROR: No Sonos IP in config. Is player.type=sonos?"
        exit 1
    fi
    echo "Sonos IP: $SONOS_IP"
}


# ════════════════════════════════════════════════════════════════════
# TEST CASES
# ════════════════════════════════════════════════════════════════════

test_01() {
    echo "Test  1: Player service starts and connects to router"
    local active
    active=$(run_on "systemctl is-active beo-player-sonos")
    if [ "$active" != "active" ]; then
        test_result 1 "beo-player-sonos is running" "false" "status=$active"
        return
    fi
    local status
    status=$(run_on "curl -s $ROUTER/router/status")
    local transport
    transport=$(echo "$status" | python3 -c "import sys,json;print(json.load(sys.stdin).get('transport_mode',''))" 2>/dev/null)
    if [ -n "$transport" ]; then
        test_result 1 "Player service running, router responding" "true"
    else
        test_result 1 "Player service running, router responding" "false" "no transport_mode"
    fi
}

test_02() {
    echo "Test  2: External Sonos play — artwork appears on BS5c"
    local state
    state=$(sonos_play_spotify)
    if [ "$state" != "PLAYING" ]; then
        test_result 2 "Artwork appears on external play" "false" "Sonos state=$state"
        return
    fi
    # Wait for player monitor to detect, fetch artwork, POST to router
    wait_for_media 6

    local cdp_ws
    cdp_ws=$(get_cdp_ws)
    if [ -z "$cdp_ws" ]; then
        test_result 2 "Artwork appears on external play" "skip" "No CDP target"
        return
    fi
    local art_info
    art_info=$(cdp_eval "$cdp_ws" "
        var img = document.querySelector('.playing-artwork');
        JSON.stringify({
            src_start: img ? img.src.substring(0, 30) : '(none)',
            natural_w: img ? img.naturalWidth : 0,
            visible: img ? img.offsetParent !== null : false
        })
    ")
    local nw
    nw=$(echo "$art_info" | python3 -c "import sys,json;print(json.load(sys.stdin).get('natural_w',0))" 2>/dev/null || echo 0)
    local vis
    vis=$(echo "$art_info" | python3 -c "import sys,json;print(json.load(sys.stdin).get('visible',False))" 2>/dev/null || echo "False")
    if [ "$vis" = "True" ] && [ "$nw" -gt 1 ] 2>/dev/null; then
        test_result 2 "Artwork visible (${nw}px wide)" "true"
    else
        test_result 2 "Artwork appears on external play" "false" "$art_info"
    fi
}

test_03() {
    echo "Test  3: External Sonos play — title/artist text populated"
    local cdp_ws
    cdp_ws=$(get_cdp_ws)
    if [ -z "$cdp_ws" ]; then
        test_result 3 "Title/artist populated" "skip" "No CDP target"
        return
    fi
    local title
    title=$(cdp_eval "$cdp_ws" "
        var el = document.querySelector('.media-view-title');
        el ? el.textContent : '(none)'
    ")
    if [ -n "$title" ] && [ "$title" != "—" ] && [ "$title" != "(none)" ]; then
        test_result 3 "Title populated: '$title'" "true"
    else
        test_result 3 "Title/artist text populated" "false" "title='$title'"
    fi
}

test_04() {
    echo "Test  4: Track change — artwork/title updates"
    local cdp_ws
    cdp_ws=$(get_cdp_ws)
    if [ -z "$cdp_ws" ]; then
        test_result 4 "Artwork updates on track change" "skip" "No CDP target"
        return
    fi
    local old_title
    old_title=$(cdp_eval "$cdp_ws" "document.querySelector('.media-view-title')?.textContent || '(none)'")

    # Play a different track
    sonos_play_spotify "$SPOTIFY_TRACK_2_URI" > /dev/null
    wait_for_media 6

    local new_title
    new_title=$(cdp_eval "$cdp_ws" "document.querySelector('.media-view-title')?.textContent || '(none)'")
    if [ -n "$new_title" ] && [ "$new_title" != "—" ] && [ "$new_title" != "(none)" ] && [ "$new_title" != "$old_title" ]; then
        test_result 4 "Title changed: '$old_title' → '$new_title'" "true"
    else
        test_result 4 "Artwork updates on track change" "false" "old='$old_title' new='$new_title'"
    fi
}

test_05() {
    echo "Test  5: Sonos pause — artwork persists (no broken image)"
    sonos_pause 2>/dev/null
    sleep 2
    local cdp_ws
    cdp_ws=$(get_cdp_ws)
    if [ -z "$cdp_ws" ]; then
        test_result 5 "Artwork persists on pause" "skip" "No CDP target"
        return
    fi
    local art_info
    art_info=$(cdp_eval "$cdp_ws" "
        var img = document.querySelector('.playing-artwork');
        JSON.stringify({
            src_empty: img ? (img.getAttribute('src') === '') : true,
            natural_w: img ? img.naturalWidth : 0
        })
    ")
    local src_empty
    src_empty=$(echo "$art_info" | python3 -c "import sys,json;print(json.load(sys.stdin).get('src_empty',True))" 2>/dev/null)
    local nw
    nw=$(echo "$art_info" | python3 -c "import sys,json;print(json.load(sys.stdin).get('natural_w',0))" 2>/dev/null)
    if [ "$src_empty" = "False" ] && [ "$nw" -gt 1 ] 2>/dev/null; then
        test_result 5 "Artwork persists after pause (${nw}px)" "true"
    else
        test_result 5 "Artwork persists on pause" "false" "$art_info"
    fi
}

test_06() {
    echo "Test  6: Sonos stop — no broken image"
    sonos_stop 2>/dev/null
    sleep 2
    local cdp_ws
    cdp_ws=$(get_cdp_ws)
    if [ -z "$cdp_ws" ]; then
        test_result 6 "No broken image on stop" "skip" "No CDP target"
        return
    fi
    local src_empty
    src_empty=$(cdp_eval "$cdp_ws" "
        var img = document.querySelector('.playing-artwork');
        img ? (img.getAttribute('src') === '') : true
    ")
    if [ "$src_empty" = "false" ] || [ "$src_empty" = "False" ]; then
        test_result 6 "No broken image after stop" "true"
    else
        test_result 6 "No broken image on stop" "false" "src_empty=$src_empty"
    fi
}

test_07() {
    echo "Test  7: Navigate away and back — artwork persists"
    sonos_play_spotify > /dev/null
    wait_for_media 6

    local cdp_ws
    cdp_ws=$(get_cdp_ws)
    if [ -z "$cdp_ws" ]; then
        test_result 7 "Artwork persists after nav" "skip" "No CDP target"
        sonos_stop 2>/dev/null
        return
    fi

    # Navigate to system, wait, navigate back to playing
    cdp_eval "$cdp_ws" "window.uiStore.navigateToView('menu/system')" > /dev/null
    sleep 2
    cdp_eval "$cdp_ws" "window.uiStore.navigateToView('menu/playing')" > /dev/null
    sleep 2

    local nw
    nw=$(cdp_eval "$cdp_ws" "
        var img = document.querySelector('.playing-artwork');
        img ? img.naturalWidth : 0
    ")
    if [ "$nw" -gt 1 ] 2>/dev/null; then
        test_result 7 "Artwork persists after nav away/back (${nw}px)" "true"
    else
        test_result 7 "Artwork persists after nav" "false" "naturalWidth=$nw"
    fi

    sonos_stop 2>/dev/null
}

test_08() {
    echo "Test  8: Router restart — UI recovers media state"
    sonos_play_spotify > /dev/null
    wait_for_media 6

    # Restart router — player will re-POST on next monitor cycle (~0.5s)
    run_on "sudo systemctl restart beo-router" > /dev/null
    sleep 6  # router starts, player re-posts, UI WS reconnects

    local cdp_ws
    cdp_ws=$(get_cdp_ws)
    if [ -z "$cdp_ws" ]; then
        test_result 8 "UI recovers after router restart" "skip" "No CDP target"
        sonos_stop 2>/dev/null
        return
    fi

    local ws_url
    ws_url=$(cdp_eval "$cdp_ws" "window.mediaWebSocket ? window.mediaWebSocket.url : 'disconnected'")

    local title
    title=$(cdp_eval "$cdp_ws" "document.querySelector('.media-view-title')?.textContent || '—'")

    if echo "$ws_url" | grep -q "8770/router/ws" && [ "$title" != "—" ] && [ -n "$title" ]; then
        test_result 8 "UI recovered: WS reconnected, title='$title'" "true"
    else
        test_result 8 "UI recovers after router restart" "false" "ws=$ws_url title=$title"
    fi

    sonos_stop 2>/dev/null
}

test_09() {
    echo "Test  9: Volume report — adapter/player match check"
    # Check whether the config has volume.type matching player.type
    local vol_config
    vol_config=$(run_on "python3 -c \"
import json
with open('/etc/beosound5c/config.json') as f:
    c = json.load(f)
vol_type = c.get('volume',{}).get('type','')
player_type = c.get('player',{}).get('type','')
# If no explicit volume.type, the adapter auto-detects from player.type
if not vol_type:
    if player_type in ('sonos','bluesound'):
        vol_type = player_type
    elif player_type in ('local','powerlink'):
        vol_type = 'powerlink'
    else:
        vol_type = 'beolab5'
match = (vol_type == player_type)
print(f'{vol_type}|{player_type}|{match}')
\"")
    local vol_type player_type match_str
    vol_type=$(echo "$vol_config" | cut -d'|' -f1)
    player_type=$(echo "$vol_config" | cut -d'|' -f2)
    match_str=$(echo "$vol_config" | cut -d'|' -f3)

    # Check router log for the acceptance/ignored message
    local log_line
    log_line=$(run_on "journalctl -u beo-router --no-pager -n 100 2>/dev/null" | grep "Volume reports from player:" | tail -1)

    if [ "$match_str" = "True" ]; then
        # Adapter matches player — volume reports should be accepted
        if echo "$log_line" | grep -q "accepted"; then
            test_result 9 "Volume reports accepted (adapter=$vol_type = player=$player_type)" "true"
        else
            test_result 9 "Volume reports accepted" "false" "adapter=$vol_type player=$player_type log='$log_line'"
        fi
    else
        # Adapter doesn't match — volume reports should be ignored (that's correct)
        if echo "$log_line" | grep -q "ignored"; then
            test_result 9 "Volume reports correctly ignored (adapter=$vol_type != player=$player_type)" "true"
        else
            test_result 9 "Volume report routing" "false" "adapter=$vol_type player=$player_type log='$log_line'"
        fi
    fi
}

test_10() {
    echo "Test 10: Remote source play — media not suppressed"
    # Register a fake source with player=remote (like Spotify source)
    run_on "curl -s -X POST $ROUTER/router/source \
        -H 'Content-Type: application/json' \
        -d '{\"id\":\"test_remote\",\"state\":\"playing\",\"name\":\"Test Remote\",\"command_url\":\"http://localhost:9999/cmd\",\"player\":\"remote\",\"handles\":[\"play\",\"pause\"]}'" > /dev/null

    # POST media — should NOT be suppressed (player=remote, not local)
    local resp
    resp=$(run_on "curl -s -X POST $ROUTER/router/media \
        -H 'Content-Type: application/json' \
        -d '{\"title\":\"Remote Source Track\",\"artist\":\"Remote Artist\",\"_reason\":\"test\"}'")
    local status
    status=$(echo "$resp" | python3 -c "import sys,json;print(json.load(sys.stdin).get('status',''))" 2>/dev/null)

    # Clean up
    run_on "curl -s -X POST $ROUTER/router/source \
        -H 'Content-Type: application/json' \
        -d '{\"id\":\"test_remote\",\"state\":\"gone\"}'" > /dev/null

    if [ "$status" = "ok" ]; then
        test_result 10 "Remote source media not suppressed" "true"
    else
        test_result 10 "Remote source play — no suppression" "false" "status=$status"
    fi
}

test_11() {
    echo "Test 11: Sonos playing → local source takes over with own metadata"
    # Step 1: Play on Sonos (simulates external playback with artwork)
    sonos_play_spotify > /dev/null
    wait_for_media 6

    local cdp_ws
    cdp_ws=$(get_cdp_ws)
    if [ -z "$cdp_ws" ]; then
        test_result 11 "Local source takeover" "skip" "No CDP target"
        sonos_stop 2>/dev/null
        return
    fi

    local sonos_title
    sonos_title=$(cdp_eval "$cdp_ws" "document.querySelector('.media-view-title')?.textContent || '—'")

    # Step 2: Register a local source as playing (simulates CD inserting)
    run_on "curl -s -X POST $ROUTER/router/source \
        -H 'Content-Type: application/json' \
        -d '{\"id\":\"cd\",\"state\":\"playing\",\"name\":\"CD\",\"command_url\":\"http://localhost:8769/command\",\"player\":\"local\",\"menu_preset\":\"cd\",\"handles\":[\"play\",\"pause\",\"next\",\"prev\",\"stop\"]}'" > /dev/null
    sleep 1

    # Step 3: Verify Sonos media POST is now suppressed
    local supp_resp
    supp_resp=$(run_on "curl -s -X POST $ROUTER/router/media \
        -H 'Content-Type: application/json' \
        -d '{\"title\":\"Sonos Should Be Suppressed\",\"artist\":\"Nope\",\"_reason\":\"test\"}'")
    local supp_status
    supp_status=$(echo "$supp_resp" | python3 -c "import sys,json;print(json.load(sys.stdin).get('status',''))" 2>/dev/null)

    # Step 4: Broadcast local source metadata via input.py (same path CD uses)
    run_on "curl -s -X POST $INPUT/webhook \
        -H 'Content-Type: application/json' \
        -d '{\"command\":\"broadcast\",\"params\":{\"type\":\"cd_update\",\"data\":{\"title\":\"Local CD Album\",\"artist\":\"Local Artist\",\"album\":\"Test Album\",\"state\":\"playing\",\"current_track\":1,\"tracks\":[{\"number\":1,\"title\":\"Track 1\"}]}}}'" > /dev/null
    sleep 1

    # Step 5: Check UI state — source should be CD with local player
    local active_source
    active_source=$(cdp_eval "$cdp_ws" "window.uiStore.activeSource || 'none'")
    local active_player
    active_player=$(cdp_eval "$cdp_ws" "window.uiStore.activeSourcePlayer || 'none'")

    # Clean up
    run_on "curl -s -X POST $ROUTER/router/source \
        -H 'Content-Type: application/json' \
        -d '{\"id\":\"cd\",\"state\":\"gone\"}'" > /dev/null
    sonos_stop 2>/dev/null

    if [ "$supp_status" = "suppressed" ] && [ "$active_source" = "cd" ] && [ "$active_player" = "local" ]; then
        test_result 11 "Local takeover OK (was='$sonos_title', suppressed=$supp_status, source=$active_source)" "true"
    else
        test_result 11 "Local source takeover" "false" "supp=$supp_status source=$active_source player=$active_player"
    fi
}

test_12() {
    echo "Test 12: Local source active → external Sonos takeover shows new metadata"
    # Step 1: Register a local source as playing (simulates CD/AirPlay streaming)
    run_on "curl -s -X POST $ROUTER/router/source \
        -H 'Content-Type: application/json' \
        -d '{\"id\":\"cd\",\"state\":\"playing\",\"name\":\"CD\",\"command_url\":\"http://localhost:8769/command\",\"player\":\"local\",\"menu_preset\":\"cd\",\"handles\":[\"play\",\"pause\",\"next\",\"prev\",\"stop\"]}'" > /dev/null

    # Step 2: Verify media is suppressed while local source active
    local supp_resp
    supp_resp=$(run_on "curl -s -X POST $ROUTER/router/media \
        -H 'Content-Type: application/json' \
        -d '{\"title\":\"Still Suppressed\",\"artist\":\"X\",\"_reason\":\"test\"}'")
    local supp_ok
    supp_ok=$(echo "$supp_resp" | python3 -c "import sys,json;print(json.load(sys.stdin).get('status',''))" 2>/dev/null)

    # Step 3: Simulate player service detecting external Sonos takeover
    # (what sonos.py does when it sees track_changed + seconds_since_command > 3)
    run_on "curl -s -X POST $ROUTER/router/playback_override \
        -H 'Content-Type: application/json' \
        -d '{\"force\":true}'" > /dev/null

    # Step 4: Verify active source is now cleared
    local status_resp
    status_resp=$(run_on "curl -s $ROUTER/router/status")
    local active_after
    active_after=$(echo "$status_resp" | python3 -c "import sys,json;print(json.load(sys.stdin).get('active_source','') or 'none')" 2>/dev/null)

    # Step 5: POST new Sonos media — should flow through now
    local flow_resp
    flow_resp=$(run_on "curl -s -X POST $ROUTER/router/media \
        -H 'Content-Type: application/json' \
        -d '{\"title\":\"External Sonos Track\",\"artist\":\"Sonos Artist\",\"album\":\"From iOS App\",\"artwork\":\"data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs+9AAAAFklEQVQYV2P8z8BQz0BhwMgwasCoAgBGKwoBmRnURwAAAABJRU5ErkJggg==\",\"state\":\"playing\",\"_reason\":\"external_takeover\"}'")
    local flow_status
    flow_status=$(echo "$flow_resp" | python3 -c "import sys,json;print(json.load(sys.stdin).get('status',''))" 2>/dev/null)

    sleep 1

    # Step 6: Verify UI shows the new Sonos metadata
    local cdp_ws
    cdp_ws=$(get_cdp_ws)
    local new_title=""
    if [ -n "$cdp_ws" ]; then
        new_title=$(cdp_eval "$cdp_ws" "document.querySelector('.media-view-title')?.textContent || '—'")
    fi

    # Clean up (source already cleared by playback_override)
    run_on "curl -s -X POST $ROUTER/router/source \
        -H 'Content-Type: application/json' \
        -d '{\"id\":\"cd\",\"state\":\"gone\"}'" > /dev/null

    if [ "$supp_ok" = "suppressed" ] && [ "$active_after" = "none" ] && [ "$flow_status" = "ok" ] && [ "$new_title" = "External Sonos Track" ]; then
        test_result 12 "External takeover: suppressed→cleared→flows→UI='$new_title'" "true"
    else
        test_result 12 "External Sonos takeover" "false" "supp=$supp_ok active=$active_after flow=$flow_status title=$new_title"
    fi
}


# ════════════════════════════════════════════════════════════════════
# RUNNER
# ════════════════════════════════════════════════════════════════════

echo "═══════════════════════════════════════════════════"
echo " Media Routing Test Suite"
echo " Device: $HOST"
echo "═══════════════════════════════════════════════════"
echo ""

# Check connectivity
if ! ssh -o ConnectTimeout=3 "$HOST" "true" 2>/dev/null; then
    echo "ERROR: Cannot connect to $HOST"
    exit 1
fi

# Resolve Sonos IP
resolve_sonos_ip

# Check services
echo ""
echo "Services:"
run_on "systemctl is-active beo-router beo-player-sonos beo-http beo-ui" | while read -r line; do
    echo "  $line"
done
echo ""

# Determine which tests to run
TESTS="${@:-1 2 3 4 5 6 7 8 9 10 11 12}"

for t in $TESTS; do
    "test_$(printf '%02d' "$t")"
done

echo ""
echo "═══════════════════════════════════════════════════"
printf " Results: \033[32m%d passed\033[0m" "$PASS"
[ "$FAIL" -gt 0 ] && printf ", \033[31m%d failed\033[0m" "$FAIL"
[ "$SKIP" -gt 0 ] && printf ", \033[33m%d skipped\033[0m" "$SKIP"
echo ""
echo "═══════════════════════════════════════════════════"

# Final cleanup: stop Sonos
sonos_stop 2>/dev/null

exit $FAIL
