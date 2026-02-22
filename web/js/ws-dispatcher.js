// WebSocket Dispatcher
// Manages WebSocket connections (hardware input + media server) and
// dispatches incoming messages to the appropriate handlers.
// Hardware input functions (processLaserEvent, handleNavEvent, etc.)
// are defined in hardware-input.js which loads before this file.

// WebSocket logging throttle
let lastWebSocketLogTime = 0;
const WEBSOCKET_LOG_THROTTLE = 1000;
const ENABLE_WEBSOCKET_LOGGING = false;

function shouldLogWebSocket() {
    if (!ENABLE_WEBSOCKET_LOGGING) return false;
    const now = Date.now();
    if (now - lastWebSocketLogTime >= WEBSOCKET_LOG_THROTTLE) {
        lastWebSocketLogTime = now;
        return true;
    }
    return false;
}

// Connection state
let mediaWebSocketConnecting = false;
let mainWebSocketConnecting = false;
let hwReconnectTimer = null;
const HW_RECONNECT_INTERVAL = 3000;

// ── Message Dispatch ──

function processWebSocketEvent(message) {
    const uiStore = window.uiStore;
    if (!uiStore) return;

    const type = message.type;
    const data = message.data;

    switch (type) {
        case 'laser':
            processLaserEvent(data);
            break;

        case 'nav':
            handleNavEvent(uiStore, data);
            break;

        case 'volume':
            handleVolumeEvent(uiStore, data);
            break;

        case 'button':
            handleButtonEvent(uiStore, data);
            break;

        case 'media_update':
            if (uiStore.handleMediaUpdate) {
                uiStore.handleMediaUpdate(data.data, data.reason);
            }
            break;

        case 'navigate':
            handleExternalNavigation(uiStore, data);
            break;

        case 'camera_overlay':
            handleCameraOverlayEvent(data);
            break;

        case 'menu_item':
            handleMenuItemEvent(uiStore, data);
            break;

        case 'source_change':
            handleSourceChange(uiStore, data);
            break;

        case 'volume_update':
            handleVolumeUpdate(data);
            break;

        default:
            // Generic source update: "{sourceId}_update" → SourcePresets[sourceId].controller
            if (type.endsWith('_update')) {
                const sourceId = type.slice(0, -'_update'.length);
                const ctrl = window.SourcePresets?.[sourceId]?.controller;
                if (ctrl?.updateMetadata) ctrl.updateMetadata(data);
                routeToPlayingPreset(uiStore, type, data);
            } else {
                console.log(`[EVENT] Unknown event type: ${type}`);
            }
    }
}

// ── Broadcast Event Handlers ──

function handleExternalNavigation(uiStore, data) {
    const page = data.page;
    console.log(`[NAVIGATE] External navigation to: ${page}`);

    if (window.uiStore && window.uiStore.logWebsocketMessage) {
        window.uiStore.logWebsocketMessage(`External navigation to: ${page}`);
    }

    // Handle next/previous cycling through visible menu items only
    if (page === 'next' || page === 'previous') {
        const visibleItems = uiStore.menuItems.filter(m => !m.hidden);
        const menuOrder = visibleItems.map(m => m.path);
        const currentRoute = uiStore.currentRoute || 'menu/playing';
        let currentIndex = menuOrder.indexOf(currentRoute);
        if (currentIndex === -1) currentIndex = menuOrder.length - 1;

        let newIndex;
        if (page === 'next') {
            newIndex = (currentIndex + 1) % menuOrder.length;
        } else {
            newIndex = (currentIndex - 1 + menuOrder.length) % menuOrder.length;
        }

        const route = menuOrder[newIndex];
        console.log(`[NAVIGATE] ${page}: ${currentRoute} -> ${route}`);
        uiStore.navigateToView(route);
        return;
    }

    // Map page names to routes
    const pageRoutes = {
        'now_playing': 'menu/playing',
        'playing': 'menu/playing',
        'spotify': 'menu/spotify',
        'scenes': 'menu/scenes',
        'security': 'menu/security',
        'system': 'menu/system',
        'showing': 'menu/showing',
        'home': 'menu/home'
    };

    const route = pageRoutes[page] || page;

    if (uiStore.navigateToView) {
        uiStore.navigateToView(route);
        console.log(`[NAVIGATE] Navigated to: ${route}`);
    } else {
        console.warn(`[NAVIGATE] No navigateToView method available on uiStore`);
    }
}

function handleCameraOverlayEvent(data) {
    const action = data.action;
    console.log(`[CAMERA] Overlay event: ${action}`);

    if (window.CameraOverlayManager) {
        if (action === 'show') {
            window.CameraOverlayManager.show(data);
        } else if (action === 'hide' || action === 'dismiss') {
            window.CameraOverlayManager.hide();
        }
    }
}

async function handleMenuItemEvent(uiStore, data) {
    const action = data.action;
    console.log(`[MENU_ITEM] ${action}`, data);

    if (action === 'add') {
        // Try loading source script if preset not yet available
        if (data.preset && !window.SourcePresets?.[data.preset] && uiStore._loadSourceScript) {
            await uiStore._loadSourceScript(data.preset);
        }
        const preset = data.preset && window.SourcePresets?.[data.preset];
        if (preset) {
            uiStore.addMenuItem(preset.item, preset.after, preset.view);
            setTimeout(() => {
                if (preset.onAdd) preset.onAdd(document.getElementById('contentArea'));
            }, 50);
        } else if (data.title && data.path) {
            // Non-preset: raw item definition
            uiStore.addMenuItem(
                { title: data.title, path: data.path },
                data.after || 'menu/playing',
                data.view || { title: data.title, content: `<div style="color:white;display:flex;align-items:center;justify-content:center;height:100%">${data.title}</div>` }
            );
        } else {
            console.warn('[MENU_ITEM] add requires preset or title+path');
        }
    } else if (action === 'remove') {
        const path = data.path || (data.preset && window.SourcePresets?.[data.preset]?.item.path);
        if (path) {
            const preset = data.preset && window.SourcePresets?.[data.preset];
            if (preset?.onRemove) preset.onRemove();
            uiStore.removeMenuItem(path);
        } else {
            console.warn('[MENU_ITEM] remove requires path or preset');
        }
    } else if (action === 'hide' || action === 'show') {
        const path = data.path || (data.preset && window.SourcePresets?.[data.preset]?.item.path);
        if (path && uiStore.hideMenuItem) {
            uiStore.hideMenuItem(path, action === 'hide');
        }
    }
}

function handleSourceChange(uiStore, data) {
    const sourceId = data.active_source;  // string or null
    const sourceName = data.source_name || null;
    const player = data.player || null;   // "local" | "remote" | null
    console.log(`[SOURCE] Active source changed: ${sourceId || 'none'} (${sourceName || 'HA fallback'}, player=${player || 'none'})`);

    uiStore.activeSource = sourceId;
    uiStore.activeSourcePlayer = player;
    uiStore.setActivePlayingPreset(sourceId);
}

function routeToPlayingPreset(uiStore, eventType, eventData) {
    if (uiStore.activePlayingPreset?.eventType === eventType) {
        uiStore.updatePlaying(eventData);
    }
}

// ── WebSocket Connections ──

function connectHardwareWebSocket() {
    if (hwReconnectTimer) {
        clearTimeout(hwReconnectTimer);
        hwReconnectTimer = null;
    }

    try {
        const ws = new WebSocket(AppConfig.websocket.input);
        let wasConnected = false;

        const connectionTimeout = setTimeout(() => {
            ws.close();
        }, 2000);

        ws.onerror = () => {
            clearTimeout(connectionTimeout);
        };

        ws.onopen = () => {
            clearTimeout(connectionTimeout);
            wasConnected = true;
            console.log('[WS] Real hardware connected - switching from emulation mode');

            if (window.dummyHardwareManager) {
                window.dummyHardwareManager.stop();
            }
        };

        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                processWebSocketEvent(msg);
            } catch (error) {
                console.error('[WS] Error parsing message:', error);
            }
        };

        ws.onclose = () => {
            clearTimeout(connectionTimeout);

            if (wasConnected) {
                console.log('[WS] Hardware disconnected - will reconnect');
            }

            // Re-enable dummy server while disconnected
            if (window.dummyHardwareManager) {
                window.dummyHardwareManager.start();
            }

            hwReconnectTimer = setTimeout(connectHardwareWebSocket, HW_RECONNECT_INTERVAL);
        };

    } catch (error) {
        hwReconnectTimer = setTimeout(connectHardwareWebSocket, HW_RECONNECT_INTERVAL);
    }
}

function initWebSocket() {
    // Always start dummy hardware server first
    if (window.dummyHardwareManager) {
        const dummyServer = window.dummyHardwareManager.start();
        if (dummyServer) {
            const fakeWs = {
                readyState: WebSocket.OPEN,
                onmessage: null,
                close: () => {},
                send: () => {}
            };

            dummyServer.addClient(fakeWs);

            fakeWs.onmessage = (event) => {
                try {
                    const msg = JSON.parse(event.data);
                    processWebSocketEvent(msg);
                } catch (error) {
                    console.error('[DUMMY-HW] Error processing message:', error);
                }
            };
        } else {
            console.error('[WS] Failed to start dummy hardware server');
        }
    } else {
        console.error('[WS] Dummy hardware manager not available');
    }

    // Skip real hardware connection in demo mode
    if (AppConfig.demo?.enabled) {
        console.log('[WS] Demo mode - skipping real hardware connection');
        initMediaWebSocket();
        return;
    }

    // Connect to real hardware with auto-reconnect
    connectHardwareWebSocket();

    // Also initialize media server connection
    initMediaWebSocket();
}

// Separate function for media server connection
function initMediaWebSocket() {
    // Skip in demo mode - EmulatorModeManager handles mock media
    if (AppConfig.demo?.enabled) {
        console.log('[MEDIA] Demo mode - skipping media server connection');
        if (window.EmulatorModeManager && !window.EmulatorModeManager.isActive) {
            setTimeout(() => window.EmulatorModeManager.activate('static emulator'), 500);
        }
        return;
    }

    if (window.mediaWebSocket && window.mediaWebSocket.readyState === WebSocket.OPEN) {
        return;
    }

    if (mediaWebSocketConnecting) {
        return;
    }

    mediaWebSocketConnecting = true;

    try {
        const mediaWs = new WebSocket(AppConfig.websocket.media);
        window.mediaWebSocket = mediaWs;

        mediaWs.onerror = () => {
            mediaWebSocketConnecting = false;
            // Auto-activate demo mode on media server failure if autoDetect enabled
            if (window.AppConfig?.demo?.autoDetect && window.EmulatorModeManager && !window.EmulatorModeManager.isActive) {
                window.EmulatorModeManager.activate('media server unavailable');
            }
        };

        mediaWs.onopen = () => {
            console.log('[MEDIA] Media server connected');
            mediaWebSocketConnecting = false;
            if (window.uiStore && window.uiStore.logWebsocketMessage) {
                window.uiStore.logWebsocketMessage('Media server connected');
            }
        };

        mediaWs.onclose = () => {
            mediaWebSocketConnecting = false;
            window.mediaWebSocket = null;
            const reconnectDelay = window.Constants?.timeouts?.websocketReconnect || 3000;
            setTimeout(() => {
                if (!window.mediaWebSocket) {
                    initMediaWebSocket();
                }
            }, reconnectDelay);
        };

        mediaWs.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);

                if (data.type === 'media_update' && window.uiStore && window.uiStore.handleMediaUpdate) {
                    window.uiStore.handleMediaUpdate(data.data, data.reason);
                }
            } catch (error) {
                console.error('[MEDIA-WS] Error processing message:', error);
            }
        };
    } catch (error) {
        mediaWebSocketConnecting = false;
    }
}

// ── Initialization ──

document.addEventListener('DOMContentLoaded', () => {
    // Small delay to ensure UI store is ready
    setTimeout(() => {
        try {
            initWebSocket();
        } catch (error) {
            console.error('WebSocket initialization failed:', error);
        }
    }, 100);
});
