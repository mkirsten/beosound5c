// Centralized configuration for BeoSound 5c
// All HA communication goes through the backend - no credentials needed here

const AppConfig = {
    // Webhook forwarding endpoint (backend forwards to HA)
    webhookUrl: 'http://localhost:8767/forward',

    // WebSocket endpoints (browser connects to same host as web UI)
    websocket: {
        input: 'ws://localhost:8765',
        media: 'ws://localhost:8766'
    },

    // Debug settings
    debug: {
        enabled: false,
        logLevel: 'warn'  // 'debug', 'info', 'warn', 'error'
    }
};

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
