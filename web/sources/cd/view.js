/**
 * CD View Controller
 *
 * Nav-wheel flip navigation: the wheel directly drives a 3D flip of album
 * artwork, revealing track list (down) or settings (up).
 *
 * Four phases arranged vertically:
 *   Phase -1: Settings       ↑ nav wheel up from artwork
 *   Phase  0: Front artwork  ← default
 *   Phase  1: Back artwork   ↓ nav wheel down (skipped if no back artwork)
 *   Phase  2: Track list     ↓ nav wheel down from back artwork
 *
 * Buttons:
 *   Artwork (0/1): LEFT/RIGHT=prev/next, GO=play/pause
 *   Tracks (2):    nav=scroll, GO=play selected, LEFT/RIGHT=back to artwork
 *   Settings (-1): nav=scroll, GO=activate, LEFT/RIGHT=back to artwork
 */
window.CDView = (() => {
    const CD_SERVICE_URL = window.AppConfig?.cdServiceUrl || 'http://localhost:8769';

    // ── State ──
    let initialized = false;
    let morphing = false;
    let metadata = null;

    // Content state
    let hasBackCover = false;

    // Flip state
    let phase = 0;            // -1, 0, 1, 2
    let flipProgress = 0;     // 0..1
    let flipTarget = null;    // target phase, null when idle
    let flipIsUp = false;     // true when flipping toward settings (lower phase)
    let snapTimer = null;     // idle timeout
    let isSnapping = false;   // true during snap/commit animation

    // List selection
    let trackSelected = 0;
    let settingsSelected = 0;
    let lastScrollTime = 0;
    const SCROLL_THROTTLE = 150; // ms between track list scroll steps

    /** Reset all transient state (called on destroy). Preserves metadata. */
    function resetState() {
        if (snapTimer) clearTimeout(snapTimer);
        initialized = false;
        morphing = false;
        phase = 0;
        flipProgress = 0;
        flipTarget = null;
        flipIsUp = false;
        snapTimer = null;
        isSnapping = false;
        trackSelected = 0;
        settingsSelected = 0;
    }

    // ── Lifecycle ──

    function init() {
        if (!document.getElementById('cd-view')) return;
        resetState();
        initialized = true;
        if (metadata) {
            applyMetadataToDOM(metadata);
        }
        console.log('[CD] View initialized');
    }

    function destroy() {
        resetState();
    }

    // ── 3D Flip ──

    /**
     * Determine flip target for a given direction from the current phase.
     * Returns target phase or null if flip not applicable.
     */
    function getFlipTarget(direction) {
        if (direction === 'clock') { // down
            if (phase === 0) return hasBackCover ? 1 : 2;
            if (phase === 1) return 2;
        } else { // counter-clock = up
            if (phase === 0) return -1;
            if (phase === 1) return 0;
        }
        return null;
    }

    /**
     * Get the face element for a given phase.
     */
    function getFaceEl(p) {
        switch (p) {
            case -1: return document.getElementById('cd-face-settings');
            case 0:  return document.getElementById('cd-face-artwork');
            case 1:  return document.getElementById('cd-face-back');
            case 2:  return document.getElementById('cd-face-tracks');
        }
        return null;
    }

    /**
     * Set up two faces for a flip: current at 0°, target at 180°.
     * Hide all other faces.
     */
    function startFlip(target) {
        flipTarget = target;
        flipProgress = 0;
        // Determine flip axis: "up" = toward settings (target is above current)
        // Phase order top-to-bottom: -1, 0, 1, 2
        flipIsUp = target < phase;

        // Build face content before showing
        if (target === 2) buildTracksFace();
        if (target === -1) buildSettingsFace();

        const flipper = document.getElementById('cd-flipper');
        if (!flipper) return;

        // Remove any snap transition
        flipper.classList.remove('cd-flipper-snap');
        flipper.style.transform = '';

        // Show current and target, hide others
        // Always flip around Y axis; "up" reverses sign for symmetric motion
        [-1, 0, 1, 2].forEach(p => {
            const el = getFaceEl(p);
            if (!el) return;
            if (p === phase) {
                el.classList.remove('cd-face-hidden');
                el.style.transform = 'rotateY(0deg)';
            } else if (p === target) {
                el.classList.remove('cd-face-hidden');
                el.style.transform = flipIsUp ? 'rotateY(-180deg)' : 'rotateY(180deg)';
            } else {
                el.classList.add('cd-face-hidden');
                el.style.transform = '';
            }
        });
    }

    /**
     * Advance flip progress and apply transform.
     */
    function advanceFlip(direction, speed) {
        const increment = 0.0304 + (speed * 0.00152);

        // Determine if this direction moves us toward or away from target
        const isForward = (direction === 'clock' && flipTarget > phase)
                       || (direction !== 'clock' && flipTarget < phase);

        if (isForward) {
            flipProgress = Math.min(flipProgress + increment, 1.0);
        } else {
            flipProgress = Math.max(flipProgress - increment, 0.0);
        }

        applyFlipTransform();

        // Commit immediately at 1.0
        if (flipProgress >= 1.0) {
            if (snapTimer) clearTimeout(snapTimer);
            commitFlip();
            return;
        }
        // Cancel immediately at 0.0
        if (flipProgress <= 0.0) {
            if (snapTimer) clearTimeout(snapTimer);
            cancelFlip();
            return;
        }

        // Reset idle timer
        resetSnapTimer();
    }

    /**
     * Apply the current flipProgress as a CSS transform on the flipper.
     */
    function applyFlipTransform() {
        const flipper = document.getElementById('cd-flipper');
        if (!flipper) return;

        const angle = flipProgress * 180;
        const tilt = flipProgress * 8;

        // Same axis (Y) both ways — "up" just reverses the sign
        if (flipIsUp) {
            flipper.style.transform = `rotateY(${-angle}deg) rotateX(${-tilt}deg)`;
        } else {
            flipper.style.transform = `rotateY(${angle}deg) rotateX(${-tilt}deg)`;
        }
    }

    /**
     * Reset the snap timer (called on each nav tick during flip).
     */
    function resetSnapTimer() {
        if (snapTimer) clearTimeout(snapTimer);
        snapTimer = setTimeout(snapFlip, 200);
    }

    /**
     * Idle timer callback: commit or snap back based on progress.
     */
    function snapFlip() {
        snapTimer = null;
        if (flipTarget === null) return;

        const flipper = document.getElementById('cd-flipper');
        if (!flipper) return;

        isSnapping = true;

        if (flipProgress >= 0.5) {
            // Animate to 180° (commit)
            flipper.classList.add('cd-flipper-snap');
            flipProgress = 1.0;
            applyFlipTransform();
            flipper.addEventListener('transitionend', function onEnd(e) {
                if (e.propertyName !== 'transform') return;
                flipper.removeEventListener('transitionend', onEnd);
                commitFlip();
            });
            // Safety fallback
            setTimeout(() => { if (isSnapping) commitFlip(); }, 300);
        } else {
            // Animate to 0° (snap back)
            flipper.classList.add('cd-flipper-snap');
            flipProgress = 0.0;
            applyFlipTransform();
            flipper.addEventListener('transitionend', function onEnd(e) {
                if (e.propertyName !== 'transform') return;
                flipper.removeEventListener('transitionend', onEnd);
                cancelFlip();
            });
            setTimeout(() => { if (isSnapping) cancelFlip(); }, 300);
        }
    }

    /**
     * Finalize a committed flip: set phase = target, reset flipper.
     */
    function commitFlip() {
        const flipper = document.getElementById('cd-flipper');
        if (!flipper) return;

        const oldPhase = phase;
        phase = flipTarget;
        flipTarget = null;
        flipProgress = 0;
        isSnapping = false;

        // Clean up: remove snap transition, reset transforms
        flipper.classList.remove('cd-flipper-snap');
        flipper.style.transform = '';

        // Show new current face at 0°, hide old face
        [-1, 0, 1, 2].forEach(p => {
            const el = getFaceEl(p);
            if (!el) return;
            if (p === phase) {
                el.classList.remove('cd-face-hidden');
                el.style.transform = '';
            } else {
                el.classList.add('cd-face-hidden');
                el.style.transform = '';
            }
        });

        // Update info text visibility
        updateInfoVisibility();

        console.log(`[CD] Flip committed: phase ${oldPhase} → ${phase}`);
    }

    /**
     * Cancel a flip: animate back, hide target face, reset.
     */
    function cancelFlip() {
        const flipper = document.getElementById('cd-flipper');
        if (!flipper) return;

        const target = flipTarget;
        flipTarget = null;
        flipProgress = 0;
        isSnapping = false;

        flipper.classList.remove('cd-flipper-snap');
        flipper.style.transform = '';

        // Hide the target face, keep current
        if (target !== null) {
            const el = getFaceEl(target);
            if (el) {
                el.classList.add('cd-face-hidden');
                el.style.transform = '';
            }
        }
        // Ensure current face is visible
        const curEl = getFaceEl(phase);
        if (curEl) {
            curEl.classList.remove('cd-face-hidden');
            curEl.style.transform = '';
        }
    }

    /**
     * Show/hide info text based on phase.
     * Visible in phases 0/1 (artwork), hidden in -1/2 (lists).
     */
    function updateInfoVisibility() {
        const infoEl = document.getElementById('cd-info');
        if (!infoEl) return;
        if (phase === 0 || phase === 1) {
            infoEl.style.opacity = '';
            infoEl.style.pointerEvents = '';
        } else {
            infoEl.style.opacity = '0';
            infoEl.style.pointerEvents = 'none';
        }
    }

    // ── Face Builders ──

    function buildTracksFace() {
        const face = document.getElementById('cd-face-tracks');
        if (!face || !metadata?.tracks?.length) return;

        const titleEl = face.querySelector('.cd-face-list-title');
        if (titleEl) titleEl.textContent = metadata.title || 'Unknown Album';

        const itemsEl = face.querySelector('.cd-face-list-items');
        if (!itemsEl) return;

        // Pre-select playing track
        const playingIdx = metadata.tracks.findIndex(t => t.num === metadata.current_track);
        trackSelected = playingIdx >= 0 ? playingIdx : 0;

        itemsEl.innerHTML = metadata.tracks.map((t, i) => {
            const playing = t.num === metadata.current_track ? ' cd-face-list-item-playing' : '';
            const selected = i === trackSelected ? ' cd-face-list-item-selected' : '';
            return `<div class="cd-face-list-item${playing}${selected}" data-index="${i}">`
                + `<span class="cd-face-track-num">${t.num}</span>`
                + `<span class="cd-face-track-name">${t.title}</span>`
                + `<span class="cd-face-track-dur">${t.duration || ''}</span>`
                + `</div>`;
        }).join('');
    }

    function buildSettingsFace() {
        const face = document.getElementById('cd-face-settings');
        if (!face) return;

        settingsSelected = 0;

        const headerEl = face.querySelector('.cd-face-list-header');
        if (headerEl) {
            const trackNum = metadata?.current_track || '?';
            const totalTracks = metadata?.tracks?.length || '?';
            headerEl.textContent = `Track ${trackNum} of ${totalTracks}`;
        }

        const itemsEl = face.querySelector('.cd-face-list-items');
        if (!itemsEl) return;

        itemsEl.innerHTML = `<div class="cd-face-list-item cd-face-list-item-selected" data-index="0" data-action="eject">Eject disc</div>`;
    }

    // ── List Scrolling ──

    function getListItems(p) {
        const face = getFaceEl(p);
        if (!face) return [];
        return face.querySelectorAll('.cd-face-list-item');
    }

    function scrollList(direction) {
        const now = performance.now();
        if (now - lastScrollTime < SCROLL_THROTTLE) return;
        lastScrollTime = now;

        const items = getListItems(phase);
        if (!items.length) return;

        const sel = phase === 2 ? trackSelected : settingsSelected;
        let next;
        if (direction === 'clock') {
            next = Math.min(sel + 1, items.length - 1);
        } else {
            next = Math.max(sel - 1, 0);
        }

        if (next === sel) return; // clamped at edge

        // Update selection
        items[sel]?.classList.remove('cd-face-list-item-selected');
        items[next]?.classList.add('cd-face-list-item-selected');
        items[next]?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });

        if (phase === 2) trackSelected = next;
        else settingsSelected = next;
    }

    // ── Event Handlers ──

    /**
     * Handle nav wheel. Returns true if consumed.
     */
    function handleNavEvent(data) {
        if (!initialized) return false;

        // During snap animation, consume but ignore
        if (isSnapping) return true;

        // During any active flip, advance it (regardless of which phase started it)
        if (flipTarget !== null) {
            advanceFlip(data.direction, data.speed || 10);
            return true;
        }

        // Phase 2 or -1 (list views): scroll, or flip back to artwork at edges
        if (phase === 2 || phase === -1) {
            // At top of tracks, scrolling up → animated flip back to artwork
            if (phase === 2 && data.direction !== 'clock' && trackSelected === 0) {
                const target = hasBackCover ? 1 : 0;
                startFlip(target);
                advanceFlip(data.direction, data.speed || 10);
                return true;
            }
            // At bottom of settings, scrolling down → animated flip back to artwork
            if (phase === -1 && data.direction === 'clock') {
                const items = getListItems(-1);
                if (settingsSelected >= items.length - 1) {
                    startFlip(0);
                    advanceFlip(data.direction, data.speed || 10);
                    return true;
                }
            }
            scrollList(data.direction);
            return true;
        }

        // Phase 0 or 1 (artwork views): start a new flip
        const target = getFlipTarget(data.direction);
        if (target !== null) {
            startFlip(target);
            advanceFlip(data.direction, data.speed || 10);
        }

        return true;
    }

    /**
     * Handle button press. Returns true if consumed.
     */
    function handleButton(button) {
        if (!initialized) return false;

        // Mid-flip: consume but ignore
        if (flipTarget !== null || isSnapping) return true;

        // Phase 2: track list
        if (phase === 2) {
            if (button === 'go') {
                const items = getListItems(2);
                const item = items[trackSelected];
                if (item && metadata?.tracks?.[trackSelected]) {
                    sendCommand('play_track', { track: metadata.tracks[trackSelected].num });
                }
                return true;
            }
            if (button === 'left' || button === 'right') {
                goToArtwork();
                return true;
            }
            return true;
        }

        // Phase -1: settings
        if (phase === -1) {
            if (button === 'go') {
                const items = getListItems(-1);
                const item = items[settingsSelected];
                if (item) {
                    const action = item.dataset.action;
                    if (action === 'eject') sendCommand('eject');
                }
                return true;
            }
            if (button === 'left' || button === 'right') {
                goToArtwork();
                return true;
            }
            return true;
        }

        // Phase 1: back artwork — LEFT/RIGHT return to front, GO is no-op
        if (phase === 1) {
            if (button === 'left' || button === 'right') { goToArtwork(); return true; }
            return true;
        }

        // Phase 0: front artwork
        if (button === 'left') { sendCommand('prev'); return true; }
        if (button === 'right') { sendCommand('next'); return true; }
        if (button === 'go') { sendCommand('toggle'); return true; }

        return false;
    }

    /**
     * Instant jump back to artwork (phase 0) from a list view.
     */
    function goToArtwork() {
        phase = 0;
        flipTarget = null;
        flipProgress = 0;
        if (snapTimer) clearTimeout(snapTimer);
        isSnapping = false;

        const flipper = document.getElementById('cd-flipper');
        if (flipper) {
            flipper.classList.remove('cd-flipper-snap');
            flipper.style.transform = '';
        }

        [-1, 0, 1, 2].forEach(p => {
            const el = getFaceEl(p);
            if (!el) return;
            if (p === 0) {
                el.classList.remove('cd-face-hidden');
                el.style.transform = '';
            } else {
                el.classList.add('cd-face-hidden');
                el.style.transform = '';
            }
        });

        updateInfoVisibility();
    }

    // ── Metadata ──

    /**
     * Called from ws-dispatcher on cd_update WebSocket events.
     */
    function updateMetadata(data) {
        const prevArtwork = metadata?.artwork;
        const prevTrack = metadata?.current_track;
        metadata = data;
        hasBackCover = !!data.back_artwork;

        if (!initialized) return;

        const loadingEl = document.getElementById('cd-loading');
        const isFirstReveal = !morphing
            && loadingEl && !loadingEl.classList.contains('cd-hidden');

        // Reset to front cover when artwork changes (new disc)
        if (data.artwork !== prevArtwork) {
            goToArtwork();
        }

        if (isFirstReveal) {
            morphing = true;
            if (data.artwork) {
                const img = new Image();
                img.onload = () => applyMetadataToDOM(data, prevTrack, true);
                img.onerror = () => applyMetadataToDOM(data, prevTrack, true);
                img.src = data.artwork;
            } else {
                applyMetadataToDOM(data, prevTrack, true);
            }
        } else if (morphing) {
            // Morph in progress — update text info (track/state) without touching the morph animation
            applyTrackText(data, prevTrack);
        } else {
            applyMetadataToDOM(data, prevTrack);
        }
    }

    /**
     * Apply stored metadata directly to DOM elements.
     */
    function applyMetadataToDOM(data, prevTrack, morph) {
        const loadingEl = document.getElementById('cd-loading');
        const artworkArea = document.getElementById('cd-artwork-area');
        const infoEl = document.getElementById('cd-info');

        if (morph) {
            if (artworkArea) {
                artworkArea.classList.remove('cd-hidden');
                artworkArea.style.visibility = 'visible';
                artworkArea.style.opacity = '0';
            }
            if (infoEl) { infoEl.classList.remove('cd-hidden'); infoEl.style.visibility = 'visible'; infoEl.style.opacity = '0'; }
        } else {
            if (loadingEl) loadingEl.classList.add('cd-hidden');
            if (artworkArea) artworkArea.classList.remove('cd-hidden');
            if (infoEl) infoEl.classList.remove('cd-hidden');
        }

        applyArtwork(data);
        applyTrackText(data, prevTrack);
        if (!morph) updateInfoVisibility();

        // Rebuild track list if currently viewing it (phase 2) so playing indicator updates
        if (phase === 2) buildTracksFace();

        console.log(`[CD] Metadata: ${data.artist} — ${data.title}, track ${data.current_track}, state ${data.state}`);

        if (morph) runMorphTransition(loadingEl, artworkArea, infoEl);
    }

    function applyArtwork(data) {
        const bust = `?t=${Date.now()}`;
        const front = document.getElementById('cd-artwork-front');
        if (front) {
            if (data.artwork) {
                front.onerror = () => { front.onerror = null; front.src = 'assets/cd-disc.png'; };
                front.src = data.artwork + bust;
            } else {
                front.src = 'assets/cd-disc.png';
            }
        }
        const back = document.getElementById('cd-artwork-back');
        if (back) {
            if (data.back_artwork) {
                back.src = data.back_artwork + bust;
            } else {
                back.src = '';
            }
        }
    }

    function applyTrackText(data, prevTrack) {
        const titleEl = document.querySelector('#cd-view .media-view-title');
        const artistEl = document.querySelector('#cd-view .media-view-artist');
        const albumEl = document.querySelector('#cd-view .media-view-album');

        let newTitle = '', newArtist = '', newAlbum = '';

        if (data.current_track && data.tracks?.length) {
            const track = data.tracks.find(t => t.num === data.current_track);
            newTitle = track ? track.title : `Track ${data.current_track}`;
            newArtist = data.artist || '';
            newAlbum = data.year
                ? `${data.title} (${data.year})`
                : data.title || '';
        } else {
            newTitle = data.title || '';
            newArtist = data.artist || '';
            newAlbum = data.album || '';
        }

        if (prevTrack && data.current_track && prevTrack !== data.current_track && titleEl) {
            animateTrackChange(titleEl, artistEl, newTitle, newArtist);
        } else {
            if (titleEl) titleEl.textContent = newTitle;
            if (artistEl) artistEl.textContent = newArtist;
        }
        if (albumEl) albumEl.textContent = newAlbum;
    }

    function runMorphTransition(loadingEl, artworkArea, infoEl) {
        // Double-rAF ensures the browser has committed the initial paint (opacity:0)
        // before starting the transition to opacity:1 — more reliable on RPi Chromium
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                if (loadingEl) { loadingEl.style.transition = 'opacity 0.8s ease-out'; loadingEl.style.opacity = '0'; }
                if (artworkArea) { artworkArea.style.transition = 'opacity 0.8s ease-in'; artworkArea.style.opacity = '1'; }
                if (infoEl) { infoEl.style.transition = 'opacity 0.6s ease-in 0.3s'; infoEl.style.opacity = '1'; }
            });
        });

        // Timeout-based cleanup (more reliable than transitionend on RPi Chromium)
        setTimeout(() => {
            if (!morphing) return;
            if (loadingEl) { loadingEl.classList.add('cd-hidden'); loadingEl.style.transition = ''; loadingEl.style.opacity = ''; }
            if (artworkArea) { artworkArea.style.transition = ''; artworkArea.style.opacity = ''; artworkArea.style.visibility = ''; }
            if (infoEl) { infoEl.style.transition = ''; infoEl.style.opacity = ''; infoEl.style.visibility = ''; }
            morphing = false;
            if (metadata) applyMetadataToDOM(metadata, null);
        }, 1200);
    }

    function animateTrackChange(titleEl, artistEl, newTitle, newArtist) {
        let cleaned = false;
        const elements = [titleEl, artistEl].filter(Boolean);

        function cleanup() {
            if (cleaned) return;
            cleaned = true;
            if (titleEl) titleEl.textContent = newTitle;
            if (artistEl) artistEl.textContent = newArtist;
            elements.forEach(el => {
                el.classList.remove('cd-track-transition', 'cd-track-exit', 'cd-track-enter');
            });
        }

        const safetyTimer = setTimeout(cleanup, 800);

        elements.forEach(el => el.classList.add('cd-track-transition', 'cd-track-exit'));

        titleEl.addEventListener('transitionend', function exitDone(e) {
            if (e.propertyName !== 'opacity') return;
            titleEl.removeEventListener('transitionend', exitDone);
            if (cleaned) return;

            if (titleEl) titleEl.textContent = newTitle;
            if (artistEl) artistEl.textContent = newArtist;

            elements.forEach(el => { el.classList.remove('cd-track-exit'); el.classList.add('cd-track-enter'); });
            void titleEl?.offsetHeight;

            requestAnimationFrame(() => {
                elements.forEach(el => el.classList.remove('cd-track-enter'));
                titleEl.addEventListener('transitionend', function enterDone(e) {
                    if (e.propertyName !== 'opacity') return;
                    titleEl.removeEventListener('transitionend', enterDone);
                    clearTimeout(safetyTimer);
                    cleanup();
                });
            });
        });
    }

    // ── Commands ──

    async function sendCommand(command, params = {}) {
        try {
            const resp = await fetch(`${CD_SERVICE_URL}/command`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command, ...params })
            });
            if (resp.ok) {
                const result = await resp.json();
                console.log(`[CD] ${command}:`, result.playback?.state || 'ok');
            }
        } catch {
            console.warn(`[CD] ${command} failed (service not running?)`);
        }
    }

    // ── Public API ──
    return {
        init,
        destroy,
        handleNavEvent,
        handleButton,
        updateMetadata,
        sendCommand,
        get isActive() { return initialized; },
        get isFlipping() { return flipTarget !== null; }
    };
})();

// ── CD Source Preset ──
// Self-registers into the global SourcePresets registry so the UI knows
// how to render the CD menu item and customise the PLAYING view.
window.SourcePresets = window.SourcePresets || {};
window.SourcePresets.cd = {
    controller: window.CDView,
    item: { title: 'CD', path: 'menu/cd' },
    after: 'menu/music',
    view: {
        title: 'CD',
        content: `
            <div id="cd-view" class="media-view">
                <div id="cd-loading" class="cd-loading-state">
                    <div class="cd-disc"></div>
                    <div class="cd-loading-text">Reading disc<span class="cd-dots"><span>.</span><span>.</span><span>.</span></span></div>
                </div>
                <div id="cd-artwork-area" class="media-view-artwork cd-artwork-container cd-hidden">
                    <div id="cd-flipper" class="cd-flipper">
                        <div id="cd-face-artwork" class="cd-face">
                            <img id="cd-artwork-front" src="assets/cd-disc.png">
                        </div>
                        <div id="cd-face-back" class="cd-face cd-face-hidden">
                            <img id="cd-artwork-back" src="">
                        </div>
                        <div id="cd-face-tracks" class="cd-face cd-face-hidden cd-face-list">
                            <div class="cd-face-list-title"></div>
                            <div class="cd-face-list-items"></div>
                        </div>
                        <div id="cd-face-settings" class="cd-face cd-face-hidden cd-face-list">
                            <div class="cd-face-list-header"></div>
                            <div class="cd-face-list-items"></div>
                        </div>
                    </div>
                </div>
                <div id="cd-info" class="media-view-info cd-hidden">
                    <div class="media-view-title"></div>
                    <div class="media-view-artist"></div>
                    <div class="media-view-album"></div>
                </div>
            </div>`
    },

    onAdd() {},

    onMount() {
        if (window.CDView) window.CDView.init();
    },

    onRemove() {
        if (window.CDView) window.CDView.destroy();
    },

    // PLAYING sub-preset: customises the PLAYING view when CD is active.
    // Uses the default artwork slot (front/back flipper) — just provides data.
    playing: {
        eventType: 'cd_update',

        onUpdate(container, data) {
            const track = data.tracks?.[data.current_track - 1];
            const titleEl = container.querySelector('.media-view-title');
            const artistEl = container.querySelector('.media-view-artist');
            const albumEl = container.querySelector('.media-view-album');
            if (titleEl) titleEl.textContent = track?.title || `Track ${data.current_track}`;
            if (artistEl) artistEl.textContent = data.artist || '—';
            if (albumEl) albumEl.textContent = data.title || '—';
            // Front artwork
            const front = container.querySelector('.playing-artwork');
            if (front) front.src = data.artwork || '';
            // Back artwork (show/hide back face)
            const backFace = container.querySelector('.playing-back');
            const backImg = container.querySelector('.playing-artwork-back');
            if (backFace && backImg && data.back_artwork) {
                backImg.src = data.back_artwork;
                backFace.style.display = '';
            }
        }
    }
};
