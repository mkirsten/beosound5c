/**
 * Demo View Controller
 *
 * Simple source that displays a list of TTS sounds.
 * Nav wheel scrolls the list, GO plays the selected sound.
 */
window.DemoView = (() => {
    const DEMO_SERVICE_URL = window.AppConfig?.demoServiceUrl || 'http://localhost:8771';

    let initialized = false;
    let selected = 0;
    let sounds = [];
    let state = 'available';

    function init() {
        if (!document.getElementById('demo-view')) return;
        initialized = true;
        selected = 0;
        renderList();
        console.log('[DEMO] View initialized');
    }

    function destroy() {
        initialized = false;
    }

    function updateMetadata(data) {
        sounds = data.sounds || [];
        selected = data.selected ?? 0;
        state = data.state || 'available';
        if (initialized) renderList();
    }

    function renderList() {
        const list = document.getElementById('demo-sound-list');
        if (!list) return;

        list.innerHTML = sounds.map((s, i) => {
            const sel = i === selected ? ' demo-item-selected' : '';
            const playing = (state === 'playing' && i === selected) ? ' demo-item-playing' : '';
            return `<div class="demo-item${sel}${playing}" data-index="${i}">
                <span class="demo-item-title">${s.title}</span>
                <span class="demo-item-sub">${s.subtitle}</span>
            </div>`;
        }).join('');

        // Update state indicator
        const indicator = document.getElementById('demo-state');
        if (indicator) {
            indicator.textContent = state === 'playing' ? 'Playing...' : 'Select a sound';
        }
    }

    function handleNavEvent(data) {
        if (!initialized || !sounds.length) return false;
        if (data.direction === 'clock') {
            selected = Math.min(selected + 1, sounds.length - 1);
        } else {
            selected = Math.max(selected - 1, 0);
        }
        sendCommand('select', { index: selected });
        return true;
    }

    function handleButton(button) {
        if (!initialized) return false;
        if (button === 'go') {
            sendCommand('play');
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

    async function sendCommand(command, params = {}) {
        try {
            await fetch(`${DEMO_SERVICE_URL}/command`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command, ...params })
            });
        } catch {
            console.warn(`[DEMO] ${command} failed`);
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

// ── Demo Source Preset ──
window.SourcePresets = window.SourcePresets || {};
window.SourcePresets.demo = {
    controller: window.DemoView,
    item: { title: 'DEMO', path: 'menu/demo' },
    after: 'menu/playing',
    view: {
        title: 'DEMO',
        content: `
            <div id="demo-view" class="media-view" style="display:flex;flex-direction:column;align-items:center;justify-content:center;color:white;height:100%;">
                <div style="width:70%;max-width:500px;">
                    <div style="font-size:28px;font-weight:bold;margin-bottom:8px;text-align:center;">Demo Source</div>
                    <div id="demo-state" style="font-size:16px;opacity:0.6;margin-bottom:24px;text-align:center;">Select a sound</div>
                    <div id="demo-sound-list" style="display:flex;flex-direction:column;gap:4px;"></div>
                </div>
                <style>
                    .demo-item {
                        display: flex; justify-content: space-between; align-items: center;
                        padding: 14px 18px; border-radius: 8px;
                        background: rgba(255,255,255,0.05); cursor: pointer;
                        transition: background 0.15s;
                    }
                    .demo-item-selected {
                        background: rgba(102,153,255,0.25);
                        box-shadow: inset 0 0 0 1px rgba(102,153,255,0.5);
                    }
                    .demo-item-playing .demo-item-title::before {
                        content: '\\25B6\\00a0'; /* ▶ */
                    }
                    .demo-item-title { font-size: 20px; font-weight: 500; }
                    .demo-item-sub { font-size: 14px; opacity: 0.5; }
                </style>
            </div>`
    },

    onAdd() {},

    onMount() {
        if (window.DemoView) window.DemoView.init();
    },

    onRemove() {
        if (window.DemoView) window.DemoView.destroy();
    },

    playing: {
        eventType: 'demo_update',

        artworkSlot: `
            <div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;">
                <div style="font-size:80px;opacity:0.3;">&#9835;</div>
            </div>
        `,

        onUpdate(container, data) {
            const sound = data.sounds?.[data.selected];
            const titleEl = container.querySelector('.media-view-title');
            const artistEl = container.querySelector('.media-view-artist');
            const albumEl = container.querySelector('.media-view-album');
            if (titleEl) titleEl.textContent = sound?.title || 'Demo';
            if (artistEl) artistEl.textContent = sound?.subtitle || '';
            if (albumEl) albumEl.textContent = data.state === 'playing' ? 'Playing' : 'Ready';
        },

        onMount(container) {},
        onRemove(container) {}
    }
};
