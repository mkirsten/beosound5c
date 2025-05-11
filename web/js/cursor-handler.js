// Configuration
const config = {
    showMouseCursor: true,  // Set to true to show the mouse cursor, false to hide it
    wsUrl: 'ws://localhost:8765',
    skipFactor: 1,          // Process 1 out of every N events (higher = more skipping)
    disableTransitions: true, // Set to true to disable CSS transitions on the pointer
    bypassRAF: true,        // Bypass requestAnimationFrame for immediate updates
    useShadowPointer: false, // Use a shadow pointer for immediate visual feedback
    showDebugOverlay: false, // Show the debug overlay
    volumeProcessingDelay: 50 // Delay between volume updates processing in ms
};

// Global variables for laser event optimization
let lastLaserEvent = null;
let isAnimationRunning = false;
let lastVolumeUpdate = 0;
let volumeUpdatePending = false;
let pendingVolumeData = null;

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
    console.log("Cursor visibility setting:", config.showMouseCursor);
    
    // Add a style element for cursor control
    const style = document.createElement('style');
    
    if (config.showMouseCursor) {
        // Explicitly override any existing cursor: none styles
        style.textContent = `
            body, div, svg, path, ellipse, * { cursor: auto !important; }
            #viewport { cursor: auto !important; }
            .list-item { cursor: pointer !important; }
            .flow-item { cursor: pointer !important; }
        `;
        console.log("Setting cursor to visible");
    } else {
        style.textContent = '* { cursor: none !important; }';
        console.log("Setting cursor to hidden");
    }
    
    document.head.appendChild(style);
    
    // Create style for disabling transitions if needed
    updateTransitionStyles();
    
    // Initialize WebSocket for cursor and controls
    initWebSocket();
    
    // Start the animation frame loop for processing laser events
    processLaserEvents();
    
    // Create shadow pointer for visual feedback
    createShadowPointer();
    
    // Start the volume processor loop
    startVolumeProcessor();
});

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
        console.log("Disabled transitions on pointer elements");
    } else {
        console.log("Enabled transitions on pointer elements");
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

// Process a single laser event
function processLaserEvent(data) {
    // Log the original laser position (0-100)
    console.log(`Laser position: ${data.position}`);
    
    // Calibration points as variables
    const MIN_LASER_POS = 3;
    const MID_LASER_POS = 72;
    const MAX_LASER_POS = 123;
    
    const MIN_ANGLE = 150;
    const MID_ANGLE = 180;
    const MAX_ANGLE = 210;
    
    // Custom mapping based on calibration points
    let angle;
    const pos = data.position;
    
    if (pos <= MIN_LASER_POS) {
        // At or below minimum
        angle = MIN_ANGLE;
    } else if (pos < MID_LASER_POS) {
        // Between min and mid, map to MIN_ANGLE-MID_ANGLE
        const slope = (MID_ANGLE - MIN_ANGLE) / (MID_LASER_POS - MIN_LASER_POS);
        angle = MIN_ANGLE + slope * (pos - MIN_LASER_POS);
    } else if (pos <= MAX_LASER_POS) {
        // Between mid and max, map to MID_ANGLE-MAX_ANGLE
        const slope = (MAX_ANGLE - MID_ANGLE) / (MAX_LASER_POS - MID_LASER_POS);
        angle = MID_ANGLE + slope * (pos - MID_LASER_POS);
    } else {
        // Above maximum
        angle = MAX_ANGLE;
    }
    
    // Log the calculated angle
    console.log(`Laser position: ${pos}, Wheel angle: ${angle.toFixed(2)}`);
    
    // Store the last known angle
    lastKnownPointerAngle = angle;
    
    // First, update shadow pointer for immediate visual feedback
    updateShadowPointer(angle);
    
    // Update via store
    updateViaStore(angle);
    
    // Clear the event and increment counter
    lastLaserEvent = null;
    eventsProcessed++;
}

// Update via the uiStore (default method)
function updateViaStore(angle) {
    const uiStore = window.uiStore;
    if (!uiStore) return;
    
    // Direct update with no prediction or smoothing
    uiStore.wheelPointerAngle = angle;
    
    // Try to bypass any transition effects by forcing immediate update
    if (config.disableTransitions) {
        // Force pointer redraw if possible - this depends on the UI implementation
        if (typeof uiStore.forceUpdate === 'function') {
            uiStore.forceUpdate();
        }
    }
    
    // Update laser position in debug overlay if available
    if (uiStore.setLaserPosition) {
        uiStore.setLaserPosition(lastLaserEvent?.position || 0);
    }
    
    uiStore.handleWheelChange();
}

// WebSocket handling for all device events
function initWebSocket() {
    const ws = new WebSocket(config.wsUrl);
    
    ws.onopen = () => {
        console.log('WebSocket connected');
        // Log to debug overlay if available
        if (window.uiStore && window.uiStore.logWebsocketMessage) {
            window.uiStore.logWebsocketMessage('WebSocket connected');
        }
    };
    
    ws.onclose = () => {
        console.log('WebSocket disconnected, reconnecting in 1s...');
        // Log to debug overlay if available
        if (window.uiStore && window.uiStore.logWebsocketMessage) {
            window.uiStore.logWebsocketMessage('WebSocket disconnected, reconnecting...');
        }
        setTimeout(initWebSocket, 1000);
    };
    
    ws.onerror = error => {
        console.error('WebSocket error:', error);
        // Log to debug overlay if available
        if (window.uiStore && window.uiStore.logWebsocketMessage) {
            window.uiStore.logWebsocketMessage(`WebSocket error: ${error}`);
        }
    };
    
    // Counter for event skipping
    let eventCounter = 0;
    
    ws.onmessage = event => {
        try {
            const message = JSON.parse(event.data);
            const { type, data } = message;
            
            // Log to debug overlay if available (for non-laser events to avoid spam)
            if (window.uiStore && window.uiStore.logWebsocketMessage && type !== 'laser') {
                window.uiStore.logWebsocketMessage(JSON.stringify(message));
            }
            
            // Special handling for laser events
            if (type === 'laser') {
                // Increment the received counter
                eventsReceived++;
                
                // Simple event skipping based on counter
                eventCounter = (eventCounter + 1) % config.skipFactor;
                
                // Only process on certain intervals based on skipFactor
                if (eventCounter === 0) {
                    // Store the laser position for the debug overlay
                    lastLaserEvent = data;
                    
                    // Bypass RAF for immediate processing if configured
                    if (config.bypassRAF) {
                        processLaserEvent(data);
                    } else {
                        // Ensure the animation loop is running
                        if (!isAnimationRunning) {
                            processLaserEvents();
                        }
                    }
                } else {
                    // Count skipped events
                    skippedEvents++;
                }
                
                return; // Exit early
            }
            
            // Non-laser events are logged normally
            console.log(`Received ${type} event:`, data);
            
            // Get reference to UI store for other event types
            const uiStore = window.uiStore;
            if (!uiStore) {
                console.error('UI Store not found');
                return;
            }
            
            // Process non-laser events as before
            switch (type) {
                case 'nav':
                    handleNavEvent(uiStore, data);
                    break;
                case 'volume':
                    handleVolumeEvent(uiStore, data);
                    break;
                case 'button':
                    handleButtonEvent(uiStore, data);
                    break;
                default:
                    console.log('Unknown event type:', type);
            }
        } catch (error) {
            console.error('Error processing message:', error);
        }
    };
}

// Handle navigation wheel events
function handleNavEvent(uiStore, data) {
    // Set topWheelPosition based on direction
    // clock = clockwise = down = positive
    // counter = counterclockwise = up = negative
    if (data.direction === 'clock') {
        uiStore.topWheelPosition = 1;
    } else {
        uiStore.topWheelPosition = -1;
    }
    
    // Let the UI handle the movement based on position
    //uiStore.handleWheelChange();
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
    // Log to debug overlay
    if (window.uiStore && window.uiStore.logWebsocketMessage) {
        window.uiStore.logWebsocketMessage(`Button pressed: ${data.button}`);
    }
    
    switch (data.button) {
        case 'left':
            // Previous track
            console.log('Previous track button pressed');
            if (uiStore.sendMediaCommand) {
                uiStore.sendMediaCommand('media_previous_track');
            }
            break;
            
        case 'right':
            // Next track
            console.log('Next track button pressed');
            if (uiStore.sendMediaCommand) {
                uiStore.sendMediaCommand('media_next_track');
            }
            break;
            
        case 'go':
            // Play/Pause
            console.log('Play/Pause button pressed');
            if (uiStore.sendMediaCommand) {
                uiStore.sendMediaCommand('media_play_pause');
            }
            break;
            
        case 'power':
            // Power button handling if needed
            console.log('Power button pressed');
            break;
            
        default:
            console.log('Unknown button:', data.button);
    }
} 