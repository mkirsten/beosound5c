// Centralized configuration for BeoSound 5c
// All HA communication goes through the backend - no credentials needed here

const AppConfig = {
    // Device identification (overridden by json/config.json on deployed devices)
    deviceName: 'development',

    // Legacy fallback for scenes (only used if config.json has no scenes array)
    scenesFile: '../json/scenes.example.json',

    // Home Assistant configuration
    homeAssistant: {
        url: 'http://homeassistant.local:8123',
        securityDashboard: 'dashboard-cameras/home'  // Dashboard path for SECURITY page (without leading slash)
    },

    // Webhook forwarding endpoint (backend forwards to HA)
    webhookUrl: 'http://localhost:8767/forward',

    // Router service
    routerUrl: 'http://localhost:8770',

    // CD service
    cdServiceUrl: 'http://localhost:8769',

    // Spotify source
    spotifyServiceUrl: 'http://localhost:8771',

    // USB file source
    usbServiceUrl: 'http://localhost:8773',

    // News source (Guardian)
    newsServiceUrl: 'http://localhost:8776',

    // WebSocket endpoints (browser connects to same host as web UI)
    websocket: {
        input: 'ws://localhost:8765',
        media: 'ws://localhost:8766/ws'
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

// Load device-specific config from unified config.json (deployed per-device)
// Falls back to ../config/default.json for local development
(function() {
    function applyConfig(config) {
        if (config.device) AppConfig.deviceName = config.device;
        if (config.scenes) AppConfig.scenes = config.scenes;
        if (config.home_assistant) {
            if (config.home_assistant.url) AppConfig.homeAssistant.url = config.home_assistant.url;
        }
        if (config.menu && config.menu.SECURITY && typeof config.menu.SECURITY === 'object') {
            if (config.menu.SECURITY.dashboard) {
                AppConfig.homeAssistant.securityDashboard = config.menu.SECURITY.dashboard;
            }
        }
    }

    // Try deployed config first, then dev fallback
    var paths = ['json/config.json', '../config/default.json'];
    var loaded = false;
    for (var i = 0; i < paths.length && !loaded; i++) {
        try {
            var xhr = new XMLHttpRequest();
            xhr.open('GET', paths[i], false);
            xhr.send();
            if (xhr.status === 200) {
                applyConfig(JSON.parse(xhr.responseText));
                console.log('[CONFIG] Loaded ' + paths[i] + ', deviceName:', AppConfig.deviceName);
                loaded = true;
            }
        } catch (e) { /* try next */ }
    }
    if (!loaded) {
        console.warn('[CONFIG] No config.json found, using defaults');
    }
})();

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
