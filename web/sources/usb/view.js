/**
 * USB View Controller
 *
 * Two-mode view for USB file browsing and playback.
 * Browse mode: folder/file list with nav wheel scrolling.
 * Playing mode: track list with current track highlighted.
 */
window.USBView = (() => {
    const USB_SERVICE_URL = window.AppConfig?.usbServiceUrl || 'http://localhost:8773';

    // Hard drive SVG placeholder (renders consistently on RPi Chromium, no emoji fonts needed)
    const USB_PLACEHOLDER = `<svg class="usb-icon-placeholder" viewBox="0 0 120 120" fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect x="15" y="20" width="90" height="80" rx="8" stroke="currentColor" stroke-width="2.5"/>
        <circle cx="60" cy="55" r="22" stroke="currentColor" stroke-width="2"/>
        <circle cx="60" cy="55" r="4" fill="currentColor"/>
        <rect x="30" y="85" width="12" height="4" rx="2" fill="currentColor" opacity="0.5"/>
        <rect x="48" y="85" width="12" height="4" rx="2" fill="currentColor" opacity="0.5"/>
    </svg>`;

    let initialized = false;
    let mode = 'browse'; // browse | playing
    let browseData = null;
    let playingData = null;
    let selected = 0;
    let navStack = []; // {path, selected} for back navigation

    // ── Lifecycle ──

    function init() {
        if (!document.getElementById('usb-view')) return;
        initialized = true;
        selected = 0;
        navStack = [];
        fetchBrowse('');
        console.log('[USB] View initialized');
    }

    function destroy() {
        initialized = false;
        browseData = null;
        playingData = null;
        selected = 0;
        navStack = [];
    }

    // ── Data Fetching ──

    async function fetchBrowse(path) {
        try {
            const resp = await fetch(`${USB_SERVICE_URL}/browse?path=${encodeURIComponent(path)}`);
            if (!resp.ok) return;
            browseData = await resp.json();
            selected = 0;
            mode = 'browse';
            if (initialized) renderBrowse();
        } catch {
            console.warn('[USB] Browse fetch failed');
        }
    }

    // ── Metadata from service (usb_update events) ──

    function updateMetadata(data) {
        playingData = data;
        if (data.state === 'playing' || data.state === 'paused') {
            mode = 'playing';
            selected = data.current_track || 0;
            if (initialized) renderPlaying();
        } else if (mode === 'playing') {
            // Stopped — switch back to browse
            mode = 'browse';
            if (initialized) {
                if (browseData) renderBrowse();
                else fetchBrowse('');
            }
        }
    }

    // ── Rendering: Browse Mode ──

    function renderBrowse() {
        const view = document.getElementById('usb-view');
        if (!view) return;

        const data = browseData;
        if (!data) return;

        // Artwork
        const artworkEl = view.querySelector('.usb-artwork');
        if (artworkEl) {
            if (data.artwork) {
                artworkEl.innerHTML = `<img src="${USB_SERVICE_URL}/artwork?path=${encodeURIComponent(data.path)}" alt="">`;
            } else {
                artworkEl.innerHTML = USB_PLACEHOLDER;
            }
        }

        // Breadcrumb
        const breadcrumb = view.querySelector('.usb-breadcrumb');
        if (breadcrumb) {
            breadcrumb.textContent = data.path ? data.path.replace(/\//g, ' / ') : 'USB';
        }

        // List
        const list = view.querySelector('.usb-list');
        if (!list) return;

        if (!data.items || data.items.length === 0) {
            list.innerHTML = '<div class="usb-empty">No files found</div>';
            return;
        }

        list.innerHTML = data.items.map((item, i) => {
            const sel = i === selected ? ' usb-item-selected' : '';
            const icon = item.type === 'folder' ? '<span class="usb-folder-icon"></span>' : '';
            return `<div class="usb-item${sel}" data-index="${i}" data-type="${item.type}" data-path="${item.path}">
                <span class="usb-item-name">${icon}${item.name}</span>
            </div>`;
        }).join('');

        scrollSelectedIntoView(list);
    }

    // ── Rendering: Playing Mode ──

    function renderPlaying() {
        const view = document.getElementById('usb-view');
        if (!view) return;

        const data = playingData;
        if (!data) return;

        // Artwork
        const artworkEl = view.querySelector('.usb-artwork');
        if (artworkEl) {
            if (data.artwork && data.artwork_url) {
                artworkEl.innerHTML = `<img src="${data.artwork_url}" alt="">`;
            } else {
                artworkEl.innerHTML = USB_PLACEHOLDER;
            }
        }

        // Breadcrumb → folder name + state
        const breadcrumb = view.querySelector('.usb-breadcrumb');
        if (breadcrumb) {
            const stateText = data.state === 'paused' ? ' (paused)' : '';
            breadcrumb.textContent = (data.folder_name || 'USB') + stateText;
        }

        // Track list
        const list = view.querySelector('.usb-list');
        if (!list) return;

        const tracks = data.tracks || [];
        list.innerHTML = tracks.map((t, i) => {
            const playing = i === data.current_track ? ' usb-item-playing' : '';
            const sel = i === selected ? ' usb-item-selected' : '';
            return `<div class="usb-item${sel}${playing}" data-index="${i}">
                <span class="usb-item-num">${i + 1}</span>
                <span class="usb-item-name">${t.name}</span>
            </div>`;
        }).join('');

        scrollSelectedIntoView(list);
    }

    function scrollSelectedIntoView(list) {
        const selEl = list?.querySelector('.usb-item-selected');
        if (selEl) selEl.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }

    // ── Nav Wheel ──

    function handleNavEvent(data) {
        if (!initialized) return false;

        const items = mode === 'browse'
            ? (browseData?.items || [])
            : (playingData?.tracks || []);

        if (!items.length) return true;

        if (data.direction === 'clock') {
            selected = Math.min(selected + 1, items.length - 1);
        } else {
            selected = Math.max(selected - 1, 0);
        }

        if (mode === 'browse') renderBrowse();
        else renderPlaying();

        return true;
    }

    // ── Buttons ──

    function handleButton(button) {
        if (!initialized) return false;

        if (mode === 'browse') return handleBrowseButton(button);
        return handlePlayingButton(button);
    }

    function handleBrowseButton(button) {
        const items = browseData?.items || [];
        const item = items[selected];

        if (button === 'go' || button === 'left') {
            if (!item) return true;
            if (item.type === 'folder') {
                // Push current state for back navigation
                navStack.push({ path: browseData.path, selected });
                fetchBrowse(item.path);
            } else {
                // File — play it
                sendCommand('play_file', { path: item.path, index: item.index });
            }
            return true;
        }
        if (button === 'right') {
            // Go up
            if (navStack.length > 0) {
                const prev = navStack.pop();
                fetchBrowse(prev.path).then(() => {
                    selected = prev.selected;
                    if (initialized) renderBrowse();
                });
            } else if (browseData?.parent != null) {
                fetchBrowse(browseData.parent);
            }
            return true;
        }
        return false;
    }

    function handlePlayingButton(button) {
        if (button === 'go') {
            // Play selected track
            const tracks = playingData?.tracks || [];
            if (tracks[selected]) {
                sendCommand('play_file', {
                    path: playingData.folder_path + '/' + tracks[selected].name,
                    index: selected
                });
            }
            return true;
        }
        if (button === 'left') {
            sendCommand('prev');
            return true;
        }
        if (button === 'right') {
            sendCommand('next');
            return true;
        }
        return false;
    }

    // ── Commands ──

    async function sendCommand(command, params = {}) {
        try {
            await fetch(`${USB_SERVICE_URL}/command`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command, ...params })
            });
        } catch {
            console.warn(`[USB] ${command} failed`);
        }
    }

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

// ── USB Source Preset ──
window.SourcePresets = window.SourcePresets || {};
window.SourcePresets.usb = {
    controller: window.USBView,
    item: { title: 'USB', path: 'menu/usb' },
    after: 'menu/music',
    view: {
        title: 'USB',
        content: `
            <div id="usb-view" class="media-view" style="display:flex;height:100%;color:white;">
                <div class="usb-artwork" style="flex:0 0 40%;display:flex;align-items:center;justify-content:center;padding:20px;">
                    <svg class="usb-icon-placeholder" viewBox="0 0 120 120" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <rect x="15" y="20" width="90" height="80" rx="8" stroke="currentColor" stroke-width="2.5"/>
                        <circle cx="60" cy="55" r="22" stroke="currentColor" stroke-width="2"/>
                        <circle cx="60" cy="55" r="4" fill="currentColor"/>
                        <rect x="30" y="85" width="12" height="4" rx="2" fill="currentColor" opacity="0.5"/>
                        <rect x="48" y="85" width="12" height="4" rx="2" fill="currentColor" opacity="0.5"/>
                    </svg>
                </div>
                <div style="flex:1;display:flex;flex-direction:column;padding:16px 20px 16px 0;overflow:hidden;">
                    <div class="usb-breadcrumb" style="font-size:14px;opacity:0.5;margin-bottom:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">USB</div>
                    <div class="usb-list" style="flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:2px;"></div>
                </div>
                <style>
                    .usb-artwork img {
                        max-width: 100%; max-height: 100%; object-fit: contain;
                        border-radius: 4px;
                    }
                    .usb-icon-placeholder {
                        width: 140px; height: 140px; color: white; opacity: 0.15;
                    }
                    .usb-item {
                        display: flex; align-items: center; gap: 10px;
                        padding: 10px 14px; border-radius: 6px;
                        background: rgba(255,255,255,0.03); cursor: pointer;
                        transition: background 0.12s;
                        flex-shrink: 0;
                    }
                    .usb-item-selected {
                        background: rgba(102,153,255,0.25);
                        box-shadow: inset 0 0 0 1px rgba(102,153,255,0.5);
                    }
                    .usb-item-playing .usb-item-name::before {
                        content: '\\25B6\\00a0';
                    }
                    .usb-folder-icon {
                        display: inline-block; width: 16px; height: 13px; margin-right: 6px;
                        background: rgba(255,255,255,0.35); border-radius: 1px 3px 3px 3px;
                        position: relative; vertical-align: -1px;
                    }
                    .usb-folder-icon::before {
                        content: ''; position: absolute; top: -4px; left: 0;
                        width: 7px; height: 4px; background: rgba(255,255,255,0.35);
                        border-radius: 1px 2px 0 0;
                    }
                    .usb-item-name { font-size: 17px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
                    .usb-item-num { font-size: 14px; opacity: 0.4; min-width: 24px; text-align: right; }
                    .usb-empty { opacity: 0.4; text-align: center; padding: 40px; font-size: 18px; }
                </style>
            </div>`
    },

    onAdd() {},

    onMount() {
        if (window.USBView) window.USBView.init();
    },

    onRemove() {
        if (window.USBView) window.USBView.destroy();
    },

    playing: {
        eventType: 'usb_update',

        artworkSlot: `
            <div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;">
                <svg viewBox="0 0 120 120" fill="none" xmlns="http://www.w3.org/2000/svg" style="width:80px;height:80px;color:white;opacity:0.3;">
                    <rect x="15" y="20" width="90" height="80" rx="8" stroke="currentColor" stroke-width="2.5"/>
                    <circle cx="60" cy="55" r="22" stroke="currentColor" stroke-width="2"/>
                    <circle cx="60" cy="55" r="4" fill="currentColor"/>
                    <rect x="30" y="85" width="12" height="4" rx="2" fill="currentColor" opacity="0.5"/>
                    <rect x="48" y="85" width="12" height="4" rx="2" fill="currentColor" opacity="0.5"/>
                </svg>
            </div>
        `,

        onUpdate(container, data) {
            const titleEl = container.querySelector('.media-view-title');
            const artistEl = container.querySelector('.media-view-artist');
            const albumEl = container.querySelector('.media-view-album');
            if (titleEl) titleEl.textContent = data.track_name || 'Unknown';
            if (artistEl) artistEl.textContent = data.folder_name || '';
            if (albumEl) albumEl.textContent = `Track ${(data.current_track || 0) + 1} of ${data.total_tracks || '?'}`;
            // Artwork
            const front = container.querySelector('.playing-artwork');
            if (front && data.artwork && data.artwork_url) front.src = data.artwork_url;
        },

        onMount(container) {},
        onRemove(container) {}
    }
};
