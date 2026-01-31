// Debug: Check if this file is loading


// Configuration - WebSocket URL loaded from AppConfig (config.js)
const config = {
    showMouseCursor: false,   // Hide mouse cursor on hardware device
    wsUrl: AppConfig.websocket.input,  // Loaded from centralized config
    skipFactor: 1,          // Process 1 out of every N events (higher = more skipping)
    disableTransitions: true, // Set to true to disable CSS transitions on the pointer
    bypassRAF: true,        // Bypass requestAnimationFrame for immediate updates
    useShadowPointer: false, // Use a shadow pointer for immediate visual feedback
    showDebugOverlay: true, // Show the debug overlay to help diagnose issues
    volumeProcessingDelay: 50, // Delay between volume updates processing in ms
    cursorHideDelay: 2000   // Delay in ms before hiding cursor after inactivity
};

// Reference to dummy hardware manager (from dummy-hardware.js)
// Note: dummyHardwareManager is declared in dummy-hardware.js as window.dummyHardwareManager

// Hardware simulation is now handled by dummy-hardware.js module

// Global variables for laser event optimization
let lastLaserEvent = { position: 93 };  // Initialize with position 93
let isAnimationRunning = false;
let lastVolumeUpdate = 0;
let volumeUpdatePending = false;
let pendingVolumeData = null;
let cursorHideTimeout = null;

// Volume adjustment variables
let requestVolumeChangeNotStarted = 0;
let requestVolumeChangeInProgress = 0;
let volumeProcessorRunning = false;

// Performance tracking
let lastUpdateTime = 0;
let frameTimeAvg = 0;

// Counters for debugging
let eventsReceived = 0;
let eventsProcessed = 0;
let skippedEvents = 0;

// DOM manipulation tracking
let domUpdateSuccesses = 0;
let domUpdateFailures = 0;
let lastKnownPointerAngle = 180; // Default middle position
let transformProperty = null;
let pointerElements = [];
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
    isAnimationRunning = true;
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
            
        default:
            console.log(`[EVENT] Unknown event type: ${type}`);
    }
}

// Process a single laser event
function processLaserEvent(data) {
    const pos = data.position;
    let angle;
    
    // Use laser position mapper if available
    if (window.LaserPositionMapper) {
        const { laserPositionToAngle } = window.LaserPositionMapper;
        angle = laserPositionToAngle(pos);
    } else {
        // Fallback to original calibration logic
        const MIN_LASER_POS = 3;
        const MID_LASER_POS = 72;
        const MAX_LASER_POS = 123;
        
        const MIN_ANGLE = 150;
        const MID_ANGLE = 180;
        const MAX_ANGLE = 210;
        
        if (pos <= MIN_LASER_POS) {
            angle = MIN_ANGLE;
        } else if (pos < MID_LASER_POS) {
            const slope = (MID_ANGLE - MIN_ANGLE) / (MID_LASER_POS - MIN_LASER_POS);
            angle = MIN_ANGLE + slope * (pos - MIN_LASER_POS);
        } else if (pos <= MAX_LASER_POS) {
            const slope = (MAX_ANGLE - MID_ANGLE) / (MAX_LASER_POS - MID_LASER_POS);
            angle = MID_ANGLE + slope * (pos - MID_LASER_POS);
        } else {
            angle = MAX_ANGLE;
        }
    }
    
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
    
    // Try to connect to real hardware server (silent attempt)
    try {
        const ws = new WebSocket('ws://localhost:8765');
        
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
    if (window.mediaWebSocket && window.mediaWebSocket.readyState === WebSocket.OPEN) {
        return;
    }
    
    if (mediaWebSocketConnecting) {
        return;
    }
    
    mediaWebSocketConnecting = true;
    
    try {
        const mediaWs = new WebSocket('ws://localhost:8766');
        window.mediaWebSocket = mediaWs;
        
        // Prevent browser from logging WebSocket errors
        mediaWs.onerror = () => {
            mediaWebSocketConnecting = false;
            // Silent failure - media server is optional
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
            // Reconnect after delay
            setTimeout(() => {
                if (!window.mediaWebSocket) {
                    initMediaWebSocket();
                }
            }, 3000);
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
    // Check if we need to forward nav events to iframe pages
    const currentPage = uiStore.currentRoute || 'unknown';
    const localHandledPages = ['menu/music', 'menu/settings', 'menu/scenes'];
    
    if (localHandledPages.includes(currentPage)) {
        // Forward nav events to iframe
        let iframeName = '';
        if (currentPage === 'menu/music') iframeName = 'music-iframe';
        else if (currentPage === 'menu/settings') iframeName = 'settings-iframe';
        else if (currentPage === 'menu/scenes') iframeName = 'scenes-iframe';
        
        const iframe = document.getElementById(iframeName);
        if (iframe && iframe.contentWindow) {
            iframe.contentWindow.postMessage({
                type: 'nav',
                data: data
            }, '*');
        }
        return; // Don't process nav events in parent when iframe should handle them
    }
    
    // Set topWheelPosition based on direction
    // clock = clockwise = down = positive
    // counter = counterclockwise = up = negative
    if (data.direction === 'clock') {
        uiStore.topWheelPosition = 1;
    } else {
        uiStore.topWheelPosition = -1;
    }
    
    // Let the UI handle the movement based on position
    uiStore.handleWheelChange();
}

// Handle volume wheel events
function handleVolumeEvent(uiStore, data) {
    if (!uiStore) return;
    
    // Convert the incoming volume change to a step size
    // The speed is the magnitude, the direction determines sign
    const volumeStepChange = data.direction === 'clock' 
        ? Math.min(3, data.speed / 10) // Cap adjustment for clockwise (increase)
        : -Math.min(3, data.speed / 10); // Cap adjustment for counter-clockwise (decrease)
    
    // Log the requested change for debugging
    console.log(`Volume change requested: ${volumeStepChange.toFixed(1)}`);
    
    // Accumulate the requested change
    // This is thread 1 - it just adds to the pending change amount
    requestVolumeChangeNotStarted += volumeStepChange;
    
    // Log the accumulated change for debugging
    console.log(`Accumulated volume change: ${requestVolumeChangeNotStarted.toFixed(1)}`);
}

// Start the volume processor - this runs as "thread 2"
function startVolumeProcessor() {
    // Only start if not already running
    if (volumeProcessorRunning) return;
    
    volumeProcessorRunning = true;
    
    // Log that we're starting the processor
    console.log("Volume processor started");
    
    // Define the processor function
    const processVolumeChanges = async () => {
        while (volumeProcessorRunning) {
            // Check if there are pending volume changes
            if (requestVolumeChangeNotStarted !== 0) {
                // Thread safety: Quickly capture and reset the pending value
                // This minimizes the time we're accessing the shared variable
                requestVolumeChangeInProgress = requestVolumeChangeNotStarted;
                requestVolumeChangeNotStarted = 0;
                
                // Log the processing step
                console.log(`Processing volume change: ${requestVolumeChangeInProgress.toFixed(1)}`);
                
                // Apply the change to the UI store
                const uiStore = window.uiStore;
                if (uiStore) {
                    // Apply the volume change with limits
                    uiStore.volume = Math.max(0, Math.min(100, uiStore.volume + requestVolumeChangeInProgress));
                    
                    // Update the volume arc UI
                    uiStore.updateVolumeArc();
                    
                    // Log to debug overlay
                    if (uiStore.logWebsocketMessage) {
                        uiStore.logWebsocketMessage(`Volume adjusted to: ${uiStore.volume.toFixed(1)}%`);
                    }
                }
                
                // Clear the in-progress flag
                requestVolumeChangeInProgress = 0;
            }
            
            // Wait a bit before processing more changes
            // This introduces a small delay between updates to avoid UI lag
            await new Promise(resolve => setTimeout(resolve, config.volumeProcessingDelay));
        }
    };
    
    // Start the processor loop
    processVolumeChanges();
}

// Handle button press events
function handleButtonEvent(uiStore, data) {
    // Log current page/route
    const currentPage = uiStore.currentRoute || 'unknown';
    console.log(`🔵 [BUTTON] Button pressed: ${data.button} on page: ${currentPage}`);
    
    // Log to debug overlay
    if (window.uiStore && window.uiStore.logWebsocketMessage) {
        window.uiStore.logWebsocketMessage(`🔵 Button pressed: ${data.button} on page: ${currentPage}`);
    }
    
    // Forward button events to iframe pages that handle their own navigation
    const localHandledPages = ['menu/music', 'menu/system', 'menu/scenes'];
    if (localHandledPages.includes(currentPage)) {
        console.log(`🔵 [BUTTON] On ${currentPage} page - forwarding button to iframe`);
        if (window.uiStore && window.uiStore.logWebsocketMessage) {
            window.uiStore.logWebsocketMessage(`🔵 On ${currentPage} page - forwarding button to iframe`);
        }

        // Forward the button event to the appropriate iframe
        let iframeName = '';
        if (currentPage === 'menu/music') iframeName = 'music-iframe';
        else if (currentPage === 'menu/system') iframeName = 'system-iframe';
        else if (currentPage === 'menu/scenes') iframeName = 'scenes-iframe';
        
        const iframe = document.getElementById(iframeName);
        if (iframe && iframe.contentWindow) {
            console.log(`🔵 [BUTTON] Sending button event to iframe ${iframeName}`);
            // Send the button event to the iframe
            iframe.contentWindow.postMessage({
                type: 'button',
                button: data.button
            }, '*');
        } else {
            console.log(`🔴 [BUTTON] ERROR: Iframe ${iframeName} not found or not ready`);
        }
        return;
    }
    

    
    // Send webhook for all contexts
    const contextMap = {
        'menu/security': 'security',
        'menu/playing': 'now_playing',
        'menu/showing': 'now_showing',
        'menu/music': 'music',
        'menu/settings': 'settings', 
        'menu/scenes': 'scenes'
    };
    
    const panelContext = contextMap[currentPage] || 'unknown';
    console.log(`🟡 [WEBHOOK] Preparing webhook for ${currentPage} (context: ${panelContext}): ${data.button}`);
    if (window.uiStore && window.uiStore.logWebsocketMessage) {
        window.uiStore.logWebsocketMessage(`🟡 Preparing webhook for ${panelContext}: ${data.button}`);
    }
    sendWebhook(panelContext, data.button);
}

// Send webhook for button events
function sendWebhook(panelContext, button, id = '1') {
    const webhookUrl = AppConfig.homeAssistant.getWebhookUrl();
    
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
