// Debug: Check if this file is loading


// Configuration - uses Constants for timeout values
const config = {
    showMouseCursor: false,   // Hide mouse cursor on hardware device
    wsUrl: AppConfig.websocket.input,  // Loaded from centralized config
    skipFactor: 1,          // Process 1 out of every N events (higher = more skipping)
    disableTransitions: true, // Disable CSS transitions on the pointer for responsiveness
    bypassRAF: true,        // Bypass requestAnimationFrame for immediate updates
    useShadowPointer: false, // Use a shadow pointer for immediate visual feedback
    showDebugOverlay: true, // Show the debug overlay to help diagnose issues

    // Timeouts from centralized Constants (with fallbacks)
    get volumeProcessingDelay() {
        return window.Constants?.timeouts?.volumeProcessing || 50;
    },
    get cursorHideDelay() {
        return window.Constants?.timeouts?.cursorHide || 2000;
    }
};

// Reference to dummy hardware manager (from dummy-hardware.js)
// Note: dummyHardwareManager is declared in dummy-hardware.js as window.dummyHardwareManager

// Hardware simulation is now handled by dummy-hardware.js module

// Global variables for laser event optimization
// Default position from Constants (fallback to 93)
const defaultLaserPosition = window.Constants?.laser?.defaultPosition || 93;
let lastLaserEvent = { position: defaultLaserPosition };
let cursorHideTimeout = null;

// Performance tracking
let lastUpdateTime = 0;
let frameTimeAvg = 0;
let eventsProcessed = 0;

// Pointer state
let lastKnownPointerAngle = 180; // Default middle position
let shadowPointer = null;

// Mouse cursor visibility control
document.addEventListener('DOMContentLoaded', () => {
    
    // Add a style element for cursor control
    const style = document.createElement('style');
    style.id = 'cursor-style';
    
    // Set initial cursor visibility based on config
    if (config.showMouseCursor) {
        style.textContent = `
            body, div, svg, path, ellipse { cursor: auto !important; }
            #viewport { cursor: auto !important; }
            .list-item { cursor: pointer !important; }
            .flow-item { cursor: pointer !important; }
            iframe, #security-iframe { cursor: auto !important; pointer-events: auto !important; z-index: 1000 !important; }
        `;
    } else {
        style.textContent = `
            *, iframe, #security-iframe { cursor: none !important; }
            iframe, #security-iframe { pointer-events: auto !important; z-index: 1000 !important; }
        `;
        console.log('[CURSOR] Mouse cursor hidden - config.showMouseCursor:', config.showMouseCursor);
    }
    document.head.appendChild(style);
    console.log('[CURSOR] Style element added to head');
    
    // Create style for disabling transitions if needed
    updateTransitionStyles();
    
    // Initialize WebSocket for cursor and controls (non-blocking)
    setTimeout(() => {
        try {
            initWebSocket();
        } catch (error) {
            console.error('❌ WebSocket initialization failed:', error);
        }
    }, 100); // Small delay to ensure it doesn't block
    
    // Process the initial laser position immediately
    if (lastLaserEvent && lastLaserEvent.position) {
        processLaserEvent(lastLaserEvent);
    }
    
    // Start the animation frame loop for processing laser events
    processLaserEvents();
    
    // Create shadow pointer for visual feedback
    createShadowPointer();
    
    // Volume processor removed
    
    // Dummy hardware manager is available as window.dummyHardwareManager
    
    // Add mousemove event listener to show cursor when moved (only if cursor is enabled)
    if (config.showMouseCursor) {
        document.addEventListener('mousemove', () => {
            showCursor();

            // Clear any existing timeout
            if (cursorHideTimeout) {
                clearTimeout(cursorHideTimeout);
            }

            // Set a new timeout to hide the cursor after delay
            cursorHideTimeout = setTimeout(hideCursor, config.cursorHideDelay);
        });
    }
    
    // Debug click events on security iframe
    document.addEventListener('click', (e) => {
        const securityIframe = document.getElementById('security-iframe');
        if (securityIframe) {
            console.log(`[CLICK DEBUG] Click on:`, e.target.tagName, e.target.id || 'no-id');
            if (e.target === securityIframe || e.target.closest('#security-iframe')) {
                console.log(`[CLICK DEBUG] Security iframe clicked!`);
            }
        }
    }, true); // Use capture phase to catch events first
});

// Function to show the cursor
function showCursor() {
    const cursorStyle = document.getElementById('cursor-style');
    if (cursorStyle) {
        cursorStyle.textContent = `
            body, div, svg, path, ellipse { cursor: auto !important; }
            #viewport { cursor: auto !important; }
            .list-item { cursor: pointer !important; }
            .flow-item { cursor: pointer !important; }
        `;
    }
}

// Function to hide the cursor
function hideCursor() {
    const cursorStyle = document.getElementById('cursor-style');
    if (cursorStyle) {
        cursorStyle.textContent = '* { cursor: none !important; }';
    }
}

// Create shadow pointer for immediate visual feedback
function createShadowPointer() {
    if (config.useShadowPointer) {
        // Create a shadow pointer element that will provide immediate visual feedback
        shadowPointer = document.createElement('div');
        shadowPointer.id = 'shadow-pointer';
        shadowPointer.style.cssText = `
            position: fixed;
            width: 20px;
            height: 20px;
            background-color: rgba(255, 0, 0, 0.5);
            border-radius: 50%;
            transform: translate(-50%, -50%);
            pointer-events: none;
            z-index: 10000;
            display: none;
        `;
        document.body.appendChild(shadowPointer);
    }
}

// Update the shadow pointer position
function updateShadowPointer(angle) {
    if (!config.useShadowPointer || !shadowPointer) return;
    
    // Calculate position based on angle
    // Assuming the wheel is centered in the viewport
    const centerX = window.innerWidth / 2;
    const centerY = window.innerHeight / 2;
    const radius = Math.min(centerX, centerY) * 0.8; // 80% of the smaller dimension
    
    // Convert angle from 158-202 range to radians
    const angleRad = (angle - 180) * (Math.PI / 180);
    
    // Calculate position
    const x = centerX + radius * Math.cos(angleRad);
    const y = centerY + radius * Math.sin(angleRad);
    
    // Update shadow pointer position
    shadowPointer.style.left = `${x}px`;
    shadowPointer.style.top = `${y}px`;
    shadowPointer.style.display = 'block';
}

// Function to update transition styles
function updateTransitionStyles() {
    // Remove existing transition style if present
    const existingStyle = document.getElementById('pointer-transition-style');
    if (existingStyle) {
        existingStyle.remove();
    }
    
    if (config.disableTransitions) {
        // Add style to disable transitions on laser pointer elements
        const transitionStyle = document.createElement('style');
        transitionStyle.id = 'pointer-transition-style';
        transitionStyle.textContent = `
            /* Target common pointer selectors */
            #laser-pointer,
            .wheel-pointer,
            [class*="pointer"],
            [id*="pointer"],
            [class*="cursor"],
            [id*="cursor"],
            .top-wheel-pointer,
            g[transform],
            [style*="transform"],
            [transform],
            *[style*="transition"],
            *[style*="rotate"],
            path, line, polygon {
                transition: none !important;
                animation: none !important;
                transition-property: none !important;
                animation-duration: 0s !important;
                transition-duration: 0s !important;
                will-change: transform;
                backface-visibility: hidden;
                transform: translateZ(0);
            }
            
            /* Speed up rendering with hardware acceleration hints */
            body, svg, #viewport {
                will-change: transform;
                backface-visibility: hidden;
                transform: translateZ(0);
            }
        `;
        document.head.appendChild(transitionStyle);
    }
}

// Animation frame loop to process laser events
function processLaserEvents() {
    const now = performance.now();
    const frameDelta = now - lastUpdateTime;
    
    // Update frame time average (simple exponential moving average)
    if (lastUpdateTime > 0) {
        frameTimeAvg = frameTimeAvg * 0.9 + frameDelta * 0.1;
    }
    lastUpdateTime = now;
    
    // Process the latest event if available
    if (lastLaserEvent !== null) {
        processLaserEvent(lastLaserEvent);
    }
    
    // Continue the animation loop
    requestAnimationFrame(processLaserEvents);
}

// Process WebSocket events (from real hardware or dummy server)
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
            // Handle external navigation commands (from HA webhook)
            handleExternalNavigation(uiStore, data);
            break;

        case 'camera_overlay':
            // Handle camera overlay commands (from HA webhook)
            handleCameraOverlayEvent(data);
            break;

        default:
            console.log(`[EVENT] Unknown event type: ${type}`);
    }
}

// Process a single laser event
function processLaserEvent(data) {
    const pos = data.position;

    // Convert laser position to angle using the mapper
    if (!window.LaserPositionMapper) {
        console.error('[LASER] LaserPositionMapper not loaded');
        return;
    }
    const { laserPositionToAngle } = window.LaserPositionMapper;
    const angle = laserPositionToAngle(pos);

    // Store the last known angle
    lastKnownPointerAngle = angle;
    
    // First, update shadow pointer for immediate visual feedback
    updateShadowPointer(angle);
    
    // Update via store, including laser position
    updateViaStore(angle, pos);
    
    // Clear the event and increment counter
    lastLaserEvent = null;
    eventsProcessed++;
}

// Update via the uiStore (default method)
function updateViaStore(angle, laserPosition) {
    const uiStore = window.uiStore;
    if (!uiStore) return;
    
    // Direct update with no prediction or smoothing
    uiStore.wheelPointerAngle = angle;
    
    // Store laser position for new mapping system
    if (laserPosition !== undefined) {
        uiStore.laserPosition = laserPosition;
    }
    
    // Try to bypass any transition effects by forcing immediate update
    if (config.disableTransitions) {
        // Force pointer redraw if possible - this depends on the UI implementation
        if (typeof uiStore.forceUpdate === 'function') {
            uiStore.forceUpdate();
        }
    }
    
    // Update laser position in debug overlay if available
    if (uiStore.setLaserPosition) {
        uiStore.setLaserPosition(laserPosition || lastLaserEvent?.position || 0);
    }
    
    uiStore.handleWheelChange();
}

// WebSocket handling for all device events
// Throttling for WebSocket connection logging
let lastWebSocketLogTime = 0;
const WEBSOCKET_LOG_THROTTLE = 1000; // 1 second
const ENABLE_WEBSOCKET_LOGGING = false; // Set to true to enable WebSocket connection logging

function shouldLogWebSocket() {
    if (!ENABLE_WEBSOCKET_LOGGING) return false; // Easy toggle for WebSocket logging
    
    const now = Date.now();
    if (now - lastWebSocketLogTime >= WEBSOCKET_LOG_THROTTLE) {
        lastWebSocketLogTime = now;
        return true;
    }
    return false;
}

// Global variables to prevent multiple connections
let mediaWebSocketConnecting = false;
let mainWebSocketConnecting = false;

function initWebSocket() {
    // Always start dummy hardware server first - it will handle keyboard/scroll input
    if (window.dummyHardwareManager) {
        const dummyServer = window.dummyHardwareManager.start();
        if (dummyServer) {
            // Create a fake WebSocket connection for the UI
            const fakeWs = {
                readyState: WebSocket.OPEN,
                onmessage: null,
                close: () => {},
                send: () => {}
            };
            
            // Add the fake connection to dummy server
            dummyServer.addClient(fakeWs);
            
            // Set up message handling
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
    
    // Skip real hardware connection in demo mode (cleaner for static hosting)
    if (AppConfig.demo?.enabled) {
        console.log('[WS] Demo mode - skipping real hardware connection');
        initMediaWebSocket();
        return;
    }

    // Try to connect to real hardware server (silent attempt)
    try {
        const ws = new WebSocket(AppConfig.websocket.input);
        
        // Set a short connection timeout
        const connectionTimeout = setTimeout(() => {
            ws.close();
        }, 1000);
        
        // Prevent browser from logging WebSocket errors
        ws.onerror = () => {
            clearTimeout(connectionTimeout);
            // Silent fallback - dummy server already running
        };
        
        ws.onopen = () => {
            clearTimeout(connectionTimeout);
            console.log('[WS] ✅ Real hardware connected - switching from emulation mode');
            
            // Real hardware server is available, disable dummy server
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
            
            // Only log if we were previously connected (not initial connection failure)
            if (ws.readyState === WebSocket.CLOSED && ws.protocol !== undefined) {
                console.log('[WS] ⚠️  Hardware disconnected - switching back to emulation mode');
            }
            
            // Start dummy server if real one disconnects
            setTimeout(() => {
                if (window.dummyHardwareManager) {
                    window.dummyHardwareManager.start();
                }
            }, 1000);
        };
        
    } catch (error) {
        // Silent fallback - dummy server already running
    }
    
    // Also initialize media server connection
    initMediaWebSocket();
}

// Separate function for media server connection
function initMediaWebSocket() {
    // Skip in demo mode - EmulatorModeManager handles mock media
    if (AppConfig.demo?.enabled) {
        console.log('[MEDIA] Demo mode - skipping media server connection');
        // Activate demo mode if not already active
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
        
        // Prevent browser from logging WebSocket errors
        mediaWs.onerror = () => {
            mediaWebSocketConnecting = false;
            // Auto-activate demo mode on media server failure if autoDetect enabled
            if (window.AppConfig?.demo?.autoDetect && window.EmulatorModeManager && !window.EmulatorModeManager.isActive) {
                window.EmulatorModeManager.activate('media server unavailable');
            }
        };
        
        mediaWs.onopen = () => {
            console.log('[MEDIA] 🎵 Media server connected (Sonos artwork available)');
            mediaWebSocketConnecting = false;
            if (window.uiStore && window.uiStore.logWebsocketMessage) {
                window.uiStore.logWebsocketMessage('Media server connected');
            }
        };
        
        mediaWs.onclose = () => {
            mediaWebSocketConnecting = false;
            window.mediaWebSocket = null;
            // Reconnect after delay (from Constants)
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
        // Silent failure - media server is optional
    }
}

// Dummy server functionality moved to dummy-hardware.js module

// Handle navigation wheel events
function handleNavEvent(uiStore, data) {
    const currentPage = uiStore.currentRoute || 'unknown';

    // Forward nav events to iframe pages that handle their own navigation
    if (window.IframeMessenger && window.IframeMessenger.routeHasIframe(currentPage)) {
        window.IframeMessenger.sendNavEvent(currentPage, data);
        return; // Don't process nav events in parent when iframe should handle them
    }

    // Set topWheelPosition based on direction
    // clock = clockwise = down = positive
    // counter = counterclockwise = up = negative
    uiStore.topWheelPosition = data.direction === 'clock' ? 1 : -1;

    // Let the UI handle the movement based on position
    uiStore.handleWheelChange();
}

// Handle volume wheel events
// Currently just logs the event - volume control implementation pending
function handleVolumeEvent(uiStore, data) {
    if (!uiStore) return;

    // Log volume event for debugging (implementation pending)
    const direction = data.direction === 'clock' ? 'up' : 'down';
    console.log(`[VOLUME] ${direction} (speed: ${data.speed})`);
}

// Handle external navigation commands (from HA webhook via input.py)
function handleExternalNavigation(uiStore, data) {
    const page = data.page;
    console.log(`🌐 [NAVIGATE] External navigation to: ${page}`);

    if (window.uiStore && window.uiStore.logWebsocketMessage) {
        window.uiStore.logWebsocketMessage(`🌐 External navigation to: ${page}`);
    }

    // Handle next/previous cycling through menu items
    if (page === 'next' || page === 'previous') {
        // Menu order: SHOWING, SYSTEM, SECURITY, SCENES, MUSIC, PLAYING
        const menuOrder = ['menu/showing', 'menu/system', 'menu/security', 'menu/scenes', 'menu/music', 'menu/playing'];
        const currentRoute = uiStore.currentRoute || 'menu/playing';
        let currentIndex = menuOrder.indexOf(currentRoute);
        if (currentIndex === -1) currentIndex = 5; // default to playing

        let newIndex;
        if (page === 'next') {
            newIndex = (currentIndex + 1) % menuOrder.length;
        } else {
            newIndex = (currentIndex - 1 + menuOrder.length) % menuOrder.length;
        }

        const route = menuOrder[newIndex];
        console.log(`🌐 [NAVIGATE] ${page}: ${currentRoute} -> ${route}`);
        uiStore.navigateToView(route);
        return;
    }

    // Map page names to routes (use actual route paths from ui.js)
    const pageRoutes = {
        'now_playing': 'menu/playing',
        'playing': 'menu/playing',
        'music': 'menu/music',
        'scenes': 'menu/scenes',
        'security': 'menu/security',
        'system': 'menu/system',
        'showing': 'menu/showing',
        'home': 'menu/home'
    };

    const route = pageRoutes[page] || page;

    // Navigate to the page using navigateToView (the actual method in ui.js)
    if (uiStore.navigateToView) {
        uiStore.navigateToView(route);
        console.log(`🌐 [NAVIGATE] Navigated to: ${route}`);
    } else {
        console.warn(`🌐 [NAVIGATE] No navigateToView method available on uiStore`);
    }
}

// Handle camera overlay events (from HA webhook)
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

// Handle button press events
function handleButtonEvent(uiStore, data) {
    const currentPage = uiStore.currentRoute || 'unknown';
    console.log(`[BUTTON] ${data.button} on ${currentPage}`);

    // Check if camera overlay is active - intercept GO, LEFT, RIGHT buttons
    if (window.CameraOverlayManager && window.CameraOverlayManager.isActive) {
        const button = data.button.toLowerCase();
        if (['go', 'left', 'right'].includes(button)) {
            const handled = window.CameraOverlayManager.handleAction(button);
            if (handled) {
                console.log(`[BUTTON] Handled by camera overlay: ${button}`);
                return;
            }
        }
    }

    // On security page, GO button opens camera overlay
    if (currentPage === 'menu/security' && data.button.toLowerCase() === 'go') {
        if (window.CameraOverlayManager) {
            console.log('[BUTTON] Opening camera overlay from security page');
            window.CameraOverlayManager.show();
            return;
        }
    }

    // Handle Playing view buttons in emulator mode - send playback controls via bridge
    if (currentPage === 'menu/playing' && window.EmulatorBridge?.isInEmulator) {
        const button = data.button.toLowerCase();
        const actionMap = {
            'left': 'prev_track',
            'right': 'next_track',
            'go': 'toggle_playback'
        };

        if (actionMap[button]) {
            window.EmulatorBridge.notifyPlaybackControl(actionMap[button]);
            return;
        }
    }

    // Forward button events to iframe pages that handle their own navigation
    if (window.IframeMessenger && window.IframeMessenger.routeHasIframe(currentPage)) {
        window.IframeMessenger.sendButtonEvent(currentPage, data.button);
        return;
    }

    // Send webhook for non-iframe contexts
    const contextMap = {
        'menu/security': 'security',
        'menu/playing': 'now_playing',
        'menu/showing': 'now_showing',
        'menu/music': 'music',
        'menu/settings': 'settings',
        'menu/scenes': 'scenes'
    };

    const panelContext = contextMap[currentPage] || 'unknown';
    sendWebhook(panelContext, data.button);
}

// Send webhook for button events
function sendWebhook(panelContext, button, id = '1') {
    const webhookUrl = AppConfig.webhookUrl;
    
    const payload = {
        device_type: 'Panel',
        panel_context: panelContext,
        button: button,
        id: id
    };
    
    console.log(`🟢 [WEBHOOK] Sending ${panelContext} webhook POST to: ${webhookUrl}`);
    console.log(`🟢 [WEBHOOK] Payload:`, JSON.stringify(payload, null, 2));
    
    if (window.uiStore && window.uiStore.logWebsocketMessage) {
        window.uiStore.logWebsocketMessage(`🟢 Sending ${panelContext} webhook: ${button}`);
    }
    
    const startTime = Date.now();
    
    fetch(webhookUrl, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
        timeout: 2000
    })
    .then(response => {
        const duration = Date.now() - startTime;
        if (response.ok) {
            console.log(`✅ [WEBHOOK] SUCCESS: ${panelContext} ${button} sent to webhook (${duration}ms)`);
            console.log(`✅ [WEBHOOK] Response status: ${response.status} ${response.statusText}`);
            if (window.uiStore && window.uiStore.logWebsocketMessage) {
                window.uiStore.logWebsocketMessage(`✅ ${panelContext} webhook SUCCESS: ${button} (${duration}ms)`);
            }
        } else {
            console.log(`❌ [WEBHOOK] FAILED: ${panelContext} ${button} - HTTP ${response.status} ${response.statusText} (${duration}ms)`);
            if (window.uiStore && window.uiStore.logWebsocketMessage) {
                window.uiStore.logWebsocketMessage(`❌ ${panelContext} webhook FAILED: ${button} - HTTP ${response.status}`);
            }
        }
    })
    .catch(error => {
        const duration = Date.now() - startTime;
        console.log(`🔴 [WEBHOOK] ERROR: ${panelContext} ${button} - ${error.message} (${duration}ms)`);
        console.log(`🔴 [WEBHOOK] Error details:`, error);
        if (window.uiStore && window.uiStore.logWebsocketMessage) {
            window.uiStore.logWebsocketMessage(`🔴 ${panelContext} webhook ERROR: ${button} - ${error.message}`);
        }
    });
}
