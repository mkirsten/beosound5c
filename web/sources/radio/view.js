/**
 * Radio Source Preset -- iframe-based ArcList browser
 *
 * Browse mode uses softarc/radio.html (ArcList V2 with lazy loading).
 * Playing mode shows station info in the standard PLAYING view.
 *
 * The controller serves two roles:
 * 1. On the browse page (menu/radio): proxies nav/button events to the iframe
 *    via IframeMessenger.
 * 2. On the PLAYING page: handles media controls (prev/next/toggle) by
 *    sending commands directly to the Radio service.
 */

const _radioController = (() => {
    const RADIO_URL = () => window.AppConfig?.radioServiceUrl || 'http://localhost:8779';
    let _playing = false;

    /** Try sending a message to the Radio iframe. Returns true if sent. */
    function sendToIframe(type, data) {
        if (!window.IframeMessenger) return false;
        return IframeMessenger.sendToRoute('menu/radio', type, data);
    }

    return {
        get isActive() { return true; },

        updateMetadata(data) {
            _playing = (data.state === 'playing' || data.state === 'paused');
        },

        handleNavEvent(data) {
            return sendToIframe('nav', { data });
        },

        handleButton(button) {
            // Try iframe first (browse page)
            if (sendToIframe('button', { button })) return true;
            // Iframe not mounted -> PLAYING page media controls
            if (!_playing) return false;
            const cmd = { go: 'toggle', left: 'prev', right: 'next', up: 'toggle_favourite', down: 'prev' }[button];
            if (!cmd) return false;
            fetch(`${RADIO_URL()}/command`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: cmd }),
            }).catch(() => {});
            return true;
        },
    };
})();

// -- Radio Source Preset --
window.SourcePresets = window.SourcePresets || {};
window.SourcePresets.radio = {
    controller: _radioController,
    item: { title: 'Radio', path: 'menu/radio' },
    after: 'menu/playing',
    view: {
        title: 'Radio',
        content: '<div id="radio-container" style="width:100%;height:100%;"></div>',
        preloadId: 'preload-radio',
        iframeSrc: 'softarc/radio.html',
        containerId: 'radio-container'
    },

    onAdd() {},

    onMount() {
        if (window.IframeMessenger) {
            IframeMessenger.registerIframe('menu/radio', 'preload-radio');
        }
        try {
            const iframe = document.getElementById('preload-radio');
            const inst = iframe?.contentWindow?.arcListInstance;
            if (inst?.revive) inst.revive();
        } catch (e) { /* iframe not ready */ }
    },

    onRemove() {
        if (window.IframeMessenger) {
            IframeMessenger.unregisterIframe('menu/radio');
        }
    },
};
