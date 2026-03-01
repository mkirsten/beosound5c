/**
 * JOIN View Controller
 *
 * Discovers Sonos devices playing on the network and lets the user
 * join this speaker to another group. Arc browser pattern matches CD view.
 *
 * Context: menu/join — Arc list of playing Sonos devices. GO joins selected.
 */
window.JoinView = (() => {
    const PLAYER_URL = window.AppConfig?.playerUrl || 'http://localhost:8766';

    // ── State ──
    let menuActive = false;
    let devices = [];
    let defaultPlayer = null;  // from config (fetched once)
    let loading = false;

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

    /** Reset transient state. */
    function resetState() {
        if (arcSnapTimer) clearTimeout(arcSnapTimer);
        if (arcAnimFrame) cancelAnimationFrame(arcAnimFrame);
        menuActive = false;
        loading = false;
        devices = [];
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
        loading = true;
        renderLoading();

        // Fetch default_player from config (once)
        if (defaultPlayer === null) {
            try {
                const paths = ['json/config.json', '../config/default.json'];
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

        // Fetch network devices
        try {
            const resp = await fetch(`${PLAYER_URL}/player/network`);
            if (resp.ok) {
                devices = await resp.json();
            }
        } catch (e) {
            console.warn('[JOIN] Network fetch failed:', e);
        }

        if (!menuActive) return;  // destroyed while fetching
        loading = false;

        if (devices.length === 0) {
            renderEmpty();
        } else {
            buildArcItems();
            renderArc();
            startAnimation();
        }
    }

    function destroy() {
        resetState();
    }

    // ── Arc Browser ──

    function buildArcItems() {
        // Sort: default player first, then playing before paused, then alphabetical
        const sorted = [...devices].sort((a, b) => {
            if (defaultPlayer) {
                if (a.name === defaultPlayer && b.name !== defaultPlayer) return -1;
                if (b.name === defaultPlayer && a.name !== defaultPlayer) return 1;
            }
            if (a.state !== b.state) return a.state === 'playing' ? -1 : 1;
            return a.name.localeCompare(b.name);
        });

        arcItems = sorted.map(d => ({
            id: `join-${d.ip}`,
            label: d.name,
            sublabel: d.artist ? `${d.artist} \u2014 ${d.title}` : d.title,
            type: 'device',
            ip: d.ip,
            state: d.state,
            artworkUrl: d.artwork_url || '',
        }));
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
            el.classList.add(item.state === 'playing' ? 'join-playing' : 'join-paused');
            el.style.transform = `translate(${item.x}px, ${item.y}px) scale(${item.scale})`;

            // Text wrapper
            const textEl = document.createElement('div');
            textEl.className = 'cd-arc-item-text';

            const nameEl = document.createElement('div');
            nameEl.className = 'cd-arc-item-name';
            if (item.isSelected) nameEl.classList.add('selected');
            nameEl.textContent = item.label;
            textEl.appendChild(nameEl);

            if (item.sublabel) {
                const subEl = document.createElement('div');
                subEl.className = 'cd-arc-item-sublabel';
                subEl.textContent = item.sublabel;
                textEl.appendChild(subEl);
            }

            el.appendChild(textEl);

            // Badge (artwork or music note)
            const badge = document.createElement('div');
            badge.className = 'cd-arc-item-badge';
            if (item.artworkUrl) {
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
        msg.innerHTML = 'No speakers playing<br><span style="font-size:13px;opacity:0.5">Start music on another Sonos to join</span>';
        container.appendChild(msg);
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

        if (button === 'go') {
            snapToNearest();
            const item = arcItems[arcTargetIndex];
            if (!item) return true;
            joinDevice(item.ip, item.label);
            return true;
        }

        return false;
    }

    async function joinDevice(ip, name) {
        console.log(`[JOIN] Joining ${name} (${ip})`);
        try {
            const resp = await fetch(`${PLAYER_URL}/player/join`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ip }),
            });
            if (resp.ok) {
                console.log(`[JOIN] Joined ${name}`);
            } else {
                console.error(`[JOIN] Join failed: HTTP ${resp.status}`);
            }
        } catch (e) {
            console.warn(`[JOIN] Join failed:`, e);
        }
    }

    // ── Metadata (called on join_update broadcasts from router) ──

    function updateMetadata(data) {
        if (!menuActive) return;
        if (Array.isArray(data)) {
            devices = data;
            buildArcItems();
            if (arcItems.length === 0) {
                renderEmpty();
            } else {
                renderArc();
                startAnimation();
            }
        }
    }

    // ── Public API ──
    return {
        init,
        destroy,
        handleNavEvent,
        handleButton,
        updateMetadata,
        get isActive() { return menuActive; },
    };
})();

// ── JOIN Source Preset ──
window.SourcePresets = window.SourcePresets || {};
window.SourcePresets.join = {
    controller: window.JoinView,
    item: { title: 'JOIN', path: 'menu/join' },
    after: 'menu/playing',
    view: {
        title: 'JOIN',
        content: `
            <div id="join-view" class="media-view" style="background: black;">
                <div id="join-arc-container" class="cd-arc-container"></div>
            </div>`
    },

    onMount() {
        if (window.JoinView) window.JoinView.init();
    },

    onRemove() {
        if (window.JoinView) window.JoinView.destroy();
    },
};
