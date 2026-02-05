// Centralized configuration for BeoSound 5c - KITCHEN DEVICE
// All HA communication goes through the backend - no credentials needed here

const AppConfig = {
    // Device identification
    deviceName: 'kitchen',  // 'church' (5c) or 'kitchen' (5d)

    // Device-specific data files
    scenesFile: '../json/scenes_kitchen.json',

    // Home Assistant configuration
    homeAssistant: {
        url: 'http://homeassistant.local:8123',
        securityDashboard: 'dashboard-cameras/home'  // Dashboard path for SECURITY page (without leading slash)
    },

    // Webhook forwarding endpoint (backend forwards to HA)
    webhookUrl: 'http://localhost:8767/forward',

    // WebSocket endpoints (browser connects to same host as web UI)
    websocket: {
        input: 'ws://localhost:8765',
        media: 'ws://localhost:8766'
    },

    // Camera overlay configuration
    cameras: [
        { id: 'door', title: 'Front door', entity: 'camera.doorbell_medium_resolution_channel' },
        { id: 'gate', title: 'Gate', entity: 'camera.g3_flex_high_resolution_channel_6' }
    ],

    // Debug settings
    debug: {
        enabled: false,
        logLevel: 'warn'  // 'debug', 'info', 'warn', 'error'
    },

    // Demo mode settings (for running without real hardware/services)
    demo: {
        enabled: false,      // Set true to force emulator mode, or use ?emulator=true URL param
        autoDetect: false    // Disabled on real hardware - don't activate emulator on service failure
    }
};

// Early emulator mode detection (before other scripts load)
(function() {
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('demo') === 'true') {
        AppConfig.demo.enabled = true;
        console.log('[CONFIG] Demo mode enabled via URL parameter');
    }
})();

// Simple debug logger that respects config settings
const Debug = {
    log: (component, ...args) => {
        if (AppConfig.debug.enabled && AppConfig.debug.logLevel === 'debug') {
            console.log(`[${component}]`, ...args);
        }
    },
    info: (component, ...args) => {
        if (AppConfig.debug.enabled && ['debug', 'info'].includes(AppConfig.debug.logLevel)) {
            console.info(`[${component}]`, ...args);
        }
    },
    warn: (component, ...args) => {
        if (AppConfig.debug.enabled) {
            console.warn(`[${component}]`, ...args);
        }
    },
    error: (component, ...args) => {
        // Always log errors regardless of debug settings
        console.error(`[${component}]`, ...args);
    }
};

// Make config available globally
window.AppConfig = AppConfig;
window.Debug = Debug;
