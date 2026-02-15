/**
 * CD View Controller
 *
 * Icons: Random, Repeat, Airplay, Database, Rotate, Eject
 * - Random/Repeat: toggles (shuffle/loop mode)
 * - Airplay: speaker picker sub-panel
 * - Database: alternative MusicBrainz matches sub-panel
 * - Rotate: 3D jewel case flip (front→back→tracks→front), conditional on back cover
 * - Eject: eject disc
 *
 * Three interaction layers:
 *   1. No icon bar: nav wheel shows it, GO=play/pause, LEFT/RIGHT=prev/next
 *   2. Icon bar visible: nav wheel moves highlight, GO=activate, LEFT/RIGHT=prev/next
 *   3. Sub-panel open: nav wheel scrolls list, GO=select, LEFT=dismiss
 */
window.CDView = (() => {
    const HIDE_DELAY = 3000;
    const CD_SERVICE_URL = 'http://localhost:8769';

    // Icon definitions
    const ICON_DEFS = [
        { name: 'random',   type: 'toggle' },
        { name: 'repeat',   type: 'toggle' },
        { name: 'airplay',  type: 'panel'  },
        { name: 'database', type: 'panel'  },
        { name: 'rotate',   type: 'action', conditional: true },
        { name: 'import',   type: 'action', conditional: true },
        { name: 'eject',    type: 'action' }
    ];

    const SVG = {
        random: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/></svg>',
        repeat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>',
        airplay: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 17H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-1"/><polygon points="12 15 17 21 7 21"/></svg>',
        database: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>',
        rotate: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6"/><path d="M2.5 22v-6h6"/><path d="M2 11.5a10 10 0 0 1 18.8-4.3"/><path d="M22 12.5a10 10 0 0 1-18.8 4.2"/></svg>',
        import: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
        eject: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 2 14 22 14"/><rect x="2" y="18" width="20" height="4" rx="1"/></svg>'
    };

    // ── State ──
    const NAV_COOLDOWN_ICONS = 280;  // ms between nav actions on icon bar
    const NAV_COOLDOWN_PANEL = 120;  // ms between nav actions in panels
    let initialized = false;
    let iconBarEl = null;
    let hideTimer = null;
    let selectedIcon = -1;
    let visibleIcons = [];           // filtered ICON_DEFS (respects conditional)
    let lastNavTime = 0;

    let toggles = { random: false, repeat: false };
    let rotatePhase = 0;             // 0=front, 1=back, 2=tracks
    let hasBackCover = false;
    let hasExternalDrive = false;
    let metadata = null;

    let activePanel = null;          // null | 'airplay' | 'database' | 'tracks'
    let panelItems = [];
    let panelSelected = 0;

    // ── Lifecycle ──

    function init() {
        if (!document.getElementById('cd-view')) return;
        rotatePhase = 0;
        activePanel = null;
        rebuildVisibleIcons();
        buildIconBar();
        initialized = true;
        console.log('[CD] View initialized');
    }

    function destroy() {
        if (hideTimer) clearTimeout(hideTimer);
        selectedIcon = -1;
        activePanel = null;
        iconBarEl = null;
        initialized = false;
    }

    function rebuildVisibleIcons() {
        visibleIcons = ICON_DEFS.filter(icon => {
            if (icon.conditional && icon.name === 'rotate') {
                return hasBackCover || (metadata?.tracks?.length > 0);
            }
            if (icon.conditional && icon.name === 'import') {
                return hasExternalDrive;
            }
            return true;
        });
    }

    // ── Icon Bar ──

    function buildIconBar() {
        const existing = document.getElementById('cd-icon-bar');
        if (existing) existing.remove();

        iconBarEl = document.createElement('div');
        iconBarEl.id = 'cd-icon-bar';
        iconBarEl.className = 'cd-icon-bar cd-icon-bar-hidden';

        visibleIcons.forEach((icon, i) => {
            const btn = document.createElement('div');
            btn.className = 'cd-icon-btn';
            if (icon.type === 'toggle' && toggles[icon.name]) {
                btn.classList.add('cd-icon-toggled');
            }
            btn.dataset.icon = icon.name;
            btn.dataset.index = i;
            btn.innerHTML = SVG[icon.name];
            iconBarEl.appendChild(btn);
        });

        const cdView = document.getElementById('cd-view');
        if (cdView) cdView.appendChild(iconBarEl);
    }

    function showIconBar() {
        if (!iconBarEl) return;
        iconBarEl.classList.remove('cd-icon-bar-hidden');
        resetHideTimer();
    }

    function hideIconBar() {
        if (!iconBarEl) return;
        iconBarEl.classList.add('cd-icon-bar-hidden');
        selectedIcon = -1;
        updateHighlight();
    }

    function resetHideTimer() {
        if (hideTimer) clearTimeout(hideTimer);
        hideTimer = setTimeout(() => {
            if (!activePanel) hideIconBar();
        }, HIDE_DELAY);
    }

    function updateHighlight() {
        if (!iconBarEl) return;
        iconBarEl.querySelectorAll('.cd-icon-btn').forEach((btn, i) => {
            btn.classList.toggle('cd-icon-selected', i === selectedIcon);
        });
    }

    function updateToggleVisual(name) {
        if (!iconBarEl) return;
        const btn = iconBarEl.querySelector(`[data-icon="${name}"]`);
        if (btn) btn.classList.toggle('cd-icon-toggled', toggles[name]);
    }

    function flashIcon(name) {
        if (!iconBarEl) return;
        const btn = iconBarEl.querySelector(`[data-icon="${name}"]`);
        if (btn) {
            btn.classList.add('cd-icon-flash');
            setTimeout(() => btn.classList.remove('cd-icon-flash'), 300);
        }
    }

    // ── Sub-Panel ──

    function openPanel(type, items, title) {
        activePanel = type;
        panelItems = items;
        panelSelected = 0;

        const panel = document.getElementById('cd-sub-panel');
        if (!panel) return;

        let html = `<div class="cd-panel-title">${title}</div><div class="cd-panel-items">`;
        items.forEach((item, i) => {
            const sel = i === 0 ? ' cd-panel-item-selected' : '';
            const extra = item.active ? ' cd-panel-item-active' : '';
            html += `<div class="cd-panel-item${sel}${extra}" data-index="${i}">${item.label}</div>`;
        });
        html += '</div>';
        panel.innerHTML = html;
        panel.classList.remove('cd-hidden');
    }

    function closePanel() {
        activePanel = null;
        const panel = document.getElementById('cd-sub-panel');
        if (panel) {
            panel.classList.add('cd-hidden');
            panel.innerHTML = '';
        }
        // If we were in track list via rotate, go back to front
        if (rotatePhase === 2) {
            setRotatePhase(0);
        }
    }

    function scrollPanel(direction) {
        if (!panelItems.length) return;
        if (direction === 'clock') {
            panelSelected = Math.min(panelSelected + 1, panelItems.length - 1);
        } else {
            if (panelSelected <= 0) { closePanel(); return; }
            panelSelected--;
        }
        const panel = document.getElementById('cd-sub-panel');
        if (!panel) return;
        panel.querySelectorAll('.cd-panel-item').forEach((el, i) => {
            el.classList.toggle('cd-panel-item-selected', i === panelSelected);
        });
        // Scroll selected into view
        const selected = panel.querySelector('.cd-panel-item-selected');
        if (selected) selected.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        resetHideTimer();
    }

    function activatePanelItem() {
        const item = panelItems[panelSelected];
        if (!item) return;

        if (activePanel === 'airplay') {
            sendCommand('set_speaker', { sink: item.value });
            closePanel();
        } else if (activePanel === 'database') {
            sendCommand('use_release', { release_id: item.value });
            closePanel();
        } else if (activePanel === 'tracks') {
            sendCommand('play_track', { track: item.value });
            flashIcon('rotate');
        }
    }

    // ── Rotate / 3D Flip ──

    function setRotatePhase(phase) {
        rotatePhase = phase;
        const flipper = document.getElementById('cd-flipper');
        const artworkArea = document.getElementById('cd-artwork-area');
        const trackList = document.getElementById('cd-track-list');

        if (phase === 0) {
            // Front cover
            if (flipper) flipper.classList.remove('cd-flipped');
            if (artworkArea) artworkArea.classList.remove('cd-hidden');
            if (trackList) trackList.classList.add('cd-hidden');
            closePanel();
        } else if (phase === 1) {
            // Back cover (3D flip)
            if (flipper) flipper.classList.add('cd-flipped');
            if (artworkArea) artworkArea.classList.remove('cd-hidden');
            if (trackList) trackList.classList.add('cd-hidden');
        } else if (phase === 2) {
            // Track list
            if (flipper) flipper.classList.remove('cd-flipped');
            if (artworkArea) artworkArea.classList.add('cd-hidden');
            if (trackList) trackList.classList.remove('cd-hidden');
            openTrackListPanel();
        }
    }

    function cycleRotate() {
        if (hasBackCover) {
            // front → back → tracks → front
            const next = (rotatePhase + 1) % 3;
            setRotatePhase(next);
        } else {
            // front → tracks → front (skip back)
            setRotatePhase(rotatePhase === 0 ? 2 : 0);
        }
    }

    function openTrackListPanel() {
        if (!metadata?.tracks?.length) return;
        const items = metadata.tracks.map(t => ({
            label: `${t.num}. ${t.title}${t.duration ? '  ' + t.duration : ''}`,
            value: t.num,
            active: false
        }));
        openPanel('tracks', items, metadata.title || 'Tracks');
    }

    function renderTrackList() {
        const el = document.getElementById('cd-track-list');
        if (!el || !metadata?.tracks) return;

        const titleEl = el.querySelector('.cd-track-list-title');
        if (titleEl) titleEl.textContent = metadata.title || 'Unknown Album';

        const itemsEl = el.querySelector('.cd-track-list-items');
        if (!itemsEl) return;
        itemsEl.innerHTML = metadata.tracks.map(t =>
            `<div class="cd-track-item">`
            + `<span class="cd-track-num">${t.num}</span>`
            + `<span class="cd-track-name">${t.title}</span>`
            + `<span class="cd-track-dur">${t.duration || ''}</span>`
            + `</div>`
        ).join('');
    }

    // ── Icon Activation ──

    function activateIcon(icon) {
        const name = icon.name;
        console.log(`[CD] Activate: ${name} (type: ${icon.type})`);

        if (icon.type === 'toggle') {
            toggles[name] = !toggles[name];
            updateToggleVisual(name);
            flashIcon(name);
            sendCommand(name === 'random' ? 'toggle_shuffle' : 'toggle_repeat');
            return;
        }

        if (name === 'eject') {
            sendCommand('eject');
            flashIcon(name);
            return;
        }

        if (name === 'rotate') {
            cycleRotate();
            flashIcon(name);
            return;
        }

        if (name === 'import') {
            sendCommand('import');
            flashIcon(name);
            return;
        }

        if (name === 'airplay') {
            flashIcon(name);
            fetchSpeakersAndOpen();
            return;
        }

        if (name === 'database') {
            flashIcon(name);
            openDatabasePanel();
            return;
        }
    }

    async function fetchSpeakersAndOpen() {
        try {
            const resp = await fetch(`${CD_SERVICE_URL}/speakers`);
            if (!resp.ok) throw new Error();
            const outputs = await resp.json();
            const items = outputs.map(o => ({
                label: o.label + (o.type === 'airplay' ? '' : ' (local)'),
                value: o.name,
                active: o.active
            }));
            if (items.length === 0) {
                items.push({ label: '(no outputs found)', value: null, active: false });
            }
            openPanel('airplay', items, 'Audio Output');
        } catch {
            openPanel('airplay', [{ label: '(service unavailable)', value: null }], 'Audio Output');
        }
    }

    function openDatabasePanel() {
        const alts = metadata?.alternatives || [];
        if (alts.length === 0) {
            openPanel('database', [{ label: '(no alternatives)', value: null }], 'Releases');
            return;
        }
        const items = alts.map(a => ({
            label: `${a.artist} — ${a.title}${a.year ? ' (' + a.year + ')' : ''}`,
            value: a.release_id
        }));
        openPanel('database', items, 'Alternative Releases');
    }

    // ── Event Handlers ──

    /**
     * Handle nav wheel. Returns true if consumed.
     */
    function handleNavEvent(data) {
        if (!initialized) return false;

        // Debounce — nav wheel fires very fast on real hardware
        const cooldown = activePanel ? NAV_COOLDOWN_PANEL : NAV_COOLDOWN_ICONS;
        const now = Date.now();
        if (now - lastNavTime < cooldown) return true;
        lastNavTime = now;

        // Layer 3: sub-panel open → scroll panel
        if (activePanel) {
            scrollPanel(data.direction);
            return true;
        }

        // Layer 2: icon bar visible → move highlight
        if (iconBarEl && !iconBarEl.classList.contains('cd-icon-bar-hidden')) {
            if (data.direction === 'clock') {
                if (selectedIcon < 0) selectedIcon = 0;
                else selectedIcon = Math.min(selectedIcon + 1, visibleIcons.length - 1);
            } else {
                if (selectedIcon <= 0) {
                    selectedIcon = -1;
                    hideIconBar();
                    return true;
                }
                selectedIcon--;
            }
            updateHighlight();
            resetHideTimer();
            return true;
        }

        // Layer 1: icon bar hidden → show it
        showIconBar();
        selectedIcon = 0;
        updateHighlight();
        return true;
    }

    /**
     * Handle button press. Returns true if consumed.
     */
    function handleButton(button) {
        if (!initialized) return false;

        // Layer 3: sub-panel → GO=select, LEFT=dismiss
        if (activePanel) {
            if (button === 'go') { activatePanelItem(); return true; }
            if (button === 'left') { closePanel(); return true; }
            return true; // consume all buttons while panel is open
        }

        // Layer 2: icon bar visible with selection → GO=activate
        if (selectedIcon >= 0 && iconBarEl && !iconBarEl.classList.contains('cd-icon-bar-hidden')) {
            if (button === 'go') {
                activateIcon(visibleIcons[selectedIcon]);
                return true;
            }
        }

        // Layer 1: standard CD controls
        if (button === 'left') { sendCommand('prev'); return true; }
        if (button === 'right') { sendCommand('next'); return true; }
        if (button === 'go') { sendCommand('toggle'); return true; }

        return false;
    }

    // ── Metadata ──

    function updateMetadata(data) {
        const prevArtwork = metadata?.artwork;
        metadata = data;
        hasBackCover = !!data.back_artwork;
        hasExternalDrive = !!data.has_external_drive;

        // Reset to front cover when artwork changes (new disc or database selection)
        if (data.artwork !== prevArtwork) {
            setRotatePhase(0);
        }

        // Front artwork
        if (data.artwork) {
            const front = document.getElementById('cd-artwork-front');
            if (front) front.src = data.artwork;
        }

        // Back artwork
        const back = document.getElementById('cd-artwork-back');
        if (back) {
            if (data.back_artwork) {
                back.src = data.back_artwork;
                back.style.display = '';
            } else {
                back.style.display = 'none';
            }
        }

        // Text
        const titleEl = document.querySelector('.cd-media-title');
        const artistEl = document.querySelector('.cd-media-artist');
        const albumEl = document.querySelector('.cd-media-album');
        if (titleEl && data.title) titleEl.textContent = data.title;
        if (artistEl && data.artist) artistEl.textContent = data.artist;
        if (albumEl && data.album) albumEl.textContent = data.album;

        // Current track
        if (data.current_track && data.tracks?.length) {
            const track = data.tracks.find(t => t.num === data.current_track);
            if (track && titleEl) titleEl.textContent = track.title;
        }

        // Update toggle states from backend
        if (data.shuffle !== undefined) {
            toggles.random = data.shuffle;
            updateToggleVisual('random');
        }
        if (data.repeat !== undefined) {
            toggles.repeat = data.repeat;
            updateToggleVisual('repeat');
        }

        // Rebuild icon bar (rotate may now be visible/hidden)
        rebuildVisibleIcons();
        if (iconBarEl) buildIconBar();

        // Render track list
        renderTrackList();

        console.log(`[CD] Metadata: ${data.artist} — ${data.title}`);
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
        get isActive() { return initialized; }
    };
})();
