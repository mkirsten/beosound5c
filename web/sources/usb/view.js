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
 * Uses the preloaded iframe pattern: the iframe is created once at startup
 * and moved between the preload container and the view container on navigation.
 * This preserves ArcList state (scroll position, nested navigation) across
 * view switches.
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
        content: '<div id="usb-container" style="width:100%;height:100%;"></div>',
        preloadId: 'preload-usb',
        iframeSrc: 'softarc/usb.html',
        containerId: 'usb-container'
    },

    onAdd() {},

    onMount() {
        if (window.IframeMessenger) {
            IframeMessenger.registerIframe('menu/usb', 'preload-usb');
        }
        // Revive ArcList if it was previously destroy()ed by rescue logic
        try {
            const iframe = document.getElementById('preload-usb');
            const inst = iframe?.contentWindow?.arcListInstance;
            if (inst?.revive) inst.revive();
        } catch (e) { /* iframe not ready */ }
    },

    onRemove() {
        if (window.IframeMessenger) {
            IframeMessenger.unregisterIframe('menu/usb');
        }
    },

    playing: {
        eventType: 'usb_update',

        onUpdate(container, data) {
            const titleEl = container.querySelector('.media-view-title');
            const artistEl = container.querySelector('.media-view-artist');
            const albumEl = container.querySelector('.media-view-album');

            const trackName = data.track_name || 'Unknown';
            const artist = data.artist || data.folder_name || '';
            const albumText = data.album
                ? (data.year ? `${data.album} (${data.year})` : data.album)
                : `Track ${(data.current_track || 0) + 1} of ${data.total_tracks || '?'}`;

            if (window.crossfadeText) {
                window.crossfadeText(titleEl, trackName);
                window.crossfadeText(artistEl, artist);
                window.crossfadeText(albumEl, albumText);
            } else {
                if (titleEl) titleEl.textContent = trackName;
                if (artistEl) artistEl.textContent = artist;
                if (albumEl) albumEl.textContent = albumText;
            }

            // Artwork
            const front = container.querySelector('.playing-artwork');
            if (front) {
                if (data.artwork && data.artwork_url) {
                    if (window.ArtworkManager) {
                        window.ArtworkManager.displayArtwork(front, data.artwork_url);
                    } else {
                        front.src = data.artwork_url;
                    }
                } else if (window.ArtworkManager) {
                    window.ArtworkManager.displayArtwork(front, null, 'noArtwork');
                }
            }
        },

        onMount(container) {
            // Fetch current now-playing state so the view is populated immediately
            const url = (window.AppConfig?.usbServiceUrl || 'http://localhost:8773') + '/now_playing';
            fetch(url).then(r => r.json()).then(data => {
                if (data.state === 'playing' || data.state === 'paused') {
                    this.onUpdate(container, data);
                }
            }).catch(() => {});
        },
        onRemove(container) {}
    }
};
