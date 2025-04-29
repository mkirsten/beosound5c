// Configuration
const config = {
    showMouseCursor: true,  // Set to true to show the mouse cursor, false to hide it
    wsUrl: 'ws://localhost:8765'
};

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
    
    // Initialize WebSocket for cursor and controls
    initWebSocket();
});

// WebSocket handling for all device events
function initWebSocket() {
    const ws = new WebSocket(config.wsUrl);
    
    ws.onopen = () => console.log('WebSocket connected');
    
    ws.onclose = () => {
        console.log('WebSocket disconnected, reconnecting in 1s...');
        setTimeout(initWebSocket, 1000);
    };
    
    ws.onerror = error => console.error('WebSocket error:', error);
    
    ws.onmessage = event => {
        try {
            const { type, data } = JSON.parse(event.data);
            console.log(`Received ${type} event:`, data);
            
            // Get reference to UI store
            const uiStore = window.uiStore;
            if (!uiStore) {
                console.error('UI Store not found');
                return;
            }
            
            switch (type) {
                case 'laser':
                    handleLaserEvent(uiStore, data);
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
                default:
                    console.log('Unknown event type:', type);
            }
        } catch (error) {
            console.error('Error processing message:', error);
        }
    };
}

// Handle laser position events
function handleLaserEvent(uiStore, data) {
    // Map 0-100 laser position to wheel angle range (158-202)
    const minAngle = 158;
    const maxAngle = 202;
    const angle = minAngle + (data.position / 100) * (maxAngle - minAngle);
    uiStore.wheelPointerAngle = angle;
    uiStore.handleWheelChange();
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