/**
 * TIDAL Source Preset
 *
 * Browse mode: softarc iframe with playlist/track browser (same as Spotify/Apple Music).
 * Playing mode: shows track info in the standard PLAYING view via media_update
 *   events from the player service (Sonos/BlueSound handles artwork).
 */

// ── TIDAL Source Preset ──
window.SourcePresets = window.SourcePresets || {};
window.SourcePresets.tidal = {
    // No controller — nav/button events route to the softarc iframe via IframeMessenger
    item: { title: 'TIDAL', path: 'menu/tidal' },
    after: 'menu/playing',
    view: {
        title: 'TIDAL',
        content: `
            <div id="tidal-container" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%;">
            </div>`,
        preloadId: 'preload-tidal',
        iframeSrc: 'softarc/tidal.html',
        containerId: 'tidal-container'
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
