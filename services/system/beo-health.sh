#!/bin/bash
# Auto-recover failed beo-* services.
# Runs every 5 minutes via beo-health.timer.
# Discovers all beo-* services dynamically — no hardcoded list to maintain.
for svc in $(systemctl list-units 'beo-*.service' --no-legend --no-pager --plain --state=failed | awk '{print $1}'); do
    logger -t beo-health "Auto-recovering $svc"
    systemctl reset-failed "$svc"
    systemctl start "$svc"
done

# go-librespot busy-loop watchdog.  Observed Jun-Jul 2026 on Office:
# after a failed login5 token renewal, go-librespot spun at ~38% CPU for
# weeks with nothing playing, driving the Pi into thermal throttling.
# If the process burns sustained CPU while its own API says playback is
# stopped, restart it — with nothing playing, a restart is invisible.
librespot_watchdog() {
    systemctl is-active --quiet beo-librespot || return 0
    local pid
    pid=$(systemctl show -p MainPID --value beo-librespot)
    [ -n "$pid" ] && [ "$pid" != "0" ] || return 0

    # Skip if recently (re)started — avoid restart cycling if the busy
    # loop reappears immediately; also skips startup CPU spikes.
    local uptime_s
    uptime_s=$(ps -o etimes= -p "$pid" 2>/dev/null | tr -d ' ')
    [ -n "$uptime_s" ] && [ "$uptime_s" -ge 600 ] || return 0

    # CPU% over a 10s window from /proc utime+stime deltas.  Strip
    # everything through the comm field ")" so awk field numbers are
    # stable regardless of the process name.
    local hz t0 t1 cpu
    hz=$(getconf CLK_TCK)
    t0=$(cut -d')' -f2- /proc/$pid/stat 2>/dev/null | awk '{print $12+$13}')
    sleep 10
    t1=$(cut -d')' -f2- /proc/$pid/stat 2>/dev/null | awk '{print $12+$13}')
    [ -n "$t0" ] && [ -n "$t1" ] || return 0
    cpu=$(( (t1 - t0) * 100 / hz / 10 ))
    [ "$cpu" -ge 25 ] || return 0

    # Only act when go-librespot itself reports playback stopped.
    curl -s --max-time 2 http://localhost:3678/status \
        | grep -q '"stopped": *true' || return 0

    logger -t beo-health "go-librespot at ${cpu}% CPU while stopped — restarting beo-librespot"
    systemctl restart beo-librespot
}
librespot_watchdog

# PC2 USB recovery.  The BeolinkPC2 daughter card (0cd4:0101) sometimes
# wedges and drops off the USB bus entirely — beo-masterlink then loops
# "Reconnect failed: PC2 not found" forever and every MasterLink/PowerLink
# feature is dead until recovery.  Verified fix (Office, Jul 2026):
# rebinding the USB host controller forces a full bus re-enumeration and
# brings the card back without a mains power-cycle.  The rebind briefly
# disconnects ALL USB devices; the BS5 HID (beo-input) and USB drives
# (beo-source-usb) both re-attach automatically.
pc2_recovery() {
    # Only on devices that run masterlink at all.
    systemctl is-active --quiet beo-masterlink || return 0
    # PC2 present → nothing to do.
    lsusb -d 0cd4:0101 >/dev/null 2>&1 && return 0

    # Rate-limit to one reset attempt per hour — if the rebind doesn't
    # bring the card back, repeating it every 5 minutes only churns the
    # other USB devices.
    local stamp=/run/beo-health-pc2-reset
    if [ -f "$stamp" ]; then
        local age=$(( $(date +%s) - $(stat -c %Y "$stamp") ))
        [ "$age" -lt 3600 ] && return 0
    fi
    touch "$stamp"

    logger -t beo-health "BeolinkPC2 (0cd4:0101) missing from USB bus — rebinding USB controllers"
    local drv devpath dev
    for drv in /sys/bus/platform/drivers/xhci-hcd /sys/bus/pci/drivers/xhci_hcd; do
        [ -d "$drv" ] || continue
        for devpath in "$drv"/*; do
            [ -L "$devpath" ] || continue  # device entries are symlinks
            dev=$(basename "$devpath")
            echo "$dev" > "$drv/unbind" 2>/dev/null || continue
            sleep 2
            echo "$dev" > "$drv/bind" 2>/dev/null
            sleep 5
        done
    done

    if lsusb -d 0cd4:0101 >/dev/null 2>&1; then
        logger -t beo-health "BeolinkPC2 back on the USB bus — beo-masterlink will reconnect"
    else
        logger -t beo-health "BeolinkPC2 still missing after controller rebind — physical power-cycle may be required"
    fi
}
pc2_recovery

# /tmp pressure safety net.  sd-hardening mounts /tmp as a 200MB tmpfs; when
# it fills, Chromium, yt-dlp and anything else writing temp files starts
# failing in confusing ways (observed Jul 2026: leaked yt-dlp extractions
# plus Chromium component downloads filled /tmp within hours of a fresh
# install).  Root causes are fixed (pip yt-dlp, --disable-component-update),
# but a pressure valve keeps one regression from taking the device down.
# Everything removed here is a cache that Chromium/ui.sh recreates on demand.
tmp_pressure() {
    local used
    used=$(df --output=pcent /tmp 2>/dev/null | tail -1 | tr -dc '0-9')
    [ -n "$used" ] && [ "$used" -ge 90 ] || return 0

    logger -t beo-health "/tmp at ${used}% — clearing disposable caches"
    # Leaked PyInstaller extractions + Chromium component downloads: safe to
    # remove wholesale (recreated on demand, and shouldn't exist at all now).
    rm -rf /tmp/_MEI* \
           /tmp/chromium-profile/component_crx_cache \
           /tmp/chromium-profile/WasmTtsEngine \
           /tmp/chromium-profile/OnDeviceHeadSuggestModel \
           /tmp/chromium-profile/Default/Cache 2>/dev/null
    # Cache dirs that ui.sh symlinks into /tmp: empty their contents but keep
    # the dirs themselves so the symlink targets stay valid for Chromium.
    local d
    for d in /tmp/chromium-gr-shader /tmp/chromium-shader \
             /tmp/chromium-graphite /tmp/chromium-gpu-cache \
             /tmp/chromium-code-cache; do
        [ -d "$d" ] && find "$d" -mindepth 1 -delete 2>/dev/null
    done

    used=$(df --output=pcent /tmp 2>/dev/null | tail -1 | tr -dc '0-9')
    logger -t beo-health "/tmp now at ${used}% after cache cleanup"
}
tmp_pressure

# Chromium renderer pids.  `pgrep -f -- --type=renderer` alone also matches
# any shell whose own command line mentions the pattern (a health check run
# by hand from an ssh one-liner will match itself), so confirm each hit is
# really a Chromium process before trusting its fd count.
chromium_renderer_pids() {
    local pid
    for pid in $(pgrep -f -- '--type=renderer' 2>/dev/null); do
        case "$(cat /proc/$pid/comm 2>/dev/null)" in
            chrom*) echo "$pid" ;;
        esac
    done
}

# Chromium UI watchdog.  Two ways the kiosk dies without the process ever
# exiting, both seen on Church (Sep 2026) — systemd sees beo-ui "active"
# throughout, so Restart=always never fires and the screen stays frozen.
#
#   1. fd pressure.  A leaky page pins Chromium shared-memory segments, one
#      fd each.  The HA camera dashboard iframe managed 983 of them over 9
#      days of uptime.  At the soft RLIMIT_NOFILE a renderer does not crash,
#      it deadlocks: main thread parked in futex_wait, display frozen
#      mid-frame.  Nothing in Chromium notices (--disable-hang-monitor), so
#      catch it here while there is still headroom.
#
#   2. Wedged renderer, any cause.  The tell is that the UI's media
#      WebSocket to beo-router is gone and never returns: the reconnect is
#      setTimeout-driven (web/js/ws-dispatcher.js) and a hung main thread
#      can never run it.  Backoff caps at 60s, so a healthy UI is never
#      without that socket across two consecutive checks five minutes apart.
#
# A restart is cheap and near-invisible — Chromium has no state to lose and
# audio playback is owned by the player service, not the browser.
ui_watchdog() {
    systemctl is-active --quiet beo-ui || return 0

    # Ignore the first 10 minutes after a (re)start: Chromium is still
    # coming up and the media WS legitimately isn't connected yet.
    local main_pid uptime_s
    main_pid=$(systemctl show -p MainPID --value beo-ui)
    [ -n "$main_pid" ] && [ "$main_pid" != "0" ] || return 0
    uptime_s=$(ps -o etimes= -p "$main_pid" 2>/dev/null | tr -d ' ')
    [ -n "$uptime_s" ] && [ "$uptime_s" -ge 600 ] || return 0

    local nows_stamp=/run/beo-health-ui-nows

    # --- 1. fd pressure ---------------------------------------------------
    # Threshold is the lower of 70% of the soft limit and a flat 4000.  A
    # healthy renderer sits at 30-150 fds, so 4000 is already pathological
    # while still far from exhausting /dev/shm — it catches a leak early
    # rather than at the cliff edge.
    local soft limit worst=0 pid n
    soft=$(systemctl show -p LimitNOFILESoft --value beo-ui 2>/dev/null)
    [ -n "$soft" ] && [ "$soft" -gt 0 ] 2>/dev/null || soft=1024
    limit=$(( soft * 70 / 100 ))
    [ "$limit" -gt 4000 ] && limit=4000

    for pid in $(chromium_renderer_pids); do
        n=$(ls /proc/$pid/fd 2>/dev/null | wc -l)
        [ "$n" -gt "$worst" ] && worst=$n
    done

    if [ "$worst" -ge "$limit" ]; then
        logger -t beo-health "Chromium renderer at ${worst} fds (limit ${limit}, soft rlimit ${soft}) — restarting beo-ui before it wedges"
        rm -f "$nows_stamp"
        systemctl restart beo-ui
        return 0
    fi

    # --- 2. wedged renderer: no media WS to the router --------------------
    # Only meaningful when the router is up to accept the connection;
    # otherwise the missing socket says nothing about the UI's health.
    systemctl is-active --quiet beo-router || { rm -f "$nows_stamp"; return 0; }

    if ss -tnp state established 2>/dev/null | grep ':8770' | grep -q chromium; then
        rm -f "$nows_stamp"
        return 0
    fi

    if [ ! -f "$nows_stamp" ]; then
        touch "$nows_stamp"
        logger -t beo-health "UI has no media WS to beo-router — restarting if still missing at next check"
        return 0
    fi

    logger -t beo-health "UI still has no media WS to beo-router (renderer wedged) — restarting beo-ui"
    rm -f "$nows_stamp"
    systemctl restart beo-ui
}
ui_watchdog

# Flight-recorder metrics — one compact line per run so the journal holds
# the resource trajectory leading up to a lockup (Kitchen: unreachable
# after 1-3 weeks, power-cycle only; suspects are Chromium memory growth
# on a swapless 4GB system and the rtl8821au USB WiFi driver).  With
# persistent journald (tools/flight-recorder.sh) the trajectory survives
# the power cycle.  ~120 bytes / 5 min ≈ 35 KB/day.
flight_metrics() {
    local mem_avail load1 tmp_used chrom throttled
    mem_avail=$(awk '/^MemAvailable:/{printf "%d", $2/1024}' /proc/meminfo)
    load1=$(cut -d' ' -f1 /proc/loadavg)
    tmp_used=$(df --output=pcent /tmp 2>/dev/null | tail -1 | tr -dc '0-9')
    # Chromium process count + total RSS (matches chromium + chrome_crashpad)
    chrom=$(ps -eo rss=,comm= | awk '$2 ~ /^chrom/ {n++; s+=$1} END {printf "%d/%dM", n, s/1024}')

    # Worst renderer fd count — the number that climbs ahead of a UI wedge.
    # A renderer sits at 30-150 fds normally; a leaking page walks it upward
    # over days (Church reached 983 before deadlocking). Chromium's shared
    # memory lives in /tmp here, not /dev/shm (see the note in ui.sh), so
    # tmp= below already covers the space side of the same leak.
    local rfd=0 pid n
    for pid in $(chromium_renderer_pids); do
        n=$(ls /proc/$pid/fd 2>/dev/null | wc -l)
        [ "$n" -gt "$rfd" ] && rfd=$n
    done
    # Firmware throttle flags: 0x0 = healthy, bits set = undervoltage/thermal
    throttled=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)

    # Default-route interface health.  A dead carrier or climbing error
    # counters with beo-ui still alive points at the WiFi driver, not
    # Chromium — the two suspects separate cleanly here.
    local iface net="none"
    iface=$(ip route show default 2>/dev/null | awk '{print $5; exit}')
    if [ -n "$iface" ]; then
        net="${iface}:carrier=$(cat /sys/class/net/$iface/carrier 2>/dev/null || echo '?')"
        net="$net,rxerr=$(cat /sys/class/net/$iface/statistics/rx_errors 2>/dev/null || echo '?')"
        net="$net,txerr=$(cat /sys/class/net/$iface/statistics/tx_errors 2>/dev/null || echo '?')"
    fi

    logger -t beo-metrics "mem_avail=${mem_avail}M chromium=${chrom} rfd=${rfd} load=${load1} tmp=${tmp_used}% throttled=${throttled:-n/a} net=${net}"
}
flight_metrics
