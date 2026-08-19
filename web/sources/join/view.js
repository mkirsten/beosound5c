/**
 * SPEAKERS View Controller
 *
 * Discovers other rooms on the network (Sonos today; any grouping-capable
 * network player) and lets the user act on them. The room the BS5c is
 * currently driving is pinned at the top (flagged `current`), rendered like
 * the others with artwork/now-playing.
 *
 * Buttons on a highlighted room:
 *   GO    = Group/Join  — link the room with what's playing here
 *   RIGHT = Target      — "Play here": re-point the BS5c to this room
 *   LEFT  = Ungroup (when grouped) / back
 *
 * Route id stays `menu/join` for menu-config back-compat; the label reads
 * SPEAKERS.
 */
window.JoinView = (() => {
    const PLAYER_URL = window.AppConfig?.playerUrl || 'http://localhost:8766';

    // ── State ──
    let menuActive = false;
    let mountGen = 0;   // increments per init(); suspended stale inits bail
    let devices = [];
    let isGrouped = false;
    let configuredIp = '';     // the "home" room (from /player/status)
    let overridden = false;    // a runtime target override is active
    let defaultPlayer = null;  // from config (fetched once)
    let loading = false;
    let pollTimer = null;
    const POLL_INTERVAL = 5000;

    // Arc browser state (same pattern as CD view)
    let arcItems = [];
    let arcTargetIndex = 0;
    let arcCurrentIndex = 0;
    let arcAnimFrame = null;
    let arcSnapTimer = null;
    let lastScrollTime = 0;
    let lastClickedItemId = null;

    // Softarc constants (shared via ArcMath)
    const _ac = ArcMath.getConstants();
    const SCROLL_SPEED = _ac.scrollSpeed;
    const SCROLL_STEP = _ac.scrollStep;
    const SNAP_DELAY = _ac.snapDelay;

    /** Reset transient state.
     *
     * ``devices`` and ``isGrouped`` deliberately persist across
     * destroy→init cycles so the view renders instantly from the
     * previous snapshot on nav-back. The existing background refresh
     * (started below by init's poll timer) patches any changes in
     * place — we don't need to block on a fresh fetch to draw
     * something useful. */
    function resetState() {
        if (arcSnapTimer) clearTimeout(arcSnapTimer);
        if (arcAnimFrame) cancelAnimationFrame(arcAnimFrame);
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
        menuActive = false;
        loading = false;
        arcItems = [];
        arcTargetIndex = 0;
        arcCurrentIndex = 0;
        arcAnimFrame = null;
        arcSnapTimer = null;
        lastScrollTime = 0;
        lastClickedItemId = null;
    }

    // ── Lifecycle ──

    async function init() {
        if (!document.getElementById('join-view')) return;
        resetState();
        menuActive = true;
        // Mount generation: an older init() suspended at an await and
        // resuming after a remount passes the shared menuActive check and
        // would arm a second poll interval that leaks for the life of the
        // parent shell.
        const gen = ++mountGen;

        // Fetch default_player from config (once)
        if (defaultPlayer === null) {
            try {
                const paths = ['/json/config.json', '/config/default.json'];
                for (const path of paths) {
                    try {
                        const resp = await fetch(path);
                        if (resp.ok) {
                            const cfg = await resp.json();
                            defaultPlayer = cfg.join?.default_player || '';
                            break;
                        }
                    } catch { /* try next */ }
                }
            } catch {
                defaultPlayer = '';
            }
        }

        if (gen !== mountGen || !menuActive) return;  // superseded during config fetch

        // Cached-first render: if we have a previous snapshot, draw it
        // immediately so the view isn't blank while the network fetch
        // is in flight. refreshDevices() below patches any changes in
        // place, so the user never sees a loading spinner on nav-back.
        if (devices.length > 0) {
            buildArcItems();
            renderArc();
            startAnimation();
            // Background refresh — fire-and-forget, patches in place.
            refreshDevices();
        } else {
            loading = true;
            renderLoading();
            try {
                const [netResp, statusResp] = await Promise.all([
                    fetch(`${PLAYER_URL}/player/network`),
                    fetch(`${PLAYER_URL}/player/status`),
                ]);
                if (netResp.ok) devices = await netResp.json();
                if (statusResp.ok) {
                    const status = await statusResp.json();
                    isGrouped = !!status.is_grouped;
                    applyVolumeNote(status);
                }
            } catch (e) {
                console.warn('[JOIN] Network fetch failed:', e);
            }
            if (gen !== mountGen || !menuActive) return;  // destroyed/remounted while fetching
            loading = false;

            if (devices.length === 0) {
                renderEmpty();
            } else {
                buildArcItems();
                renderArc();
                startAnimation();
            }
        }

        // Poll for changes while view is open
        if (gen !== mountGen) return;  // a newer init owns the poll now
        if (pollTimer) clearInterval(pollTimer);
        pollTimer = setInterval(refreshDevices, POLL_INTERVAL);
    }

    function destroy() {
        resetState();
    }

    /** Fetch fresh data and update in-place without flicker. */
    async function refreshDevices() {
        if (!menuActive || loading) return;

        let newDevices;
        try {
            const [netResp, statusResp] = await Promise.all([
                fetch(`${PLAYER_URL}/player/network`),
                fetch(`${PLAYER_URL}/player/status`),
            ]);
            if (!netResp.ok) return;
            newDevices = await netResp.json();
            if (statusResp.ok) {
                const status = await statusResp.json();
                isGrouped = !!status.is_grouped;
                applyVolumeNote(status);
            }
        } catch { return; }

        if (!menuActive) return;

        const oldIds = arcItems.map(i => i.id).join(',');
        const oldItems = arcItems;
        devices = newDevices;
        buildArcItems();
        const newIds = arcItems.map(i => i.id).join(',');

        if (arcItems.length === 0) {
            renderEmpty();
            return;
        }

        if (oldIds !== newIds) {
            // Item set changed (speakers grouped/ungrouped) — preserve selection
            const selectedIp = oldItems[Math.round(arcCurrentIndex)]?.ip;
            const newIdx = arcItems.findIndex(i => i.ip === selectedIp);
            if (newIdx >= 0) {
                arcCurrentIndex = newIdx;
                arcTargetIndex = newIdx;
            } else {
                arcCurrentIndex = Math.min(Math.round(arcCurrentIndex), arcItems.length - 1);
                arcTargetIndex = arcCurrentIndex;
            }
            // Force full rebuild on next frame
            const container = document.getElementById('join-arc-container');
            if (container) container.innerHTML = '';
            renderArc();
            startAnimation();
        } else {
            // Same items — patch content in-place (no DOM rebuild)
            patchArcContent();
        }
    }

    /** Update visible DOM elements in-place when item data changes. */
    function patchArcContent() {
        const container = document.getElementById('join-arc-container');
        if (!container) return;

        for (const item of arcItems) {
            const el = container.querySelector(`[data-item-id="${item.id}"]`);
            if (!el) continue;

            // Content class
            el.classList.toggle('join-no-content', !item.hasContent);

            const nameEl = el.querySelector('.cd-arc-item-name');
            if (nameEl) {
                // Update text node without touching child elements (EQ, group icon)
                const textNode = nameEl.firstChild;
                if (textNode && textNode.nodeType === Node.TEXT_NODE) {
                    if (textNode.textContent !== item.label) textNode.textContent = item.label;
                }

                // EQ bars — add/remove based on playing state
                const existingEq = nameEl.querySelector('.join-eq');
                if (item.state === 'playing' && !existingEq) {
                    const eq = document.createElement('span');
                    eq.className = 'join-eq';
                    eq.innerHTML = '<span></span><span></span><span></span>';
                    nameEl.appendChild(eq);
                } else if (item.state !== 'playing' && existingEq) {
                    existingEq.remove();
                }

                // Group icon — add/remove/update
                const existingGi = nameEl.querySelector('.join-group-icon');
                if (item.group?.length > 0) {
                    const text = `+${item.group.length}`;
                    if (existingGi) {
                        if (existingGi.textContent !== text) existingGi.textContent = text;
                    } else {
                        const gi = document.createElement('span');
                        gi.className = 'join-group-icon';
                        gi.textContent = text;
                        nameEl.appendChild(gi);
                    }
                } else if (existingGi) {
                    existingGi.remove();
                }
            }

            // Sublabel
            const textWrapper = el.querySelector('.cd-arc-item-text');
            let subEl = el.querySelector('.cd-arc-item-sublabel');
            if (item.sublabel) {
                if (subEl) {
                    if (subEl.textContent !== item.sublabel) subEl.textContent = item.sublabel;
                } else {
                    subEl = document.createElement('div');
                    subEl.className = 'cd-arc-item-sublabel';
                    subEl.textContent = item.sublabel;
                    textWrapper?.appendChild(subEl);
                }
            } else if (subEl) {
                subEl.remove();
            }

            // Artwork — only swap if URL changed (skip unjoin — has static SVG)
            const badge = el.querySelector('.cd-arc-item-badge');
            if (!badge || item.type === 'unjoin') continue;
            const existingImg = badge.querySelector('.cd-arc-item-badge-img');
            if (item.hasContent && item.artworkUrl) {
                if (existingImg) {
                    if (existingImg.src !== item.artworkUrl) existingImg.src = item.artworkUrl;
                } else {
                    badge.textContent = '';
                    const img = document.createElement('img');
                    img.className = 'cd-arc-item-badge-img';
                    img.src = item.artworkUrl;
                    img.onerror = () => { img.remove(); badge.textContent = '\u266B'; };
                    badge.appendChild(img);
                }
            } else {
                if (existingImg) existingImg.remove();
                if (badge.textContent !== '\u266B') badge.textContent = '\u266B';
            }
        }
    }

    // ── Arc Browser ──

    function buildArcItems() {
        // Sort: default player first, then by tier (playing > has content > empty),
        // then alphabetical within each tier
        function tier(d) {
            if (d.state === 'playing') return 0;
            if (d.title || d.artwork_url) return 1;
            return 2;
        }
        // The active target (the room we're driving) is pinned at the top,
        // separate from the sortable list of other rooms.
        const current = devices.find(d => d.current);
        const others = devices.filter(d => !d.current);
        const sorted = [...others].sort((a, b) => {
            if (defaultPlayer) {
                if (a.name === defaultPlayer && b.name !== defaultPlayer) return -1;
                if (b.name === defaultPlayer && a.name !== defaultPlayer) return 1;
            }
            const ta = tier(a), tb = tier(b);
            if (ta !== tb) return ta - tb;
            return a.name.localeCompare(b.name);
        });

        const toItem = (d, { current = false } = {}) => ({
            id: current ? `cur-${d.ip}` : `join-${d.ip}`,
            label: d.name || 'This room',
            sublabel: d.artist ? `${d.artist} \u2014 ${d.title}` : d.title,
            type: current ? 'current' : 'device',
            current,
            home: !!configuredIp && d.ip === configuredIp,
            ip: d.ip,
            state: d.state,
            hasContent: !!(d.title || d.artwork_url),
            artworkUrl: d.artwork_url || '',
            title: d.title || '',
            artist: d.artist || '',
            album: d.album || '',
            group: d.group || [],
        });

        arcItems = [];

        // Current target first \u2014 the room you're playing on right now.
        if (current) arcItems.push(toItem(current, { current: true }));

        // UNJOIN affordance when this speaker is in a group.
        if (isGrouped) {
            arcItems.push({
                id: 'unjoin',
                label: 'UNJOIN',
                sublabel: '',
                type: 'unjoin',
                current: false,
                ip: '',
                state: '',
                hasContent: true,
                artworkUrl: '',
                title: '', artist: '', album: '',
                group: [],
            });
        }

        arcItems.push(...sorted.map(d => toItem(d)));
    }

    function getVisibleItems() {
        return ArcMath.getVisibleItems(arcCurrentIndex, arcItems);
    }

    function updateExistingElements(container) {
        const existingItems = Array.from(container.querySelectorAll('.cd-arc-item'));
        const visibleItems = getVisibleItems();

        if (existingItems.length !== visibleItems.length) return false;

        for (let i = 0; i < existingItems.length; i++) {
            if (!existingItems[i] || !visibleItems[i] ||
                existingItems[i].dataset.itemId !== visibleItems[i].id) {
                return false;
            }
        }

        existingItems.forEach((element, index) => {
            const item = visibleItems[index];
            if (!item) return;
            element.style.transform = `translate(${item.x}px, ${item.y}px) scale(${item.scale})`;

            const nameEl = element.querySelector('.cd-arc-item-name');
            if (item.isSelected && !element.classList.contains('cd-arc-item-selected')) {
                element.classList.add('cd-arc-item-selected');
                if (nameEl) nameEl.classList.add('selected');
            } else if (!item.isSelected && element.classList.contains('cd-arc-item-selected')) {
                element.classList.remove('cd-arc-item-selected');
                if (nameEl) nameEl.classList.remove('selected');
            }
        });

        return true;
    }

    function renderArc() {
        const container = document.getElementById('join-arc-container');
        if (!container || !arcItems.length) return;

        if (updateExistingElements(container)) return;

        container.innerHTML = '';
        const visibleItems = getVisibleItems();

        for (const item of visibleItems) {
            const el = document.createElement('div');
            el.className = 'cd-arc-item leaf';
            el.dataset.itemId = item.id;
            if (item.isSelected) el.classList.add('cd-arc-item-selected');
            if (!item.hasContent) el.classList.add('join-no-content');
            if (item.current) el.classList.add('join-current');
            el.style.transform = `translate(${item.x}px, ${item.y}px) scale(${item.scale})`;

            // Text wrapper
            const textEl = document.createElement('div');
            textEl.className = 'cd-arc-item-text';

            const nameEl = document.createElement('div');
            nameEl.className = 'cd-arc-item-name';
            if (item.isSelected) nameEl.classList.add('selected');
            nameEl.textContent = item.label;

            // Mark the room we're currently driving, and the configured HOME
            // room (targeting HOME clears the override — the way back).
            if (item.current) {
                const badge = document.createElement('span');
                badge.className = 'join-current-badge';
                badge.textContent = 'HERE';
                nameEl.appendChild(badge);
            } else if (item.home && overridden) {
                const badge = document.createElement('span');
                badge.className = 'join-current-badge';
                badge.textContent = 'HOME';
                nameEl.appendChild(badge);
            }

            // EQ bars for playing
            if (item.state === 'playing') {
                const eq = document.createElement('span');
                eq.className = 'join-eq';
                eq.innerHTML = '<span></span><span></span><span></span>';
                nameEl.appendChild(eq);
            }

            // Group icon
            if (item.group && item.group.length > 0) {
                const gi = document.createElement('span');
                gi.className = 'join-group-icon';
                gi.textContent = `+${item.group.length}`;
                nameEl.appendChild(gi);
            }

            textEl.appendChild(nameEl);

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
            if (item.type === 'unjoin') {
                badge.classList.add('join-unjoin-badge');
                badge.innerHTML = '<svg viewBox="0 0 24 24" width="40" height="40" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M16 8l-8 8M8 8l8 8"/></svg>';
            } else if (item.hasContent && item.artworkUrl) {
                const img = document.createElement('img');
                img.className = 'cd-arc-item-badge-img';
                img.src = item.artworkUrl;
                img.onerror = () => { img.remove(); badge.textContent = '\u266B'; };
                badge.appendChild(img);
            } else {
                badge.textContent = '\u266B';
            }
            el.appendChild(badge);

            container.appendChild(el);
        }
    }

    function renderLoading() {
        const container = document.getElementById('join-arc-container');
        if (!container) return;
        container.innerHTML = '';
        const msg = document.createElement('div');
        msg.className = 'join-empty';
        msg.textContent = 'Searching\u2026';
        container.appendChild(msg);
    }

    function renderEmpty() {
        const container = document.getElementById('join-arc-container');
        if (!container) return;
        container.innerHTML = '';
        const msg = document.createElement('div');
        msg.className = 'join-empty';
        msg.innerHTML = 'No other speakers found<br><span style="font-size:13px;opacity:0.5">Check that other speakers are on the network</span>';
        container.appendChild(msg);
    }

    /** Absorb /player/status: home room, override state, and the notes. */
    function applyVolumeNote(status) {
        if (status) {
            configuredIp = status.configured_ip || '';
            overridden = !!status.overridden;
        }
        const vnote = document.getElementById('speakers-vol-note');
        if (vnote) vnote.style.display = (status && status.volume_follows_target === false) ? '' : 'none';
        const rnote = document.getElementById('speakers-reset-note');
        // Only nudge "reset" when we've actually moved off the home room.
        if (rnote) rnote.style.display = overridden ? '' : 'none';
    }

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

    function startAnimation() {
        if (arcAnimFrame) return;
        let lastRenderedIndex = -999;
        let lastRenderTime = 0;
        const MIN_RENDER_INTERVAL = 16;

        function tick() {
            const route = window.uiStore?.currentRoute;
            if (route !== 'menu/join') {
                arcAnimFrame = null;
                return;
            }

            const diff = arcTargetIndex - arcCurrentIndex;
            if (Math.abs(diff) < 0.01) {
                arcCurrentIndex = arcTargetIndex;
            } else {
                arcCurrentIndex += diff * SCROLL_SPEED;
            }

            checkForSelectionClick();

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

    function scrollArc(direction, speed) {
        const speedMultiplier = Math.min(speed / 10, 5);
        const scrollStep = SCROLL_STEP * speedMultiplier;

        if (direction === 'clock') {
            arcTargetIndex = Math.min(arcItems.length - 1, arcTargetIndex + scrollStep);
        } else {
            arcTargetIndex = Math.max(0, arcTargetIndex - scrollStep);
        }

        lastScrollTime = Date.now();

        if (arcSnapTimer) clearTimeout(arcSnapTimer);
        arcSnapTimer = setTimeout(() => {
            if (Date.now() - lastScrollTime >= SNAP_DELAY) {
                const closest = Math.round(arcTargetIndex);
                arcTargetIndex = Math.max(0, Math.min(arcItems.length - 1, closest));
            }
        }, SNAP_DELAY);

        startAnimation();
    }

    function snapToNearest() {
        const nearest = Math.round(arcCurrentIndex);
        arcCurrentIndex = Math.max(0, Math.min(arcItems.length - 1, nearest));
        arcTargetIndex = arcCurrentIndex;
        if (arcSnapTimer) {
            clearTimeout(arcSnapTimer);
            arcSnapTimer = null;
        }
    }

    // ── Event Handlers ──

    function handleNavEvent(data) {
        if (menuActive && arcItems.length) {
            scrollArc(data.direction, data.speed || 10);
            return true;
        }
        return false;
    }

    function handleButton(button) {
        if (!menuActive || !arcItems.length) return false;

        if (button === 'go' || button === 'right') {
            snapToNearest();
            const item = arcItems[arcTargetIndex];
            if (!item) return true;

            // The current-target row is a status anchor — acting on it is a
            // no-op; just drop into PLAYING.
            if (item.type === 'current') {
                if (window.uiStore?.navigateToView) {
                    window.uiStore.navigateToView('menu/playing');
                }
                return true;
            }

            // Blue highlight on selected item (matches CD pattern)
            const container = document.getElementById('join-arc-container');
            if (container) {
                const el = container.querySelector(`[data-item-id="${item.id}"]`);
                if (el) {
                    el.classList.add('cd-arc-item-playing');
                }
            }

            if (item.type === 'unjoin') {
                unjoinDevice();
            } else if (button === 'right') {
                targetDevice(item);   // control this room (re-point the BS5c)
            } else {
                joinDevice(item);     // GO — group with current playback
            }
            return true;
        }

        // LEFT is always back — ungroup is the discoverable UNJOIN row, not a
        // hidden button binding (keeps LEFT consistent with the rest of the UI).

        return false;
    }

    async function unjoinDevice() {
        console.log('[JOIN] Unjoining');
        try {
            const resp = await fetch(`${PLAYER_URL}/player/unjoin`, { method: 'POST' });
            if (resp.ok) {
                console.log('[JOIN] Unjoined');
                if (window.uiStore?.navigateToView) {
                    window.uiStore.navigateToView('menu/playing');
                }
            } else {
                console.error(`[JOIN] Unjoin failed: HTTP ${resp.status}`);
            }
        } catch (e) {
            console.warn('[JOIN] Unjoin failed:', e);
        }
    }

    async function targetDevice(item) {
        console.log(`[SPEAKERS] Targeting ${item.label} (${item.ip})`);
        try {
            const resp = await fetch(`${PLAYER_URL}/player/target`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ip: item.ip, name: item.label }),
            });
            if (resp.ok) {
                console.log(`[SPEAKERS] Now playing on ${item.label}`);
                // Pre-populate media info so PLAYING renders instantly
                if (window.uiStore) {
                    window.uiStore.mediaInfo = {
                        title: item.title || '—',
                        artist: item.artist || '—',
                        album: item.album || '—',
                        artwork: item.artworkUrl || '',
                        state: item.state === 'playing' ? 'playing' : 'stopped',
                        position: '0:00',
                        duration: '0:00'
                    };
                }
                if (item.artworkUrl && window.ArtworkManager) {
                    window.ArtworkManager.preloadImage(item.artworkUrl);
                }
                if (window.uiStore?.navigateToView) {
                    window.uiStore.navigateToView('menu/playing');
                }
            } else {
                console.error(`[SPEAKERS] Target failed: HTTP ${resp.status}`);
            }
        } catch (e) {
            console.warn('[SPEAKERS] Target failed:', e);
        }
    }

    async function joinDevice(item) {
        console.log(`[JOIN] Joining ${item.label} (${item.ip})`);
        try {
            const resp = await fetch(`${PLAYER_URL}/player/join`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    ip: item.ip,
                    title: item.title,
                    artist: item.artist,
                    album: item.album,
                    artwork_url: item.artworkUrl,
                }),
            });
            if (resp.ok) {
                console.log(`[JOIN] Joined ${item.label}`);
                // Pre-populate media info so PLAYING renders instantly
                if (window.uiStore) {
                    window.uiStore.mediaInfo = {
                        title: item.title || '—',
                        artist: item.artist || '—',
                        album: item.album || '—',
                        artwork: item.artworkUrl || '',
                        state: 'playing',
                        position: '0:00',
                        duration: '0:00'
                    };
                }
                if (item.artworkUrl && window.ArtworkManager) {
                    window.ArtworkManager.preloadImage(item.artworkUrl);
                }
                // Navigate to PLAYING view after successful join
                if (window.uiStore?.navigateToView) {
                    window.uiStore.navigateToView('menu/playing');
                }
            } else {
                console.error(`[JOIN] Join failed: HTTP ${resp.status}`);
            }
        } catch (e) {
            console.warn(`[JOIN] Join failed:`, e);
        }
    }

    // ── Public API ──
    return {
        init,
        destroy,
        handleNavEvent,
        handleButton,
        get isActive() { return menuActive; },
    };
})();

// ── SPEAKERS Source Preset ──
// Route id stays `join`/`menu/join` for menu-config back-compat; label is SPEAKERS.
window.SourcePresets = window.SourcePresets || {};
window.SourcePresets.join = {
    controller: window.JoinView,
    item: { title: 'SPEAKERS', path: 'menu/join' },
    after: 'menu/playing',
    view: {
        title: 'SPEAKERS',
        content: `
            <div id="join-view" class="media-view" style="background: black;">
                <div id="join-arc-container" class="cd-arc-container"></div>
                <div class="speakers-legend">
                    <span><span class="speakers-key">GO</span> group</span>
                    <span><span class="speakers-key">RIGHT</span> control here</span>
                    <span id="speakers-reset-note" class="speakers-legend-note" style="display:none">RIGHT the HOME room to reset</span>
                    <span id="speakers-vol-note" class="speakers-legend-note" style="display:none">control moves the room only — volume stays on this speaker</span>
                </div>
            </div>`
    },

    onMount() {
        if (window.JoinView) window.JoinView.init();
    },

    onRemove() {
        if (window.JoinView) window.JoinView.destroy();
    },
};
