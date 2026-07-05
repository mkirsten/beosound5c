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
