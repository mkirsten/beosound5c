// ── Spotify Source Preset ──
window.SourcePresets = window.SourcePresets || {};
window.SourcePresets.spotify = {
    // No controller — nav/button events route to the softarc iframe via IframeMessenger
    item: { title: 'SPOTIFY', path: 'menu/spotify' },
    after: 'menu/playing',
    view: {
        title: 'SPOTIFY',
        content: `
            <div id="spotify-container" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%;">
            </div>`,
        preloadId: 'preload-spotify',
        iframeSrc: 'softarc/spotify.html',
        containerId: 'spotify-container'
    },

    onAdd() {},

    onMount() {
        // The softarc iframe handles its own init via DOMContentLoaded
    },

    onRemove() {},

    // No playing sub-preset needed — DEFAULT_PLAYING_PRESET handles media_update
    // from the player service (Sonos or local/go-librespot) perfectly.
};
