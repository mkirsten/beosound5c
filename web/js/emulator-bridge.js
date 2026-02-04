// Emulator Bridge for BeoSound 5c
// Handles all communication between iframe content and parent emulator
// This keeps emulator concerns isolated from core application code

const EmulatorBridge = {
    // Whether we're running inside the emulator
    isInEmulator: false,
    parentWindow: null,

    init() {
        this.isInEmulator = window.parent !== window;
        this.parentWindow = this.isInEmulator ? window.parent : null;

        if (this.isInEmulator) {
            console.log('[EMULATOR-BRIDGE] Running in emulator mode');
        }
    },

    // Send a message to the parent emulator (no-op if not in emulator)
    sendToParent(type, data = {}) {
        if (!this.isInEmulator || !this.parentWindow) return false;

        this.parentWindow.postMessage({ type, ...data }, '*');
        console.log(`[EMULATOR-BRIDGE] Sent: ${type}`, data);
        return true;
    },

    // === Scene notifications ===
    notifySceneActivated(sceneId, sceneName) {
        return this.sendToParent('scene_activated', { sceneId, sceneName });
    },

    // === Music/Playback notifications ===
    notifyPlaylistSelected(playlistId, playlistName) {
        return this.sendToParent('go_action', {
            actionType: 'playlist',
            playlistId,
            playlistName
        });
    },

    notifyTrackSelected(trackIndex, trackName, playlistId, playlistName) {
        return this.sendToParent('go_action', {
            actionType: 'track',
            trackIndex,
            trackName,
            playlistId,
            playlistName
        });
    },

    // === Playback control notifications (from Playing view) ===
    notifyPlaybackControl(action) {
        // action: 'prev_track', 'next_track', 'toggle_playback'
        return this.sendToParent('playback_control', { action });
    },

    // === View change notifications ===
    notifyViewChanged(path) {
        return this.sendToParent('view_changed', { path });
    },

    // === State reporting ===
    reportState(state) {
        return this.sendToParent('state_update', state);
    },

    // === Request data from emulator ===
    requestCurrentTrack() {
        return this.sendToParent('request_track', {});
    }
};

// Initialize on load
document.addEventListener('DOMContentLoaded', () => {
    EmulatorBridge.init();
});

// Also initialize immediately if DOM is already ready
if (document.readyState !== 'loading') {
    EmulatorBridge.init();
}

// Make available globally
window.EmulatorBridge = EmulatorBridge;
