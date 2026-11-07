/**
 * The elapsed time on the PLAYING view.
 *
 * Two clocks, deliberately. The player is asked where it is every few
 * seconds, and between those answers the wall clock carries the number
 * forward. Asking once a second would be a request a second for a digit
 * that is entirely predictable in between; never asking would let drift and
 * seeks go unnoticed. This way the display ticks every second and the truth
 * arrives often enough that a seek shows up almost at once.
 *
 * It is not part of the media broadcast for the same reason: metadata
 * changes at track boundaries, a position changes always, and pushing it
 * would mean a message a second to every connected browser — including the
 * television, which is showing the same UI over Cast — whether or not any
 * of them has a clock on screen.
 *
 * Nothing here runs unless PLAYING is mounted. See media-manager.js.
 */
(function () {
    'use strict';

    const POLL_MS = 5000;     // how often the player is actually asked
    const TICK_MS = 1000;     // how often the digits are redrawn

    let pollTimer = null, tickTimer = null;
    let known = null;         // { positionMs, durationMs, atWallClock, playing }

    // The track text appears in three places: the normal panel, the
    // immersive overlay, and the canvas panel's mirror of that overlay
    // drawn over a video. The first two are written here; the mirror is a
    // clone and picks the row up when canvas-panel re-syncs it.
    function rows() {
        return [
            document.getElementById('media-time'),
            document.querySelector('.immersive-info-time'),
        ].filter(Boolean);
    }

    function fmt(ms) {
        const total = Math.max(0, Math.round(ms / 1000));
        const m = Math.floor(total / 60);
        return `${m}:${String(total % 60).padStart(2, '0')}`;
    }

    function playerUrl(path) {
        const base = (typeof getServiceUrl === 'function')
            ? getServiceUrl('playerUrl', 8766)
            : (window.AppConfig?.playerUrl || 'http://localhost:8766');
        return base + path;
    }

    async function poll() {
        try {
            const r = await fetch(playerUrl('/player/position'), { cache: 'no-store' });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const d = await r.json();
            if (!d.supported || !d.duration_ms) { known = null; return; }
            known = {
                positionMs: d.position_ms,
                durationMs: d.duration_ms,
                atWallClock: performance.now(),
                // Paused time must not keep running, and the player is the
                // one that knows: the body class lags a broadcast behind.
                playing: d.state === 'playing',
            };
        } catch (e) {
            // A player that is down or does not answer leaves the row
            // blank rather than frozen on a stale number.
            known = null;
        }
    }

    function tick() {
        const nodes = rows();
        if (!nodes.length) return;
        let text = '', show = false;
        if (known) {
            const drift = known.playing ? performance.now() - known.atWallClock : 0;
            const shown = Math.min(known.positionMs + drift, known.durationMs);
            text = `${fmt(shown)} / ${fmt(known.durationMs)}`;
            show = true;
        }
        let changed = false;
        for (const node of nodes) {
            if (node.textContent !== text) { node.textContent = text; changed = true; }
            node.classList.toggle('has-time', show);
        }
        // The canvas mirror is a copy taken at show() time, so it has to be
        // told when the text underneath it moved on. Only on a real change:
        // re-laying the mirror out every second would be work for nothing.
        if (changed) window.CanvasPanel?.refreshText?.();
    }

    function start() {
        if (pollTimer) return;
        poll().then(tick);
        pollTimer = setInterval(() => poll().then(tick), POLL_MS);
        tickTimer = setInterval(tick, TICK_MS);
    }

    function stop() {
        clearInterval(pollTimer); pollTimer = null;
        clearInterval(tickTimer); tickTimer = null;
        known = null;
        for (const node of rows()) {
            node.classList.remove('has-time');
            node.textContent = '';
        }
    }

    // A track change resets the count without waiting for the next poll,
    // so the old track's elapsed time is never shown against the new one.
    function reset() {
        known = null;
        if (pollTimer) poll().then(tick);
    }

    const api = { start, stop, reset, fmt,
                  _set: (k) => { known = k; }, _tick: tick };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;          // Node.js environment (unit tests)
    } else {
        window.ElapsedTime = api;
    }
})();
