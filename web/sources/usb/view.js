/**
 * USB Source Preset — iframe-based ArcList browser
 *
 * Browse mode uses softarc/usb.html (ArcList V2 with lazy loading).
 * Playing mode shows track info in the standard PLAYING view.
 *
 * The controller serves two roles:
 * 1. On the browse page (menu/usb): proxies nav/button events to the iframe
 *    via IframeMessenger. hardware-input.js calls the controller first for
 *    source pages, so the controller must forward to the iframe itself.
 * 2. On the PLAYING page: handles media controls (prev/next/toggle) by
 *    sending commands directly to the USB service.
 *
 * It distinguishes the two by checking if the iframe is mounted — onMount
 * creates it, onRemove destroys it.
 */

const _usbController = (() => {
    const USB_URL = () => window.AppConfig?.usbServiceUrl || 'http://localhost:8773';
    let _playing = false;

    /** Try sending a message to the USB iframe. Returns true if sent. */
    function sendToIframe(type, data) {
        if (!window.IframeMessenger) return false;
        return IframeMessenger.sendToRoute('menu/usb', type, data);
    }

    return {
        // Always true so the source-page path calls handleNavEvent/handleButton
        // (otherwise events are consumed with no handler)
        get isActive() { return true; },

        updateMetadata(data) {
            _playing = (data.state === 'playing' || data.state === 'paused');
        },

        handleNavEvent(data) {
            // Forward to iframe if mounted (browse page)
            // Returns false if iframe not mounted (PLAYING page) → falls through to menu scroll
            return sendToIframe('nav', { data });
        },

        handleButton(button) {
            // Try iframe first (browse page — iframe handles ArcList navigation)
            if (sendToIframe('button', { button })) return true;
            // Iframe not mounted → PLAYING page media controls
            if (!_playing) return false;
            const cmd = { go: 'toggle', left: 'prev', right: 'next' }[button];
            if (!cmd) return false;
            fetch(`${USB_URL()}/command`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: cmd }),
            }).catch(() => {});
            return true;
        },
    };
})();

// ── USB Source Preset ──
window.SourcePresets = window.SourcePresets || {};
window.SourcePresets.usb = {
    controller: _usbController,
    item: { title: 'USB', path: 'menu/usb' },
    after: 'menu/playing',
    view: {
        title: 'USB',
        content: '<div id="usb-container" style="width:100%;height:100%;"></div>'
    },

    onAdd() {},

    onMount() {
        const container = document.getElementById('usb-container');
        if (!container || container.querySelector('iframe')) return;
        const iframe = document.createElement('iframe');
        iframe.id = 'preload-usb';
        iframe.src = 'softarc/usb.html';
        iframe.style.cssText = 'width:100%;height:100%;border:none;border-radius:8px;box-shadow:0 5px 15px rgba(0,0,0,0.3);';
        container.appendChild(iframe);
        if (window.IframeMessenger) {
            IframeMessenger.registerIframe('menu/usb', 'preload-usb');
        }
    },

    onRemove() {
        if (window.IframeMessenger) {
            IframeMessenger.unregisterIframe('menu/usb');
        }
        const container = document.getElementById('usb-container');
        if (container) container.innerHTML = '';
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
