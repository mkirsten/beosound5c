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
    let overlayTextSynced = false; // whether text has been synced for current immersive session
    let transitionCleanup = null;  // setTimeout ID for post-transition cleanup

    function isFullyImmersive() { return progress >= 1; }
    function isPartiallyImmersive() { return progress > 0; }

    // ── Playback state check ──
    function isPlaying() {
        const state = window.uiStore?.mediaInfo?.state;
        return state === 'PLAYING' || state === 'TRANSITIONING';
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
                '<div class="immersive-info-title"></div>' +
                '<div class="immersive-info-artist"></div>' +
                '<div class="immersive-info-album"></div>';
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
            overlayTextSynced = false;
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
        if (p > 0.4 && !overlayTextSynced) {
            syncOverlayText(false);
            overlayTextSynced = true;
        }

        // Overlay fades in during the second half of progress
        if (overlay) {
            const overlayOpacity = p > 0.5 ? (p - 0.5) * 2 : 0;
            overlay.style.opacity = String(overlayOpacity);
        }
    }

    // ── Smooth text update with per-field fade ──

    function getMediaText() {
        // Read from DOM first (reflects source-specific formatting, e.g. CD track titles),
        // fall back to uiStore.mediaInfo (always populated by Sonos/generic path)
        const titleEl = document.getElementById('media-title');
        const artistEl = document.getElementById('media-artist');
        const albumEl = document.getElementById('media-album');
        const mi = window.uiStore?.mediaInfo;

        return {
            title: (titleEl?.textContent && titleEl.textContent !== '\u2014') ? titleEl.textContent : (mi?.title || ''),
            artist: (artistEl?.textContent && artistEl.textContent !== '\u2014') ? artistEl.textContent : (mi?.artist || ''),
            album: (albumEl?.textContent && albumEl.textContent !== '\u2014') ? albumEl.textContent : (mi?.album || '')
        };
    }

    function syncOverlayText(animate) {
        const overlay = ensureOverlay();
        if (!overlay) return;

        const newText = getMediaText();

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
    }

    // ── Laser-driven progressive animation ──

    function updateFromLaser() {
        const uiStore = window.uiStore;
        if (!uiStore || uiStore.currentRoute !== 'menu/playing') return;

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
        overlayTextSynced = true;
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

    // ── Init: wrap UIStore methods ──

    function init() {
        const uiStore = window.uiStore;
        if (!uiStore) { setTimeout(init, 200); return; }

        // 1. Wrap setMenuVisible: exit immersive when menu reappears
        const origSetMenuVisible = uiStore.setMenuVisible.bind(uiStore);
        uiStore.setMenuVisible = function(visible) {
            if (visible && isPartiallyImmersive() && !isTracking) {
                animatedExit();
            }
            origSetMenuVisible(visible);
        };

        // 2. Wrap handleWheelChange: laser tracking + idle timer reset
        const origHandleWheelChange = uiStore.handleWheelChange.bind(uiStore);
        uiStore.handleWheelChange = function() {
            origHandleWheelChange();
            updateFromLaser();
            if (!isTracking) resetIdleTimer();
        };

        // 3. Wrap navigateToView: cleanup on view change
        const origNavigate = uiStore.navigateToView.bind(uiStore);
        uiStore.navigateToView = function(path) {
            const wasPlaying = uiStore.currentRoute === 'menu/playing';
            const wasImmersive = isPartiallyImmersive();
            origNavigate(path);
            if (wasPlaying && path !== 'menu/playing') {
                clearIdleTimer();
                instantExit();
            }
            if (path === 'menu/playing') {
                // DOM was rebuilt by updateView() — overlay is gone, text cache is stale
                lastOverlayText = { title: '', artist: '', album: '' };
                overlayTextSynced = false;
                if (wasImmersive && wasPlaying) {
                    // Re-apply immersive state after DOM rebuild (e.g. spurious wake)
                    setTimeout(() => {
                        ensureOverlay();
                        syncOverlayText(false);
                        overlayTextSynced = true;
                        applyProgress(progress);
                    }, 100);
                } else if (!wasPlaying && isPlaying()) {
                    // Waking to playing view while music is active — go straight to immersive
                    setTimeout(() => {
                        ensureOverlay();
                        uiStore.setMenuVisible(false);
                        animatedEnter();
                    }, 200);
                } else {
                    resetIdleTimer();
                    setTimeout(() => ensureOverlay(), 100);
                }
            }
        };

        // 4. Wrap handleMediaUpdate: sync overlay text on track change
        //    crossfadeText swaps DOM text after 200ms, so wait for that
        const origHandleMediaUpdate = uiStore.handleMediaUpdate.bind(uiStore);
        uiStore.handleMediaUpdate = function(data, reason) {
            origHandleMediaUpdate(data, reason);
            setTimeout(() => syncOverlayText(true), 250);
        };

        // 5. Wrap updatePlaying: sync overlay text on source-routed updates
        //    crossfadeText swaps DOM text after 200ms, so wait for that
        const origUpdatePlaying = uiStore.updatePlaying.bind(uiStore);
        uiStore.updatePlaying = function(data) {
            origUpdatePlaying(data);
            setTimeout(() => syncOverlayText(true), 250);
        };

        // Initial setup
        if (uiStore.currentRoute === 'menu/playing') {
            resetIdleTimer();
            ensureOverlay();
        }

        console.log('[IMMERSIVE] Module initialized (v5.1)');
    }

    // Expose for debugging / manual toggle
    window.ImmersiveMode = {
        enter: animatedEnter,
        exit: animatedExit,
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
