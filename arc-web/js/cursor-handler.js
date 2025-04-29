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
    
    // Initialize WebSocket for laser cursor
    initWebSocket();
});

// WebSocket handling for laser cursor
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
            
            if (type === 'laser') {
                // Get reference to pointer UI elements
                const uiStore = window.uiStore;
                if (uiStore) {
                    // Map 0-100 laser position to wheel angle range (158-202)
                    const minAngle = 158;
                    const maxAngle = 202;
                    const angle = minAngle + (data.position / 100) * (maxAngle - minAngle);
                    uiStore.wheelPointerAngle = angle;
                    uiStore.handleWheelChange();
                }
            }
        } catch (error) {
            console.error('Error processing message:', error);
        }
    };
} 