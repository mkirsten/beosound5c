/**
 * Apple Music Source Preset
 *
 * Browse mode: softarc iframe with playlist/track browser (same as Spotify).
 * Playing mode: shows track info in the standard PLAYING view via media_update
 *   events from the player service (Sonos/BlueSound handles artwork).
 */

// ── Apple Music Source Preset ──
window.SourcePresets = window.SourcePresets || {};
window.SourcePresets.apple_music = {
    // No controller — nav/button events route to the softarc iframe via IframeMessenger
    item: { title: 'APPLE MUSIC', path: 'menu/apple_music' },
    after: 'menu/playing',
    view: {
        title: 'APPLE MUSIC',
        content: `
            <div id="apple-music-container" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%;">
            </div>`,
        preloadId: 'preload-apple-music'
    },

    onAdd() {},

    onMount() {
        // The softarc iframe handles its own init via DOMContentLoaded
    },

    onRemove() {},

    // PLAYING sub-preset: use media_update from beo-player-sonos (handles artwork perfectly)
    // When Sonos is the output, beo-player-sonos polls and broadcasts artwork/metadata.
    playing: {
        eventType: 'media_update'
    }
};
