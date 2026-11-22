(function() {
    'use strict';

    // ── Configuration ──
    const IDLE_TIMEOUT = 30000;       // 30s inactivity before immersive
    const ARTWORK_MS = 600;           // transition duration for idle enter/exit
    const OVERLAY_ANGLE_START = 200;  // laser angle where immersive starts
    const OVERLAY_ANGLE_END = 210;    // laser angle where immersive is fully active
    const OVERLAY_RANGE = OVERLAY_ANGLE_END - OVERLAY_ANGLE_START; // 10 degrees

    // Transform targets at full immersive (progress = 1)
    const TX = -243, TY = 50, SCALE_EXTRA = 0.21;

    // ── State ──
    let progress = 0;           // 0 = normal, 1 = fully immersive
    let idleTimer = null;
    let isTracking = false;     // true while laser is actively driving progress
    let lastOverlayText = { title: '', artist: '', album: '' };
    let transitionCleanup = null;  // setTimeout ID for post-transition cleanup
    // Armed by ws-dispatcher on source_change. The next view-change to
    // menu/playing enters immersive eagerly — without waiting for
    // media to arrive — so a remote-triggered source start never
    // flashes the menu before immersive mode kicks in.
    let eagerEntryArmed = false;
    let eagerEntryArmedTimer = null;
    // Set by hardware-input when a physical GO on the PLAYING view is about
    // to resume playback. The next not-playing → playing edge consumes it
    // and stays in the menu: only starts from *elsewhere* (Sonos/Spotify
    // app, BeoRemote, HA) pull the screen into immersive.
    let localPlayPending = false;

    function isFullyImmersive() { return progress >= 1; }
    function isPartiallyImmersive() { return progress > 0; }

    // ── Playback state check ──
    function isPlaying() {
        const state = window.uiStore?.mediaInfo?.state;
        return state === 'playing' || state === 'TRANSITIONING';
    }

    // ── DOM helpers ──

    function getContainer() {
        return document.getElementById('now-playing');
    }

    function ensureOverlay() {
        const container = getContainer();
        if (!container) return null;
        let el = container.querySelector('.immersive-info');
        if (!el) {
            el = document.createElement('div');
            el.className = 'immersive-info';
            el.innerHTML =
                '<div class="immersive-info-state"></div>' +
                '<div class="immersive-info-title"></div>' +
                '<div class="immersive-info-artist"></div>' +
                '<div class="immersive-info-album"></div>' +
                // Same row as the normal panel. The canvas text mirror
                // clones this overlay wholesale, so adding it here is also
                // what puts the clock over a canvas or a music video.
                '<div class="immersive-info-time"></div>';
            container.appendChild(el);
        }
        return el;
    }

    // ── Apply visual state from progress value ──

    function applyProgress(p) {
        const container = getContainer();
        if (!container) return;

        const artwork = container.querySelector('.media-view-artwork');
        const info = container.querySelector('.media-view-info');
        const overlay = ensureOverlay();

        if (p <= 0) {
            // Fully normal
            container.classList.remove('immersive-active');
            if (artwork) artwork.style.transform = '';
            if (info) { info.style.opacity = ''; info.style.pointerEvents = ''; }
            if (overlay) overlay.style.opacity = '0';
            return;
        }

        // Partially or fully immersive
        container.classList.add('immersive-active');

        // Artwork transform: interpolate from identity to full immersive
        if (artwork) {
            artwork.style.transform = `translate(${TX * p}px, ${TY * p}px) scale(${1 + SCALE_EXTRA * p})`;
        }

        // Info fades out in the first half of progress
        if (info) {
            const infoOpacity = Math.max(0, 1 - p * 2);
            info.style.opacity = String(infoOpacity);
            info.style.pointerEvents = infoOpacity < 0.1 ? 'none' : '';
        }

        // Sync overlay text just before it becomes visible
        if (p > 0.4) {
            syncOverlayText(false);
        }

        // Overlay fades in during the second half of progress
        if (overlay) {
            const overlayOpacity = p > 0.5 ? (p - 0.5) * 2 : 0;
            overlay.style.opacity = String(overlayOpacity);
        }
    }

    // ── Canvas / music-video indicator dots ──

    function syncOverlayDots() {
        const overlay = ensureOverlay();
        if (!overlay) return;
        const mi = window.uiStore && window.uiStore.mediaInfo;
        const hasCanvas = !!(mi && mi.canvas_url);
        const hasVideo  = !!(mi && mi.music_video_url);
        const titleEl  = overlay.querySelector('.immersive-info-title');
        const artistEl = overlay.querySelector('.immersive-info-artist');
        if (titleEl)  titleEl.classList.toggle('has-canvas', hasCanvas);
        if (artistEl) artistEl.classList.toggle('has-video',  hasVideo);
    }

    // ── Smooth text update with per-field fade ──

    function syncOverlayText(animate) {
        const overlay = ensureOverlay();
        if (!overlay) return;

        const mi = window.uiStore?.mediaInfo;
        const newText = {
            title: mi?.title || '',
            artist: mi?.artist || '',
            album: mi?.album || ''
        };

        const fields = [
            { key: 'title', el: overlay.querySelector('.immersive-info-title') },
            { key: 'artist', el: overlay.querySelector('.immersive-info-artist') },
            { key: 'album', el: overlay.querySelector('.immersive-info-album') }
        ];

        for (const f of fields) {
            if (!f.el) continue;
            if (newText[f.key] === lastOverlayText[f.key]) continue;

            if (animate && isPartiallyImmersive()) {
                // Fade out, swap text, fade in
                f.el.style.opacity = '0';
                const newVal = newText[f.key];
                setTimeout(() => {
                    f.el.textContent = newVal;
                    f.el.style.opacity = '';
                }, 250);
            } else {
                // Instant update
                f.el.textContent = newText[f.key];
            }
        }

        lastOverlayText = { ...newText };
        syncOverlayDots();
    }

    // ── Laser-driven progressive animation ──

    function updateFromLaser() {
        const uiStore = window.uiStore;
        if (!uiStore || uiStore.currentRoute !== 'menu/playing') return;

        // Menu always wins — never track or transform artwork while the
        // menu is visible, or the large artwork overlaps menu items.
        if (uiStore.menuVisible) {
            if (isTracking) {
                isTracking = false;
                progress = 0;
                setTrackingMode(false);
                applyProgress(0);
            }
            return;
        }

        const angle = uiStore.wheelPointerAngle;

        if (angle >= OVERLAY_ANGLE_START) {
            // Laser is in overlay zone — track progressively
            const newProgress = Math.min(1, (angle - OVERLAY_ANGLE_START) / OVERLAY_RANGE);

            if (!isTracking && newProgress > 0) {
                isTracking = true;
                clearIdleTimer();
                // Enable short tracking transitions to smooth discrete laser steps
                setTrackingMode(true);
            }

            progress = newProgress;
            applyProgress(progress);
        } else if (isTracking) {
            // Laser moved back into menu zone — exit tracking
            isTracking = false;
            progress = 0;
            setTrackingMode(false);
            applyProgress(0);
            resetIdleTimer();
        }
    }

    // ── Transition mode control ──

    function setTrackingMode(on) {
        const container = getContainer();
        if (!container) return;
        container.classList.remove('immersive-transitioning');
        if (on) {
            container.classList.add('immersive-tracking');
        } else {
            container.classList.remove('immersive-tracking');
        }
    }

    function enableIdleTransitions() {
        const container = getContainer();
        if (!container) return;
        container.classList.remove('immersive-tracking');
        container.classList.add('immersive-transitioning');
    }

    function clearTransitions() {
        const container = getContainer();
        if (!container) return;
        container.classList.remove('immersive-transitioning', 'immersive-tracking');
    }

    // ── Animated enter/exit (for idle timer) ──

    function scheduleTransitionCleanup() {
        clearTimeout(transitionCleanup);
        transitionCleanup = setTimeout(() => {
            const c = getContainer();
            if (c) c.classList.remove('immersive-transitioning');
            transitionCleanup = null;
        }, ARTWORK_MS);
    }

    function animatedEnter() {
        if (isFullyImmersive() || isTracking) return;
        const container = getContainer();
        if (!container) return;

        syncOverlayText(false);
        enableIdleTransitions();

        // Force reflow so the transition actually animates from current state
        container.offsetHeight;

        progress = 1;
        applyProgress(1);
        scheduleTransitionCleanup();

        console.log('[IMMERSIVE] Entered (idle)');
    }

    function animatedExit() {
        if (!isPartiallyImmersive() || isTracking) return;
        const container = getContainer();
        if (!container) return;

        enableIdleTransitions();
        container.offsetHeight;

        progress = 0;
        applyProgress(0);
        scheduleTransitionCleanup();

        console.log('[IMMERSIVE] Exited (animated)');
    }

    function instantExit() {
        if (!isPartiallyImmersive()) return;
        isTracking = false;
        progress = 0;
        clearTimeout(transitionCleanup);
        transitionCleanup = null;
        clearTransitions();
        applyProgress(0);
    }

    // ── Idle timer ──

    function resetIdleTimer() {
        clearTimeout(idleTimer);
        idleTimer = null;
        const uiStore = window.uiStore;
        if (!uiStore || uiStore.currentRoute !== 'menu/playing') return;
        if (isFullyImmersive() || isTracking) return;

        idleTimer = setTimeout(() => {
            if (!uiStore || uiStore.currentRoute !== 'menu/playing') return;
            if (isFullyImmersive() || isTracking) return;
            if (!isPlaying()) return;  // only go immersive if something is playing
            uiStore.setMenuVisible(false);
            animatedEnter();
        }, IDLE_TIMEOUT);
    }

    function clearIdleTimer() {
        clearTimeout(idleTimer);
        idleTimer = null;
    }

    // ── Init: listen for UIStore events ──

    function init() {
        const uiStore = window.uiStore;
        if (!uiStore) { setTimeout(init, 200); return; }

        // Demo mode never enters immersive on its own. On a device, arriving at
        // a playing view and getting full-bleed artwork is the point; in the
        // browser emulator it means a visitor's first sight of the UI is an
        // album cover with the arc hidden. The idle timer and laser tracking
        // still work, so immersive is a few seconds or one scroll away.
        const eagerEntryAllowed = !window.AppConfig?.demo?.enabled;

        // 1. Menu visibility: exit immersive when menu reappears.
        // Menu always wins — if laser happens to be in the tracking zone
        // when the menu is unhidden, force isTracking=false so the large
        // transformed artwork doesn't overlap the menu.
        document.addEventListener('bs5c:menu-visibility', (e) => {
            if (e.detail.visible && isPartiallyImmersive()) {
                if (isTracking) {
                    isTracking = false;
                    setTrackingMode(false);
                }
                animatedExit();
            }
        });

        // 2. Wheel change: laser tracking + idle timer reset
        document.addEventListener('bs5c:wheel-change', () => {
            updateFromLaser();
            if (!isTracking) resetIdleTimer();
        });

        // 3. View change: cleanup on navigation
        document.addEventListener('bs5c:view-change', (e) => {
            const { from, to } = e.detail;
            const wasPlaying = from === 'menu/playing';
            const wasImmersive = isPartiallyImmersive();

            if (wasPlaying && to !== 'menu/playing') {
                clearIdleTimer();
                instantExit();
            }
            if (to === 'menu/playing') {
                // DOM was rebuilt by updateView() — overlay is gone, text cache is stale
                lastOverlayText = { title: '', artist: '', album: '' };
                if (wasImmersive && wasPlaying) {
                    // Re-apply immersive state after DOM rebuild (e.g. spurious wake)
                    setTimeout(() => {
                        ensureOverlay();
                        syncOverlayText(false);
                        applyProgress(progress);
                    }, 100);
                } else if (eagerEntryAllowed && (eagerEntryArmed || (!wasPlaying && isPlaying()))) {
                    // Either (a) a remote source-start just armed us, or
                    // (b) we're waking to an already-playing view. Go
                    // straight to immersive without waiting for media.
                    // The media_update that follows will populate the
                    // overlay text via the bs5c:media-text-updated path.
                    if (eagerEntryArmed) {
                        eagerEntryArmed = false;
                        clearTimeout(eagerEntryArmedTimer);
                        eagerEntryArmedTimer = null;
                    }
                    setTimeout(() => {
                        // User may have wheeled away during the delay —
                        // hiding the menu on a normal view would strand it.
                        if (uiStore.currentRoute !== 'menu/playing') return;
                        ensureOverlay();
                        uiStore.setMenuVisible(false);
                        animatedEnter();
                    }, 200);
                } else {
                    resetIdleTimer();
                    setTimeout(() => ensureOverlay(), 100);
                }
            }
        });

        // 4. Media text updated: sync overlay text on track change
        document.addEventListener('bs5c:media-text-updated', () => {
            syncOverlayText(true);
        });

        // 5. Media update: sync dots when canvas/video URLs arrive (async injection,
        //    may not change title/artist so bs5c:media-text-updated won't fire)
        document.addEventListener('bs5c:media-update', () => {
            syncOverlayDots();
        });

        // 6. Playback started (not-playing → playing edge, from media-manager).
        //    A start from somewhere other than this device's own buttons goes
        //    straight to immersive: navigate to PLAYING if we're elsewhere
        //    (the view-change path above enters immersive since isPlaying()
        //    is already true), or enter in place if we're already there —
        //    that's the case the backend's "navigate" wake can't cover, since
        //    navigating to the current route produces no view change.
        document.addEventListener('bs5c:playback-started', () => {
            if (localPlayPending) {
                localPlayPending = false;
                return;
            }
            if (!eagerEntryAllowed) return;
            if (uiStore.currentRoute !== 'menu/playing') {
                uiStore.navigateToView('menu/playing');
                return;
            }
            if (isFullyImmersive() || isTracking) return;
            ensureOverlay();
            uiStore.setMenuVisible(false);
            animatedEnter();
        });

        // Initial setup
        //
        // Demo mode skips the eager entry below. On a device, waking to a
        // playing view and getting full-bleed artwork is the point; in the
        // browser emulator it means a visitor's first sight of the UI is an
        // album cover with the arc hidden. They still get immersive from the
        // idle timer or by running the laser up, same as on hardware.
        if (uiStore.currentRoute === 'menu/playing' && !eagerEntryAllowed) {
            resetIdleTimer();
            setTimeout(() => ensureOverlay(), 100);
        } else if (uiStore.currentRoute === 'menu/playing') {
            // Media state may not be loaded yet — check periodically
            let startupChecks = 0;
            const startupCheck = () => {
                // Stop polling if the user navigated away during startup —
                // entering immersive here would hide the menu on a normal view.
                if (uiStore.currentRoute !== 'menu/playing') return;
                if (isPlaying() && !isPartiallyImmersive()) {
                    ensureOverlay();
                    uiStore.setMenuVisible(false);
                    animatedEnter();
                    return; // done — don't keep polling
                }
                startupChecks++;
                if (startupChecks < 10) {
                    setTimeout(startupCheck, 500);
                } else {
                    // Media never arrived — fall back to idle timer
                    resetIdleTimer();
                    ensureOverlay();
                }
            };
            setTimeout(startupCheck, 500);
        }

        console.log('[IMMERSIVE] Module initialized (v6.0)');
    }

    /** Arm immersive-on-first-view-to-menu/playing.
     *
     * Called by ws-dispatcher when source_change indicates a new
     * active source has been activated (remote button, digit, etc.).
     * The flag auto-expires in 3s so a stale arm can't linger. */
    function armEagerEntry() {
        eagerEntryArmed = true;
        clearTimeout(eagerEntryArmedTimer);
        eagerEntryArmedTimer = setTimeout(() => {
            eagerEntryArmed = false;
            eagerEntryArmedTimer = null;
        }, 3000);
    }

    /** A physical GO on the PLAYING view is about to resume playback —
     *  the resulting playback-started edge must not enter immersive. Only
     *  latches while not playing (a GO that pauses is not a start). */
    function noteLocalPlayIntent() {
        if (!isPlaying()) localPlayPending = true;
    }

    // Expose for debugging / manual toggle
    window.ImmersiveMode = {
        enter: animatedEnter,
        exit: animatedExit,
        armEagerEntry,
        noteLocalPlayIntent,
        get active() { return isPartiallyImmersive(); },
        get progress() { return progress; },
        syncText: () => syncOverlayText(false)
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => setTimeout(init, 200));
    } else {
        setTimeout(init, 200);
    }
})();
