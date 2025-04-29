// Configuration
const config = {
    showMouseCursor: true,  // Set to true to show the mouse cursor, false to hide it
    wsUrl: 'ws://localhost:8765',
    skipFactor: 1,          // Process 1 out of every N events (higher = more skipping)
    disableTransitions: true, // Set to true to disable CSS transitions on the pointer
    bypassRAF: true,        // Bypass requestAnimationFrame for immediate updates
    useShadowPointer: false, // Use a shadow pointer for immediate visual feedback
    showDebugOverlay: false  // Show the debug overlay
};

// Global variables for laser event optimization
let lastLaserEvent = null;
let isAnimationRunning = false;

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
    
    // Create debug overlay
    createDebugOverlay();
    
    // Initialize WebSocket for cursor and controls
    initWebSocket();
    
    // Start the animation frame loop for processing laser events
    processLaserEvents();
    
    // Try to identify pointer elements right away
    findPointerElements();
    
    // Set up observer to find pointer elements when they're added or changed
    setupMutationObserver();
    
    // Create shadow pointer for visual feedback
    createShadowPointer();
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

// Set up mutation observer to find pointer elements when they change
function setupMutationObserver() {
    // Create an observer to watch for DOM changes
    const observer = new MutationObserver((mutations) => {
        // Look for added nodes that might be pointer elements
        for (const mutation of mutations) {
            if (mutation.type === 'childList') {
                // Check added nodes
                for (const node of mutation.addedNodes) {
                    if (node.nodeType === Node.ELEMENT_NODE) {
                        checkElement(node);
                        // Also check children of added nodes
                        node.querySelectorAll('*').forEach(checkElement);
                    }
                }
            } else if (mutation.type === 'attributes') {
                // If an element's attributes are changing, it might be the pointer
                checkElement(mutation.target);
            }
        }
    });
    
    // Start observing the whole document
    observer.observe(document.documentElement, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['style', 'transform', 'class']
    });
    
    console.log("Mutation observer set up to find pointer elements");
}

// Check if an element might be a pointer element
function checkElement(element) {
    // Skip if we've already processed this element
    if (pointerElements.includes(element)) return;
    
    // Check for pointer-related attributes
    const isPointer = 
        (element.id && (element.id.includes('pointer') || element.id.includes('cursor'))) ||
        (element.className && (String(element.className).includes('pointer') || String(element.className).includes('cursor'))) ||
        element.tagName.toLowerCase() === 'g' ||
        element.hasAttribute('transform') ||
        getComputedStyle(element).transform !== 'none';
    
    if (isPointer) {
        pointerElements.push(element);
        console.log("Found potential pointer element:", element.tagName, element.id, element.className);
        
        // Test applying a transform to see if it moves correctly
        const originalTransform = element.style.transform || '';
        const originalAttrTransform = element.getAttribute('transform') || '';
        
        // We'll restore these immediately
        if (element.style.transform !== undefined) {
            element.style.transform = 'rotate(180deg)';
            setTimeout(() => {
                element.style.transform = originalTransform;
            }, 10);
        }
        
        if (element.hasAttribute('transform')) {
            element.setAttribute('transform', 'rotate(180)');
            setTimeout(() => {
                if (originalAttrTransform) {
                    element.setAttribute('transform', originalAttrTransform);
                } else {
                    element.removeAttribute('transform');
                }
            }, 10);
        }
    }
}

// Function to find pointer elements more aggressively
function findPointerElements() {
    console.log("Searching for pointer elements...");
    
    // Try common patterns for the wheel pointer
    const selectors = [
        '#laser-pointer',
        '.wheel-pointer',
        '[class*="pointer"]',
        '[id*="pointer"]',
        '[class*="cursor"]',
        '[id*="cursor"]',
        'g[transform]',
        '.top-wheel-pointer',
        'line',
        'path',
        'polygon',
        '*[style*="transform"]',
        '*[style*="rotate"]'
    ];
    
    // Try to find elements matching our selectors
    for (const selector of selectors) {
        try {
            const elements = document.querySelectorAll(selector);
            if (elements.length > 0) {
                console.log(`Found ${elements.length} potential pointer elements with selector: ${selector}`);
                elements.forEach(el => {
                    if (!pointerElements.includes(el)) {
                        pointerElements.push(el);
                    }
                });
            }
        } catch (e) {
            console.error(`Error with selector ${selector}:`, e);
        }
    }
    
    // Also look for elements with transform styles
    document.querySelectorAll('*').forEach(el => {
        try {
            const style = window.getComputedStyle(el);
            if (style.transform !== 'none') {
                if (!pointerElements.includes(el)) {
                    pointerElements.push(el);
                    console.log("Found element with transform:", el.tagName, el.id, el.className);
                }
            }
        } catch (e) {
            // Ignore errors for inaccessible elements
        }
    });
    
    console.log(`Found ${pointerElements.length} potential pointer elements`);
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

// Create debug overlay to show stats
function createDebugOverlay() {
    const overlay = document.createElement('div');
    overlay.id = 'debug-overlay';
    overlay.style.cssText = `
        position: fixed;
        top: 10px;
        left: 10px;
        background: rgba(0,0,0,0.7);
        color: #00ff00;
        padding: 10px;
        border-radius: 5px;
        font-family: monospace;
        font-size: 12px;
        z-index: 9999;
        pointer-events: none;
        display: ${config.showDebugOverlay ? 'block' : 'none'};
    `;
    document.body.appendChild(overlay);
    
    // Update the debug overlay every 300ms
    setInterval(updateDebugOverlay, 300);
}

// Toggle debug overlay visibility
function toggleDebugOverlay() {
    config.showDebugOverlay = !config.showDebugOverlay;
    const overlay = document.getElementById('debug-overlay');
    if (overlay) {
        overlay.style.display = config.showDebugOverlay ? 'block' : 'none';
    }
    console.log(`Debug overlay ${config.showDebugOverlay ? 'shown' : 'hidden'}`);
}

// Update the debug overlay with current stats
function updateDebugOverlay() {
    const overlay = document.getElementById('debug-overlay');
    if (!overlay || !config.showDebugOverlay) return;
    
    overlay.innerHTML = `
        <div>Events: ${eventsReceived} recv, ${eventsProcessed} proc, ${skippedEvents} skip</div>
        <div>Settings: Skip=${config.skipFactor}, Trans=${config.disableTransitions ? 'OFF' : 'ON'}</div>
        <div>FPS: ${(1000 / frameTimeAvg).toFixed(1)}, RAF: ${config.bypassRAF ? 'BYPASS' : 'USED'}</div>
        <div>Controls: -/+ = skip, T = trans, R = RAF, S = shadow, H = hide</div>
    `;
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
    // Map 0-100 laser position to wheel angle range (158-202)
    const minAngle = 158;
    const maxAngle = 202;
    const angle = minAngle + (data.position / 100) * (maxAngle - minAngle);
    
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
    
    uiStore.handleWheelChange();
}

// WebSocket handling for all device events
function initWebSocket() {
    const ws = new WebSocket(config.wsUrl);
    
    ws.onopen = () => console.log('WebSocket connected');
    
    ws.onclose = () => {
        console.log('WebSocket disconnected, reconnecting in 1s...');
        setTimeout(initWebSocket, 1000);
    };
    
    ws.onerror = error => console.error('WebSocket error:', error);
    
    // Counter for event skipping
    let eventCounter = 0;
    
    ws.onmessage = event => {
        try {
            const { type, data } = JSON.parse(event.data);
            
            // Special handling for laser events
            if (type === 'laser') {
                // Increment the received counter
                eventsReceived++;
                
                // Simple event skipping based on counter
                eventCounter = (eventCounter + 1) % config.skipFactor;
                
                // Only process on certain intervals based on skipFactor
                if (eventCounter === 0) {
                    // Bypass RAF for immediate processing if configured
                    if (config.bypassRAF) {
                        processLaserEvent(data);
                    } else {
                        // Set the most recent event for RAF processing
                        lastLaserEvent = data;
                        
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

// Add key controls for adjusting settings
document.addEventListener('keydown', (e) => {
    if ((e.key === '-' || e.key === '_') && config.skipFactor > 1) {
        // Decrease skip factor (process more events)
        config.skipFactor--;
        console.log(`Skip factor: ${config.skipFactor}`);
    } else if (e.key === '+' || e.key === '=' || e.key === 'ArrowUp') {
        // Increase skip factor (process fewer events)
        config.skipFactor++;
        console.log(`Skip factor: ${config.skipFactor}`);
    } else if (e.key === 't' || e.key === 'T') {
        // Toggle transitions
        config.disableTransitions = !config.disableTransitions;
        console.log(`Transitions ${config.disableTransitions ? 'disabled' : 'enabled'}`);
        updateTransitionStyles();
    } else if (e.key === 'r' || e.key === 'R') {
        // Toggle RAF bypass
        config.bypassRAF = !config.bypassRAF;
        console.log(`RAF bypass ${config.bypassRAF ? 'enabled' : 'disabled'}`);
    } else if (e.key === 's' || e.key === 'S') {
        // Toggle shadow pointer
        config.useShadowPointer = !config.useShadowPointer;
        console.log(`Shadow pointer ${config.useShadowPointer ? 'enabled' : 'disabled'}`);
        if (shadowPointer) {
            shadowPointer.style.display = config.useShadowPointer ? 'block' : 'none';
        } else {
            createShadowPointer();
        }
    } else if (e.key === 'h' || e.key === 'H') {
        // Toggle debug overlay
        toggleDebugOverlay();
    }
});

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
    // Adjust volume based on direction and speed
    const volumeStep = data.speed / 25; // Scale down for smoother control
    
    if (data.direction === 'clock') {
        uiStore.volume = Math.min(100, uiStore.volume + volumeStep);
    } else {
        uiStore.volume = Math.max(0, uiStore.volume - volumeStep);
    }
    
    // Update the volume arc
    uiStore.updateVolumeArc();
}

// Handle button press events
function handleButtonEvent(uiStore, data) {
    switch (data.button) {
        case 'left':
            // Simulate left action - move to previous menu item
            uiStore.topWheelPosition = -1;
            uiStore.handleWheelChange();
            break;
            
        case 'right':
            // Simulate right action - move to next menu item
            uiStore.topWheelPosition = 1;
            uiStore.handleWheelChange();
            break;
            
        case 'go':
            // Simulate selection/activation of current menu item
            const currentItem = uiStore.menuItems[uiStore.selectedMenuItem];
            if (currentItem) {
                console.log('Activating menu item:', currentItem.title);
                // The UI already navigates to the view when selected
                // This is just an additional activation if needed
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