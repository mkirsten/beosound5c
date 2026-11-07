/**
 * CD View Controller
 *
 * Two contexts:
 *   menu/cd      → Arc track browser (matches softarc positioning exactly)
 *   menu/playing → Nav wheel drives progressive artwork flip; LEFT/RIGHT=prev/next, GO=toggle
 *
 * The arc browser shows tracks from the current disc with circular number badges.
 * Last item is always "Eject CD". Nav wheel scrolls, GO plays/ejects.
 */
window.CDView = (() => {
    const CD_SERVICE_URL = window.AppConfig?.cdServiceUrl || 'http://localhost:8769';

    // ── State ──
    let menuActive = false;
    let metadata = null;

    // Arc browser state — matches softarc exactly
    let arcItems = [];           // [{id, label, type, ...}]
    let arcTargetIndex = 0;
    let arcCurrentIndex = 0;
    let arcAnimFrame = null;
    let arcSnapTimer = null;
    let lastScrollTime = 0;
    let lastClickedItemId = null; // For click sound on selection change

    // Submenu state
    let viewMode = 'main';       // 'main' | 'settings' | 'artwork'
    let savedMainIndex = 0;      // Scroll position when entering submenu

    // Softarc constants (shared via ArcMath)
    const _ac = ArcMath.getConstants();
    const SCROLL_SPEED = _ac.scrollSpeed;
    const SCROLL_STEP = _ac.scrollStep;
    const SNAP_DELAY = _ac.snapDelay;
    const MIDDLE_INDEX = _ac.middleIndex;
    const BASE_ITEM_SIZE = _ac.baseItemSize;
    const MAX_RADIUS = _ac.maxRadius;
    const HORIZONTAL_MULTIPLIER = _ac.horizontalMultiplier;
    const BASE_X_OFFSET = _ac.baseXOffset;

    // Morph (loading → content transition)
    let morphing = false;
    let pendingMorphData = null;  // queued cd_update during morph

    // Playing flip state (progressive, follows nav wheel)
    let flipProgress = 0;        // 0..1: how far through current flip gesture
    let flipIsFlipped = false;   // which face is "home" — false=front, true=back
    let flipDirection = null;    // 'clock' or 'counter' — direction that started this flip
    let flipSnapTimer = null;

    /** Reset transient state. Preserves metadata. */
    function resetState() {
        if (arcSnapTimer) clearTimeout(arcSnapTimer);
        if (arcAnimFrame) cancelAnimationFrame(arcAnimFrame);
        if (flipSnapTimer) clearTimeout(flipSnapTimer);
        menuActive = false;
        morphing = false;
        arcItems = [];
        arcTargetIndex = 0;
        arcCurrentIndex = 0;
        arcAnimFrame = null;
        arcSnapTimer = null;
        lastScrollTime = 0;
        lastClickedItemId = null;
        viewMode = 'main';
        savedMainIndex = 0;
        flipProgress = 0;
        flipIsFlipped = false;
        flipDirection = null;
        flipSnapTimer = null;
        pendingMorphData = null;
    }

    // ── Lifecycle ──

    function init() {
        if (!document.getElementById('cd-view')) return;
        resetState();
        menuActive = true;
        if (metadata) {
            // Disc already known — skip loading, show arc immediately
            const loadingEl = document.getElementById('cd-loading');
            if (loadingEl) loadingEl.classList.add('cd-hidden');
            const arcContainer = document.getElementById('cd-arc-container');
            if (arcContainer) arcContainer.classList.remove('cd-hidden');
            buildArcItems();
            scrollToPlayingTrack();
            renderArc();
            startAnimation();
        }
        console.log('[CD] View initialized');
    }

    function destroy() {
        resetState();
        // Keep metadata across navigation — next cd_update replaces it naturally.
        // When disc is ejected, menu item is removed so stale data is unreachable.
    }

    // ── Arc Browser ──

    /** Build arc items array from metadata (or submenu). Does NOT touch scroll position. */
    function buildArcItems() {
        if (viewMode === 'settings') { arcItems = buildSettingsItems(); return; }
        if (viewMode === 'artwork') { arcItems = buildArtworkItems(); return; }

        if (!metadata?.tracks?.length) {
            arcItems = [];
            return;
        }

        // Tracks
        arcItems = metadata.tracks.map(t => ({
            id: `track-${t.num}`, label: t.title || `Track ${t.num}`,
            trackNum: t.num, duration: t.duration || '', type: 'track'
        }));

        // Play Settings submenu
        arcItems.push({
            id: 'settings', label: 'Play Settings',
            type: 'submenu', badgeIcon: '\u2699'
        });

        // Artwork submenu (only if alternatives exist)
        if (metadata.alternatives?.length) {
            arcItems.push({
                id: 'artwork', label: 'Artwork',
                type: 'submenu', badgeImage: metadata.artwork
            });
        }

        // Import Disc (only if external USB drive detected)
        if (metadata.has_external_drive) {
            arcItems.push({
                id: 'import', label: 'Import Disc',
                type: 'action', badgeIcon: '\u2B73'
            });
        }

        // Eject
        arcItems.push({
            id: 'eject', label: 'Eject CD',
            type: 'action', badgeIcon: '\u23CF'
        });
    }

    function buildSettingsItems() {
        return [
            { id: 'back', label: '\u2190 Back', type: 'back', badgeIcon: '\u2190' },
            {
                id: 'repeat', type: 'toggle',
                label: `Repeat: ${metadata?.repeat ? 'On' : 'Off'}`,
                toggleOn: !!metadata?.repeat,
                badgeIcon: '\uD83D\uDD01',
                command: 'toggle_repeat'
            },
            {
                id: 'shuffle', type: 'toggle',
                label: `Shuffle: ${metadata?.shuffle ? 'On' : 'Off'}`,
                toggleOn: !!metadata?.shuffle,
                badgeIcon: '\uD83D\uDD00',
                command: 'toggle_shuffle'
            }
        ];
    }

    function buildArtworkItems() {
        const items = [
            { id: 'back', label: '\u2190 Back', type: 'back', badgeIcon: '\u2190' }
        ];
        for (const alt of (metadata?.alternatives || [])) {
            items.push({
                id: `release-${alt.release_id}`,
                label: `${alt.title}${alt.year ? ' (' + alt.year + ')' : ''}`,
                sublabel: alt.artist,
                type: 'release',
                releaseId: alt.release_id,
                badgeIcon: '\uD83C\uDFB5'
            });
        }
        return items;
    }

    function enterSubmenu(mode) {
        savedMainIndex = arcTargetIndex;
        viewMode = mode;
        buildArcItems();
        arcTargetIndex = 0;
        arcCurrentIndex = 0;
        lastClickedItemId = null;
        renderArc();
    }

    function exitSubmenu() {
        viewMode = 'main';
        buildArcItems();
        arcTargetIndex = Math.min(savedMainIndex, arcItems.length - 1);
        arcCurrentIndex = arcTargetIndex;
        lastClickedItemId = null;
        renderArc();
    }

    /** Set scroll position to the currently playing track. */
    function scrollToPlayingTrack() {
        const playingIdx = metadata?.tracks?.findIndex(t => t.num === metadata.current_track) ?? -1;
        arcTargetIndex = playingIdx >= 0 ? playingIdx : 0;
        arcCurrentIndex = arcTargetIndex;
    }

    /** Compute visible items with position/scale — delegates to shared ArcMath. */
    function getVisibleItems() {
        return ArcMath.getVisibleItems(arcCurrentIndex, arcItems);
    }

    /**
     * Update existing DOM elements in-place (matches softarc updateExistingElements).
     * Returns true if successful, false if full rebuild needed.
     */
    function updateExistingElements(container) {
        const existingItems = Array.from(container.querySelectorAll('.cd-arc-item'));
        const visibleItems = getVisibleItems();

        // If item count doesn't match, need full render
        if (existingItems.length !== visibleItems.length) {
            return false;
        }

        // Check if items are the same (by data-item-id)
        for (let i = 0; i < existingItems.length; i++) {
            if (!existingItems[i] || !visibleItems[i] ||
                existingItems[i].dataset.itemId !== visibleItems[i].id) {
                return false;
            }
        }

        // Update positions of existing elements
        existingItems.forEach((element, index) => {
            const item = visibleItems[index];
            if (!item) return;

            // Update transform
            element.style.transform = `translate(${item.x}px, ${item.y}px) scale(${item.scale})`;

            // Update selected state
            const nameEl = element.querySelector('.cd-arc-item-name');
            if (item.isSelected && !element.classList.contains('cd-arc-item-selected')) {
                element.classList.add('cd-arc-item-selected');
                if (nameEl) nameEl.classList.add('selected');
            } else if (!item.isSelected && element.classList.contains('cd-arc-item-selected')) {
                element.classList.remove('cd-arc-item-selected');
                if (nameEl) nameEl.classList.remove('selected');
            }

            // Update playing state (track changes between adjacent tracks)
            const isPlaying = item.type === 'track' && item.trackNum === metadata?.current_track;
            element.classList.toggle('cd-arc-item-playing', isPlaying);

            // Update toggle state (for settings submenu live updates)
            if (item.type === 'toggle') {
                element.classList.toggle('cd-arc-item-toggle-on', !!item.toggleOn);
                if (nameEl) nameEl.textContent = item.label;
            }
        });

        return true;
    }

    /**
     * Full render — clears and rebuilds DOM (only when items change).
     */
    function renderArc() {
        const container = document.getElementById('cd-arc-container');
        if (!container || !arcItems.length) return;

        // Try in-place update first (matches softarc optimization)
        if (updateExistingElements(container)) {
            return;
        }

        // Full rebuild needed (items changed)
        container.innerHTML = '';

        const visibleItems = getVisibleItems();

        for (const item of visibleItems) {
            const isPlaying = item.type === 'track' && item.trackNum === metadata?.current_track;

            const el = document.createElement('div');
            el.className = 'cd-arc-item leaf';
            el.dataset.itemId = item.id;
            if (item.isSelected) el.classList.add('cd-arc-item-selected');
            if (isPlaying) el.classList.add('cd-arc-item-playing');
            if (item.type === 'action' && item.id === 'eject') el.classList.add('cd-arc-item-eject');
            if (item.type === 'back') el.classList.add('cd-arc-item-back');
            if (item.type === 'toggle' && item.toggleOn) el.classList.add('cd-arc-item-toggle-on');
            el.style.transform = `translate(${item.x}px, ${item.y}px) scale(${item.scale})`;

            // Text wrapper (name + optional duration/sublabel)
            const textEl = document.createElement('div');
            textEl.className = 'cd-arc-item-text';

            const nameEl = document.createElement('div');
            nameEl.className = 'cd-arc-item-name';
            if (item.isSelected) nameEl.classList.add('selected');
            nameEl.textContent = item.label;
            textEl.appendChild(nameEl);

            if (item.duration) {
                const durEl = document.createElement('div');
                durEl.className = 'cd-arc-item-duration';
                durEl.textContent = item.duration;
                textEl.appendChild(durEl);
            }
            if (item.sublabel) {
                const subEl = document.createElement('div');
                subEl.className = 'cd-arc-item-sublabel';
                subEl.textContent = item.sublabel;
                textEl.appendChild(subEl);
            }

            el.appendChild(textEl);

            // Badge
            const badge = document.createElement('div');
            badge.className = 'cd-arc-item-badge';
            if (item.badgeImage) {
                const img = document.createElement('img');
                img.className = 'cd-arc-item-badge-img';
                img.src = item.badgeImage;
                badge.appendChild(img);
            } else if (item.badgeIcon) {
                badge.textContent = item.badgeIcon;
            } else if (item.trackNum) {
                badge.textContent = String(item.trackNum);
            }
            el.appendChild(badge);

            container.appendChild(el);
        }
    }

    /** Send click sound when selection changes (exact softarc checkForSelectionClick). */
    function checkForSelectionClick() {
        const centerIndex = Math.round(arcCurrentIndex);
        const currentItem = arcItems[centerIndex];
        if (currentItem && currentItem.id !== lastClickedItemId) {
            lastClickedItemId = currentItem.id;
            if (window.uiStore?.sendClickCommand) {
                window.uiStore.sendClickCommand();
            }
        }
    }

    /**
     * Main animation loop — exact softarc implementation.
     * Runs continuously once started, with 60fps render cap.
     */
    function startAnimation() {
        if (arcAnimFrame) return;
        let lastRenderedIndex = -999; // Force first render
        let lastRenderTime = 0;
        const MIN_RENDER_INTERVAL = 16; // 60fps cap (matches softarc)

        function tick() {
            // Stop animation when not on CD menu page
            const route = window.uiStore?.currentRoute;
            if (route !== 'menu/cd') {
                arcAnimFrame = null;
                return;
            }

            const diff = arcTargetIndex - arcCurrentIndex;
            if (Math.abs(diff) < 0.01) {
                arcCurrentIndex = arcTargetIndex;
            } else {
                arcCurrentIndex += diff * SCROLL_SPEED;
            }

            // Check selection click on every tick (matches softarc)
            checkForSelectionClick();

            // Only render if position changed and enough time elapsed (matches softarc)
            const positionChanged = Math.abs(arcCurrentIndex - lastRenderedIndex) > 0.001;
            const now = Date.now();
            const enoughTimeElapsed = (now - lastRenderTime) >= MIN_RENDER_INTERVAL;

            if (positionChanged && enoughTimeElapsed) {
                renderArc();
                lastRenderedIndex = arcCurrentIndex;
                lastRenderTime = now;
            }

            arcAnimFrame = requestAnimationFrame(tick);
        }
        arcAnimFrame = requestAnimationFrame(tick);
    }

    /**
     * Scroll with speed-based step — exact softarc logic.
     */
    function scrollArc(direction, speed) {
        const speedMultiplier = Math.min(speed / 10, 5);
        const scrollStep = SCROLL_STEP * speedMultiplier;

        if (direction === 'clock') {
            arcTargetIndex = Math.min(arcItems.length - 1, arcTargetIndex + scrollStep);
        } else {
            arcTargetIndex = Math.max(0, arcTargetIndex - scrollStep);
        }

        lastScrollTime = Date.now();

        // Reset snap timer
        if (arcSnapTimer) clearTimeout(arcSnapTimer);
        arcSnapTimer = setTimeout(() => {
            if (Date.now() - lastScrollTime >= SNAP_DELAY) {
                const closest = Math.round(arcTargetIndex);
                arcTargetIndex = Math.max(0, Math.min(arcItems.length - 1, closest));
            }
        }, SNAP_DELAY);

        // Ensure animation is running
        startAnimation();
    }

    /**
     * Snap to nearest item immediately (on button press).
     */
    function snapToNearest() {
        const nearest = Math.round(arcCurrentIndex);
        arcCurrentIndex = Math.max(0, Math.min(arcItems.length - 1, nearest));
        arcTargetIndex = arcCurrentIndex;
        if (arcSnapTimer) {
            clearTimeout(arcSnapTimer);
            arcSnapTimer = null;
        }
    }

    // ── Metadata ──

    function updateMetadata(data) {
        const prevArtwork = metadata?.artwork;
        const prevTrack = metadata?.current_track;
        metadata = data;

        // Always update the PLAYING page back face (track list for flip)
        updatePlayingBackFace(data);

        if (!menuActive) return;

        const loadingEl = document.getElementById('cd-loading');
        const isFirstReveal = !morphing
            && loadingEl && !loadingEl.classList.contains('cd-hidden');

        if (isFirstReveal) {
            morphing = true;
            buildArcItems();
            scrollToPlayingTrack();
            if (data.artwork) {
                const img = new Image();
                img.onload = () => revealArcBrowser(loadingEl);
                img.onerror = () => revealArcBrowser(loadingEl);
                img.src = data.artwork;
            } else {
                revealArcBrowser(loadingEl);
            }
        } else if (morphing) {
            pendingMorphData = data;  // apply after morph completes
        } else {
            if (viewMode !== 'main') {
                // In a submenu — rebuild submenu items (updates toggle labels) but keep position
                const prevTarget = arcTargetIndex;
                const prevCurrent = arcCurrentIndex;
                buildArcItems();
                arcTargetIndex = Math.min(prevTarget, arcItems.length - 1);
                arcCurrentIndex = Math.min(prevCurrent, arcItems.length - 1);
                renderArc();
            } else {
                const prevTarget = arcTargetIndex;
                const prevCurrent = arcCurrentIndex;
                buildArcItems();
                if (data.artwork !== prevArtwork) {
                    // New disc — scroll to playing track
                    scrollToPlayingTrack();
                } else if (data.current_track !== prevTrack) {
                    // Track changed — follow it
                    scrollToPlayingTrack();
                } else {
                    // Same disc, same track — preserve scroll position
                    arcTargetIndex = Math.min(prevTarget, arcItems.length - 1);
                    arcCurrentIndex = Math.min(prevCurrent, arcItems.length - 1);
                }
                renderArc();
            }
        }
    }

    function revealArcBrowser(loadingEl) {
        const arcContainer = document.getElementById('cd-arc-container');
        if (arcContainer) {
            arcContainer.classList.remove('cd-hidden');
            arcContainer.style.opacity = '0';
        }

        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                if (loadingEl) {
                    loadingEl.style.transition = 'opacity 0.8s ease-out';
                    loadingEl.style.opacity = '0';
                }
                if (arcContainer) {
                    arcContainer.style.transition = 'opacity 0.8s ease-in';
                    arcContainer.style.opacity = '1';
                }
            });
        });

        setTimeout(() => {
            if (!morphing) return;
            if (loadingEl) {
                loadingEl.classList.add('cd-hidden');
                loadingEl.style.transition = '';
                loadingEl.style.opacity = '';
            }
            if (arcContainer) {
                arcContainer.style.transition = '';
                arcContainer.style.opacity = '';
            }
            morphing = false;
            if (pendingMorphData) {
                const queued = pendingMorphData;
                pendingMorphData = null;
                updateMetadata(queued);
            }
            renderArc();
            startAnimation();
        }, 1200);
    }

    // ── Playing View Back Face (track listing for flip) ──

    /**
     * Populate the PLAYING page back face with a track listing.
     * Called on every cd_update — ensures the flip always has content,
     * even when MusicBrainz doesn't have a back cover image.
     * If back_artwork IS present, DEFAULT_PLAYING_PRESET handles it
     * and we skip (image back face takes priority).
     */
    function updatePlayingBackFace(data) {
        if (data.back_artwork) return;  // image back takes priority
        const container = document.getElementById('now-playing');
        if (!container) return;
        const backFace = container.querySelector('.playing-back');
        if (!backFace) return;

        const tracks = data.tracks || [];
        if (!tracks.length) return;

        const current = data.current_track || 0;
        const lines = tracks.map((t, i) => {
            const num = t.num || (i + 1);
            const playing = num === current;
            const title = t.title || `Track ${num}`;
            const dur = t.duration || '';
            return `<div class="cd-back-track${playing ? ' cd-back-track-playing' : ''}">`
                + `<span class="cd-back-num">${num}.</span>`
                + `<span class="cd-back-title">${title}</span>`
                + (dur ? `<span class="cd-back-dur">${dur}</span>` : '')
                + `</div>`;
        });

        const albumTitle = data.title || '';
        const artist = data.artist || '';
        const header = artist ? `${albumTitle} — ${artist}` : albumTitle;

        backFace.innerHTML = `<div class="cd-back-tracklist">`
            + (header ? `<div class="cd-back-header">${header}</div>` : '')
            + lines.join('')
            + `</div>`;
        backFace.style.display = '';
    }

    // ── Playing View Flip (Progressive — follows nav wheel) ──

    /** Set flipper to its rest state (CSS class-based, no inline transform). */
    function setFlipRestState(flipper) {
        flipper.classList.remove('playing-flipper-snap');
        flipper.style.transform = '';
        if (flipIsFlipped) {
            flipper.classList.add('flipped');
        } else {
            flipper.classList.remove('flipped');
        }
    }

    // ── Event Handlers ──

    function handleNavEvent(data) {
        const route = window.uiStore?.currentRoute;

        // On PLAYING the wheel belongs to the backdrop carousel, which
        // offers the track list as one of its choices and calls
        // setFlipped() when you land on it. Returning false lets the event
        // through. The progressive, speed-tracking flip gesture goes with
        // it: the carousel steps, so the card does too.
        if (route === 'menu/playing') {
            return false;
        }

        // On CD menu: scroll the arc browser
        if (menuActive && arcItems.length) {
            scrollArc(data.direction, data.speed || 10);
            return true;
        }
        return false;
    }

    function handleButton(button) {
        const route = window.uiStore?.currentRoute;

        // On PLAYING page: media control
        if (route === 'menu/playing') {
            if (button === 'left') { sendCommand('prev'); return true; }
            if (button === 'right') { sendCommand('next'); return true; }
            if (button === 'go') { sendCommand('toggle'); return true; }
            return false;
        }

        // On CD menu: arc browser actions
        if (!menuActive || !arcItems.length) return false;

        if (button === 'go') {
            snapToNearest();
            const item = arcItems[arcTargetIndex];
            if (!item) return true;
            switch (item.type) {
                case 'track':
                    sendCommand('play_track', { track: item.trackNum });
                    break;
                case 'submenu':
                    enterSubmenu(item.id === 'settings' ? 'settings' : 'artwork');
                    break;
                case 'back':
                    exitSubmenu();
                    break;
                case 'toggle':
                    sendCommand(item.command);
                    break;
                case 'release':
                    sendCommand('use_release', { release_id: item.releaseId });
                    break;
                case 'action':
                    if (item.id === 'eject') sendCommand('eject');
                    if (item.id === 'import') sendCommand('import');
                    break;
            }
            return true;
        }

        if (button === 'left') {
            if (viewMode !== 'main') { exitSubmenu(); return true; }
            sendCommand('prev'); return true;
        }
        if (button === 'right') { sendCommand('next'); return true; }

        return false;
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
            } else {
                console.error(`[CD] ${command} failed: HTTP ${resp.status}`);
            }
        } catch {
            console.warn(`[CD] ${command} failed (service not running?)`);
        }
    }

    /** The flip card, if this track has a back worth showing. */
    function flipParts() {
        const container = document.getElementById('now-playing');
        const flipper = container?.querySelector('.playing-flipper');
        const back = flipper?.querySelector('.playing-back');
        if (!flipper || !back || back.style.display === 'none') return null;
        return { flipper, back };
    }

    /**
     * Show the track list, or the cover. Called by js/backdrop.js when the
     * carousel arrives at or leaves the TRACKS entry.
     *
     * Reuses the snap animation the wheel gesture used to end on, so the
     * card turns the same way it always did. No click is sent: that was
     * feedback for a wheel gesture, and the carousel announces itself.
     */
    function setFlipped(wantBack) {
        const parts = flipParts();
        if (!parts) return false;
        if (flipIsFlipped === !!wantBack) return true;
        if (flipSnapTimer) { clearTimeout(flipSnapTimer); flipSnapTimer = null; }
        parts.flipper.classList.add('playing-flipper-snap');
        parts.flipper.style.transform = `rotateY(${wantBack ? 180 : 0}deg)`;
        setTimeout(() => {
            flipIsFlipped = !!wantBack;
            flipProgress = 0;
            flipDirection = null;
            setFlipRestState(parts.flipper);
        }, 200);
        return true;
    }

    // ── Public API ──
    return {
        init,
        destroy,
        handleNavEvent,
        setFlipped,
        get canFlip() { return !!flipParts(); },
        handleButton,
        updateMetadata,
        sendCommand,
        get isActive() { return menuActive || (window.uiStore?.activeSource === 'cd'); },
        get metadata() { return metadata; }
    };
})();

// ── CD Source Preset ──
window.SourcePresets = window.SourcePresets || {};
window.SourcePresets.cd = {
    controller: window.CDView,
    item: { title: 'CD', path: 'menu/cd' },
    after: 'menu/playing',
    view: {
        title: 'CD',
        content: `
            <div id="cd-view" class="media-view">
                <div id="cd-loading" class="cd-loading-state">
                    <div class="cd-disc"></div>
                    <div class="cd-loading-text">Reading disc<span class="cd-dots"><span>.</span><span>.</span><span>.</span></span></div>
                </div>
                <div id="cd-arc-container" class="cd-arc-container cd-hidden"></div>
            </div>`
    },

    onAdd() {},

    onMount() {
        if (window.CDView) window.CDView.init();
    },

    onRemove() {
        if (window.CDView) window.CDView.destroy();
    },

    // No playing sub-preset needed — DEFAULT_PLAYING_PRESET handles media_update
    // from the source via the unified router media path.
};
