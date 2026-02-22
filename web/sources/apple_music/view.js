/**
 * Apple Music Source Preset — stub
 *
 * Browse mode: will show library/playlist browsing (not yet implemented).
 * Playing mode: shows track info in the standard PLAYING view.
 *
 * STATUS: STUB — shows placeholder UI. Service returns errors on commands.
 */

const _appleMusicController = (() => {
    const SERVICE_URL = () => window.AppConfig?.appleMusicServiceUrl || 'http://localhost:8774';
    let _playing = false;

    function sendCommand(cmd) {
        fetch(`${SERVICE_URL()}/command`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: cmd }),
        }).catch(() => {});
    }

    return {
        get isActive() { return _playing; },

        updateMetadata(data) {
            _playing = (data.state === 'playing' || data.state === 'paused');
        },

        handleNavEvent(data) {
            return false; // No browse UI yet
        },

        handleButton(button) {
            if (!_playing) return false;
            const cmd = { go: 'toggle', left: 'prev', right: 'next' }[button];
            if (!cmd) return false;
            sendCommand(cmd);
            return true;
        },
    };
})();

// ── Apple Music Source Preset ──
window.SourcePresets = window.SourcePresets || {};
window.SourcePresets.apple_music = {
    controller: _appleMusicController,
    item: { title: 'APPLE MUSIC', path: 'menu/apple_music' },
    after: 'menu/playing',
    view: {
        title: 'APPLE MUSIC',
        content: `
            <div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;color:white;opacity:0.5;font-size:18px;">
                Apple Music — not yet implemented
            </div>`
    },

    onAdd() {},
    onMount() {},
    onRemove() {},

    playing: {
        eventType: 'apple_music_update',

        onUpdate(container, data) {
            const titleEl = container.querySelector('.media-view-title');
            const artistEl = container.querySelector('.media-view-artist');
            const albumEl = container.querySelector('.media-view-album');
            if (titleEl) crossfadeText(titleEl, data.title || '—');
            if (artistEl) crossfadeText(artistEl, data.artist || '—');
            if (albumEl) crossfadeText(albumEl, data.album || '—');
            const img = container.querySelector('.playing-artwork');
            if (img && window.ArtworkManager) {
                window.ArtworkManager.displayArtwork(img, data.artwork, 'noArtwork');
            }
        },

        onMount(container) {},
        onRemove(container) {}
    }
};
