/**
 * Plex Source Preset
 *
 * Browse mode: softarc iframe with playlist/track browser (same as Spotify/Apple Music/TIDAL).
 * Playing mode: shows track info in the standard PLAYING view via media_update
 *   events from the player service (Sonos/BlueSound handles artwork).
 */

// ── Plex Source Preset ──
window.SourcePresets = window.SourcePresets || {};
window.SourcePresets.plex = {
    // No controller — nav/button events route to the softarc iframe via IframeMessenger
    item: { title: 'PLEX', path: 'menu/plex' },
    after: 'menu/playing',
    view: {
        title: 'PLEX',
        content: `
            <div id="plex-container" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%;">
            </div>`,
        preloadId: 'preload-plex',
        iframeSrc: 'softarc/plex.html',
        containerId: 'plex-container'
    },

    onAdd() {},

    onMount() {
        // The softarc iframe handles its own init via DOMContentLoaded
    },

    onRemove() {},

    // PLAYING sub-preset: use media_update from beo-player-sonos (handles artwork perfectly)
    playing: {
        eventType: 'media_update'
    }
};
