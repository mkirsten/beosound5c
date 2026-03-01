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

    // PLAYING sub-preset: use media_update from beo-player-sonos (handles artwork perfectly)
    // When Sonos is the output, beo-player-sonos polls and broadcasts artwork/metadata.
    // For librespot fallback, spotify.py sends media_update in the same format.
    playing: {
        eventType: 'media_update'
    }
};
