/**
 * What fills the PLAYING screen behind the text.
 *
 * The device has always decided this for you: artwork first, then a Spotify
 * canvas or a music video if the track had one. This makes it a choice you
 * can turn to, on the same wheel that picks albums and stations — which does
 * nothing on PLAYING otherwise — and on the remote's FORMAT key.
 *
 *   Auto · Artwork · Canvas · Video · Tracks · VU · Lake · Lake blue · MilkDrop
 *
 * Auto is first and is the default, so a device nobody touches behaves
 * exactly as it did before.
 *
 * Two things about the list are deliberate:
 *
 * It is built per track from what actually exists. A track with no canvas
 * has no Canvas entry; a device with beo-visualizer stopped has no meters.
 * Stepping therefore never lands on something that would draw nothing.
 *
 * The choice is remembered by *name*, not by position. Keyed on position, a
 * track without a canvas would silently shift you from MilkDrop to Lake on
 * the way past. Pick MilkDrop and you keep MilkDrop.
 *
 * The chosen name lives in the router, not here, because there can be more
 * than one here: the same UI is shown on a television over Cast, and two
 * screens drawing different things would be worse than either of them.
 */
(function () {
    'use strict';

    // Carousel order. Each entry says when it is available; `apply` is what
    // happens on arrival, and runs again whenever availability changes.
    const CATALOGUE = [
        { id: 'auto',       label: 'AUTO',            always: true },
        { id: 'artwork',    label: 'ARTWORK',         always: true },
        { id: 'canvas',     label: 'CANVAS',          needs: () => !!window.CanvasPanel?.hasCanvas },
        { id: 'video',      label: 'MUSIC VIDEO',     needs: () => !!window.CanvasPanel?.hasMusicVideo },
        { id: 'tracks',     label: 'TRACKS',          needs: hasTrackList },
        { id: 'vu',         label: 'VU METERS',       needs: hasVisualizer },
        { id: 'lake',       label: 'SPECTRAL LAKE',   needs: hasVisualizer },
        { id: 'lake_blue',  label: 'SPECTRAL LAKE — BLUE', needs: hasVisualizer },
        { id: 'milkdrop',   label: 'MILKDROP',        needs: hasVisualizer },
    ];

    const VISUALIZER_IDS = ['vu', 'lake', 'lake_blue', 'milkdrop'];
    const TOAST_MS = 1800;

    let chosen = 'auto';
    let applied = null;          // what is actually on screen
    let toastEl = null, toastTimer = null;

    // ── Availability ──

    function hasVisualizer() {
        // Presence of the module is not enough: the feed has to be up. The
        // first turn of the wheel happens before any socket has been tried,
        // so an unmounted-but-present module counts as available and the
        // layer finds out for itself. A dead feed draws its resting state,
        // which is honest rather than blank.
        return !!window.BeoVisualizer;
    }

    function hasTrackList() {
        // CD only, and only once the flip card has a back to show.
        const ctrl = window.SourcePresets?.cd?.controller;
        return !!(ctrl && typeof ctrl.setFlipped === 'function' && ctrl.canFlip);
    }

    function available() {
        return CATALOGUE.filter(e => e.always || (e.needs && e.needs()));
    }

    function entry(id) {
        return CATALOGUE.find(e => e.id === id) || CATALOGUE[0];
    }

    // ── Applying ──

    // On the body, next to the canvas panel, and for the same reason.
    //
    // position:fixed resolves against the nearest ancestor with a
    // transform, and #viewport and #contentArea both carry one — the GPU
    // promotion hints in hardware-input.js. Mounted inside #now-playing
    // the layer inherited that box: right size, but starting 180px in,
    // leaving a strip of artwork down the left of the screen.
    function host() {
        return document.body;
    }

    // Only while PLAYING is on screen. The layer is a full-screen fixed
    // element, so left mounted it would cover whatever you navigated to.
    let onPlaying = true;

    function apply(id) {
        if (!onPlaying) {
            if (window.BeoVisualizer) window.BeoVisualizer.unmount();
            applied = null;
            return;
        }
        const list = available();
        // The chosen backdrop may not exist for this track. Fall back to
        // Auto for the moment rather than stepping to a neighbour, which
        // would quietly change what the user picked.
        const effective = list.some(e => e.id === id) ? id : 'auto';
        if (effective === applied) return;
        applied = effective;

        const wantsVisualizer = VISUALIZER_IDS.includes(effective);
        const wantsTracks = effective === 'tracks';

        // Spotify canvas and music video: suppressed unless they are what
        // was asked for. Auto leaves the panel to its own devices, which is
        // the behaviour this view has always had.
        if (window.CanvasPanel?.setSuppressed) {
            const free = (effective === 'auto' || effective === 'canvas' || effective === 'video');
            window.CanvasPanel.setSuppressed(!free);
            if (effective === 'canvas' || effective === 'video') window.CanvasPanel.show();
            else if (!free) window.CanvasPanel.hide();
        }

        if (window.BeoVisualizer) {
            if (wantsVisualizer) window.BeoVisualizer.mount(host(), effective);
            else window.BeoVisualizer.unmount();
        }

        const cd = window.SourcePresets?.cd?.controller;
        if (cd && typeof cd.setFlipped === 'function') cd.setFlipped(wantsTracks);
    }

    // ── Label ──

    function toast(text) {
        if (!toastEl) {
            toastEl = document.createElement('div');
            toastEl.className = 'backdrop-toast';
            document.body.appendChild(toastEl);
        }
        toastEl.textContent = text;
        toastEl.classList.add('show');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => toastEl.classList.remove('show'), TOAST_MS);
    }

    // ── Choosing ──

    function routerUrl(path) {
        const base = (typeof getServiceUrl === 'function')
            ? getServiceUrl('routerUrl', 8770)
            : (window.AppConfig?.routerUrl || 'http://localhost:8770');
        return base + path;
    }

    function publish(id) {
        // Optimistic: draw it now and tell the router, rather than waiting
        // for the broadcast to come back. A wheel that lags a round trip
        // feels broken even when it is only slow.
        chosen = id;
        apply(id);
        if (chosen !== applied) watchForArrival();
        toast(entry(id).label);
        fetch(routerUrl('/router/backdrop'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ backdrop: id }),
        }).catch(() => { /* router down; the local choice still stands */ });
    }

    function step(delta) {
        const list = available();
        if (!list.length) return;
        const at = list.findIndex(e => e.id === chosen);
        const next = list[((at < 0 ? 0 : at + delta) % list.length + list.length) % list.length];
        publish(next.id);
    }

    // ── Wheel ──
    //
    // Turning is continuous and the carousel is not, so steps are taken on
    // accumulated travel. The threshold is what stops a slow nudge from
    // skipping three backdrops at once.
    const STEP_THRESHOLD = 24;
    let travel = 0, lastNavAt = 0;

    function handleNav(data) {
        const now = performance.now();
        if (now - lastNavAt > 600) travel = 0;   // new gesture
        lastNavAt = now;
        travel += (data.direction === 'clock' ? 1 : -1) * (data.speed || 10);
        while (travel >= STEP_THRESHOLD) { travel -= STEP_THRESHOLD; step(1); }
        while (travel <= -STEP_THRESHOLD) { travel += STEP_THRESHOLD; step(-1); }
        return true;
    }

    // ── Events from the router ──

    function onBackdrop(data) {
        if (!data || typeof data.backdrop !== 'string') return;
        const same = data.backdrop === chosen;
        chosen = data.backdrop;
        apply(chosen);
        if (chosen !== applied) watchForArrival();
        if (!same) toast(entry(chosen).label);
    }

    function onBackdropStep(data) {
        step((data && data.delta < 0) ? -1 : 1);
    }

    // Availability moves with the track: a canvas arrives a second after
    // the metadata, a video later still, and both have to finish loading
    // before the panel counts them. A choice made in that gap falls back to
    // Auto and would stay there — the name is remembered but never
    // reconsidered, so picking Canvas on a track whose canvas is still
    // arriving leaves you on Auto for the rest of the song.
    //
    // So while the choice is not the thing on screen, look again. The check
    // is a handful of property reads, and it stops the moment the two agree,
    // which for most tracks is immediately.
    const SETTLE_MS = 2000;
    let settleTimer = null;

    function refresh() {
        applied = null;          // force re-evaluation
        apply(chosen);
    }

    function watchForArrival() {
        clearInterval(settleTimer);
        settleTimer = setInterval(() => {
            if (!onPlaying || chosen === applied) { clearInterval(settleTimer); return; }
            refresh();
        }, SETTLE_MS);
    }

    // The shell announces every view change; canvas-panel listens to the
    // same event for the same reason.
    document.addEventListener('bs5c:view-change', (e) => {
        const to = e?.detail?.to;
        const was = onPlaying;
        onPlaying = (to === 'menu/playing');
        if (was === onPlaying) return;
        applied = null;          // force a fresh decision either way
        apply(chosen);
        if (onPlaying && chosen !== applied) watchForArrival();
        if (!onPlaying) clearInterval(settleTimer);
    });

    // A new track brings new availability: the canvas and video the last
    // one had are gone, and this one's have not arrived yet.
    document.addEventListener('bs5c:media-update', () => {
        if (!onPlaying) return;
        refresh();
        if (chosen !== applied) watchForArrival();
    });

    function init() {
        onPlaying = (window.uiStore?.currentRoute === 'menu/playing');
        fetch(routerUrl('/router/backdrop'))
            .then(r => r.json())
            .then(d => { if (d && d.backdrop) { chosen = d.backdrop; apply(chosen); } })
            .catch(() => apply(chosen));
    }

    window.Backdrop = {
        CATALOGUE: CATALOGUE,
        get chosen() { return chosen; },
        get applied() { return applied; },
        available: available,
        step: step,
        set: publish,
        handleNav: handleNav,
        onBackdrop: onBackdrop,
        onBackdropStep: onBackdropStep,
        refresh: refresh,
        init: init,
    };

    if (typeof module !== 'undefined' && module.exports) {
        // Node.js environment (unit tests). No DOM, so nothing boots.
        module.exports = { CATALOGUE, available, step, publish, entry,
                           handleNav, onBackdrop, onBackdropStep, refresh,
                           get chosen() { return chosen; },
                           get applied() { return applied; },
                           _setChosen: (v) => { chosen = v; } };
    } else if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => setTimeout(init, 400));
    } else {
        setTimeout(init, 400);
    }
})();
