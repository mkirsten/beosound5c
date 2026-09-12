// Centralized configuration for BeoSound 5c
// All HA communication goes through the backend - no credentials needed here

const AppConfig = {
    // Device identification (overridden by json/config.json on deployed devices)
    deviceName: 'development',

    // Legacy fallback for scenes (only used if config.json has no scenes array)
    scenesFile: '../json/scenes.example.json',

    // Home Assistant configuration
    homeAssistant: {
        url: 'http://homeassistant.local:8123'
    },

    // Webhook forwarding endpoint (backend forwards to HA)
    webhookUrl: 'http://localhost:8767/forward',

    // Router service
    routerUrl: 'http://localhost:8770',

    // Input service (also proxies the Home Assistant reads: /people, /ha/panel)
    inputServiceUrl: 'http://localhost:8767',

    // CD service
    cdServiceUrl: 'http://localhost:8769',

    // Spotify source
    spotifyServiceUrl: 'http://localhost:8771',

    // Apple Music source
    appleMusicServiceUrl: 'http://localhost:8774',

    // TIDAL source
    tidalServiceUrl: 'http://localhost:8777',

    // Plex source
    plexServiceUrl: 'http://localhost:8778',

    // Jellyfin source
    jellyfinServiceUrl: 'http://localhost:8781',

    // USB file source
    usbServiceUrl: 'http://localhost:8773',

    // News source (Guardian)
    newsServiceUrl: 'http://localhost:8776',

    // Radio source (Radio Browser)
    radioServiceUrl: 'http://localhost:8779',

    // Player service (Sonos/BlueSound — for direct transport commands)
    playerUrl: 'http://localhost:8766',

    // WebSocket endpoints (browser connects to same host as web UI)
    websocket: {
        input: 'ws://localhost:8765',
        media: 'ws://localhost:8770/router/ws'
    },

    // Camera overlay: set a "cameras" array in config.json to enable it.
    // Empty by default on purpose — entity ids only mean something in one
    // Home Assistant install, so a shipped default would put tiles on every
    // device pointing at cameras that don't exist. The browser emulator gets
    // its own set from the demo config.
    cameras: [],

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
        if (Array.isArray(config.cameras) && config.cameras.length) {
            AppConfig.cameras = config.cameras;
        }
    }

    // Try the deployed config first, then the repo default.
    //
    // Relative, not absolute: the documented dev command serves web/ as the
    // document root (`cd web && python3 -m http.server`), where /config/ does
    // not exist — absolute paths made both reads 404 and left the UI on its
    // built-in defaults. Softarc pages live one level down, hence the ../.
    var up = location.pathname.indexOf('/softarc/') !== -1 ? '../' : '';
    var paths = [
        up + 'json/config.json',        // deployed device config
        up + 'json/demo/config.json',   // browser emulator / local dev
        up + '../config/default.json',  // repo checkout served from the root
    ];
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
        return;
    }
    if (urlParams.get('demo') === 'false') return;   // escape hatch

    // No beo-* services behind this page? Then it is a dev server or the
    // website emulator, and the UI should show bundled demo content rather
    // than an empty four-item arc. A device serves the UI from beo-http on
    // port 80, so anything else is a static server.
    //
    // Softarc pages inherit this via the same check — they load config.js too.
    // A device serves the UI over plain HTTP on port 80. The protocol check
    // matters as much as the port: on https the port is empty too, so keying
    // on the port alone made the public emulator look like a device, and every
    // source view then tried to reach a service on localhost.
    const port = location.port;
    const servedByDevice = location.protocol === 'http:' && (port === '' || port === '80');
    if (!servedByDevice && location.protocol !== 'file:') {
        AppConfig.demo.enabled = true;
        console.log(`[CONFIG] Demo mode enabled (${location.protocol}//${location.host})`);
        return;
    }

    // Child frames (softarc pages) inherit from the shell that embedded them.
    // Belt and braces: the shell may have been opened with ?demo=true, which
    // its iframes don't carry.
    try {
        if (window.parent !== window && window.parent.AppConfig?.demo?.enabled) {
            AppConfig.demo.enabled = true;
            console.log('[CONFIG] Demo mode inherited from parent frame');
        }
    } catch (e) {
        /* cross-origin parent — nothing to inherit */
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

/**
 * Get a service URL from AppConfig with localhost fallback.
 * @param {string} key   AppConfig property name, e.g. 'spotifyServiceUrl'
 * @param {number} port  Fallback localhost port
 * @returns {string}
 */
function getServiceUrl(key, port) {
    return (typeof AppConfig !== 'undefined' && AppConfig[key])
        || `http://localhost:${port}`;
}

// Make config available globally
window.AppConfig = AppConfig;
window.Debug = Debug;
window.getServiceUrl = getServiceUrl;
