// Centralized configuration for BeoSound 5c
// This file should be loaded before all other JavaScript files

const AppConfig = {
    // Home Assistant - for Apple TV display data (read-only)
    homeAssistant: {
        url: 'http://homeassistant.local:8123',
        webhookPath: '/api/webhook/beosound5c',
        // Token should be set via: localStorage.setItem('ha_token', 'your-token')
        // Or configure in browser console: AppConfig.homeAssistant.setToken('your-token')
        getToken: () => localStorage.getItem('ha_token') || '',
        setToken: (token) => localStorage.setItem('ha_token', token),
        getWebhookUrl: () => `${AppConfig.homeAssistant.url}${AppConfig.homeAssistant.webhookPath}`
    },

    // WebSocket endpoints
    websocket: {
        input: 'ws://localhost:8765/ws',
        media: 'ws://localhost:8766/ws'
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
