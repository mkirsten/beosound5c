/**
 * Dummy Hardware Simulation for BeoSound 5C
 * 
 * This module simulates the hardware WebSocket server when the real one isn't available.
 * It provides keyboard and mouse/trackpad input that generates the exact same WebSocket
 * messages as the real hardware server.
 */

// Dummy WebSocket server for standalone mode
class DummyWebSocketServer {
    constructor() {
        this.clients = new Set();
        this.isRunning = false;
    }

    start() {
        if (this.isRunning) return;
        this.isRunning = true;
        console.log('[DUMMY-HW] Dummy hardware server started for standalone mode');
        
        // Show mouse cursor when dummy hardware is active
        this.showCursor();
    }

    showCursor() {
        // Force cursor to be visible when using dummy hardware
        const cursorStyle = document.getElementById('cursor-style');
        if (cursorStyle) {
            cursorStyle.textContent = `
                body, div, svg, path, ellipse { cursor: auto !important; }
                #viewport { cursor: auto !important; }
                .list-item { cursor: pointer !important; }
                .flow-item { cursor: pointer !important; }
            `;
            console.log('[DUMMY-HW] Mouse cursor enabled for dummy hardware mode');
        }
    }

    stop() {
        this.isRunning = false;
        console.log('[DUMMY-HW] Dummy hardware server stopped');
    }

    addClient(client) {
        this.clients.add(client);
        console.log(`[DUMMY-HW] Client connected (${this.clients.size} total)`);
    }

    removeClient(client) {
        this.clients.delete(client);
        console.log(`[DUMMY-HW] Client disconnected (${this.clients.size} total)`);
    }

    broadcast(message) {
        if (!this.isRunning) return;
        
        const messageStr = JSON.stringify(message);
        console.log(`[DUMMY-HW] Broadcasting: ${messageStr}`);
        
        this.clients.forEach(client => {
            if (client.readyState === WebSocket.OPEN) {
                try {
                    client.onmessage({ data: messageStr });
                } catch (error) {
                    console.error('[DUMMY-HW] Error sending to client:', error);
                }
            }
        });
    }

    // Hardware event simulation methods
    sendNavEvent(direction, speed) {
        this.broadcast({
            type: 'nav',
            data: { direction, speed }
        });
    }

    sendVolumeEvent(direction, speed) {
        this.broadcast({
            type: 'volume', 
            data: { direction, speed }
        });
    }

    sendButtonEvent(button) {
        this.broadcast({
            type: 'button',
            data: { button }
        });
    }

    sendLaserEvent(position) {
        this.broadcast({
            type: 'laser',
            data: { position }
        });
    }
}

// Global dummy server instance
let dummyServer = null;

// Trackpad/Mouse wheel simulation for laser pointer
class LaserPointerSimulator {
    constructor(server) {
        this.server = server;
        // Use the same calibration values as the real hardware
        this.MIN_LASER_POS = 3;
        this.MID_LASER_POS = 72;
        this.MAX_LASER_POS = 123;
        this.currentLaserPosition = this.MID_LASER_POS; // Start in middle (180°)
        this.isEnabled = false;
    }

    enable() {
        if (this.isEnabled) return;
        this.isEnabled = true;
        
        console.log('[DUMMY-HW] Enabling trackpad/mouse wheel for laser pointer control');
        
        // Add wheel event listener
        document.addEventListener('wheel', this.handleWheelEvent.bind(this), { passive: false });
    }

    disable() {
        if (!this.isEnabled) return;
        this.isEnabled = false;
        
        console.log('[DUMMY-HW] Disabling trackpad/mouse wheel control');
        document.removeEventListener('wheel', this.handleWheelEvent.bind(this));
    }

    handleWheelEvent(event) {
        if (!this.isEnabled || !this.server.isRunning) {
            console.log(`[DUMMY-HW] Wheel event ignored - enabled: ${this.isEnabled}, server running: ${this.server.isRunning}`);
            return;
        }
        
        console.log(`[DUMMY-HW] Wheel event received - deltaY: ${event.deltaY}`);
        
        try {
            // Prevent default scrolling behavior
            event.preventDefault();
            
            // Only process significant movements to reduce noise
            const MIN_DELTA_THRESHOLD = 1; // More sensitive
            if (Math.abs(event.deltaY) < MIN_DELTA_THRESHOLD) {
                console.log(`[DUMMY-HW] Wheel delta too small: ${event.deltaY}`);
                return;
            }
            
            // Calculate position change from wheel delta
            const sensitivity = 0.4; // Much more responsive
            const deltaPosition = event.deltaY * sensitivity;
            
            console.log(`[DUMMY-HW] Processing wheel: deltaY=${event.deltaY}, deltaPosition=${deltaPosition}, currentPos=${this.currentLaserPosition}`);
            
            // Update laser position with correct bounds (3-123, same as real hardware)
            const newPosition = Math.max(this.MIN_LASER_POS, Math.min(this.MAX_LASER_POS, this.currentLaserPosition + deltaPosition));
            this.currentLaserPosition = newPosition;
            
            // Convert position to angle for logging using the same calibration as real hardware
            let angle;
            const pos = this.currentLaserPosition;
            
            if (pos <= this.MIN_LASER_POS) {
                angle = 150;
            } else if (pos < this.MID_LASER_POS) {
                const slope = (180 - 150) / (this.MID_LASER_POS - this.MIN_LASER_POS);
                angle = 150 + slope * (pos - this.MIN_LASER_POS);
            } else if (pos <= this.MAX_LASER_POS) {
                const slope = (210 - 180) / (this.MAX_LASER_POS - this.MID_LASER_POS);
                angle = 180 + slope * (pos - this.MID_LASER_POS);
            } else {
                angle = 210;
            }
            
            console.log(`[DUMMY-HW] New laser position: ${Math.round(this.currentLaserPosition)} (${angle.toFixed(1)}°)`);
            
            // Send laser event
            this.server.sendLaserEvent(Math.round(this.currentLaserPosition));
            
        } catch (error) {
            console.error('[DUMMY-HW] Error in wheel handler:', error);
        }
    }
}

// Keyboard simulation for hardware buttons and wheels
class KeyboardSimulator {
    constructor(server) {
        this.server = server;
        this.isEnabled = false;
    }

    enable() {
        if (this.isEnabled) return;
        this.isEnabled = true;
        
        console.log('[DUMMY-HW] Enabling keyboard simulation for hardware controls');
        
        // Add keyboard event listener
        document.addEventListener('keydown', this.handleKeyDown.bind(this));
    }

    disable() {
        if (!this.isEnabled) return;
        this.isEnabled = false;
        
        console.log('[DUMMY-HW] Disabling keyboard simulation');
        document.removeEventListener('keydown', this.handleKeyDown.bind(this));
    }

    handleKeyDown(event) {
        if (!this.isEnabled || !this.server.isRunning) return;
        
        try {
            // Only handle if no input elements are focused
            if (document.activeElement.tagName === 'INPUT' || 
                document.activeElement.tagName === 'TEXTAREA') {
                return;
            }
            
            let handled = false;
            
            // Map keyboard keys to hardware events
            switch(event.key) {
                case 'ArrowLeft':
                    console.log('[DUMMY-HW] Left arrow -> left button');
                    this.server.sendButtonEvent('left');
                    handled = true;
                    break;
                    
                case 'ArrowRight':
                    console.log('[DUMMY-HW] Right arrow -> right button');
                    this.server.sendButtonEvent('right');
                    handled = true;
                    break;
                    
                case 'Enter':
                    console.log('[DUMMY-HW] Enter -> go button');
                    this.server.sendButtonEvent('go');
                    handled = true;
                    break;
                    
                case ' ': // Space bar as alternative go button
                    console.log('[DUMMY-HW] Space -> go button');
                    this.server.sendButtonEvent('go');
                    handled = true;
                    break;
                    
                case 'ArrowUp':
                    console.log('[DUMMY-HW] Up arrow -> navigation wheel counter-clockwise');
                    this.server.sendNavEvent('counter', 20);
                    handled = true;
                    break;
                    
                case 'ArrowDown':
                    console.log('[DUMMY-HW] Down arrow -> navigation wheel clockwise');
                    this.server.sendNavEvent('clock', 20);
                    handled = true;
                    break;
                    
                case 'PageUp':
                case '+':
                case '=':
                    console.log('[DUMMY-HW] PageUp/+ -> volume up');
                    this.server.sendVolumeEvent('clock', 20);
                    handled = true;
                    break;
                    
                case 'PageDown':
                case '-':
                case '_':
                    console.log('[DUMMY-HW] PageDown/- -> volume down');
                    this.server.sendVolumeEvent('counter', 20);
                    handled = true;
                    break;
                    
                case 'Escape':
                    console.log('[DUMMY-HW] Escape -> power button');
                    this.server.sendButtonEvent('power');
                    handled = true;
                    break;
            }
            
            if (handled) {
                event.preventDefault();
                event.stopPropagation();
            }
        } catch (error) {
            console.error('[DUMMY-HW] Error in keyboard handler:', error);
        }
    }
}

// Main dummy hardware manager
class DummyHardwareManager {
    constructor() {
        this.server = null;
        this.laserSimulator = null;
        this.keyboardSimulator = null;
        this.isActive = false;
    }

    start() {
        if (this.isActive) {
            console.log('[DUMMY-HW] Already active');
            return this.server;
        }

        console.log('[DUMMY-HW] Starting dummy hardware simulation');
        
        // Create server
        this.server = new DummyWebSocketServer();
        this.server.start();
        
        // Create simulators
        this.laserSimulator = new LaserPointerSimulator(this.server);
        this.keyboardSimulator = new KeyboardSimulator(this.server);
        
        console.log('[DUMMY-HW] Created simulators, enabling...');
        
        // Enable simulators
        this.laserSimulator.enable();
        this.keyboardSimulator.enable();
        
        this.isActive = true;
        
        console.log('[DUMMY-HW] Dummy hardware ready - keyboard/trackpad will simulate real hardware');
        console.log(`[DUMMY-HW] Laser simulator enabled: ${this.laserSimulator.isEnabled}`);
        console.log(`[DUMMY-HW] Keyboard simulator enabled: ${this.keyboardSimulator.isEnabled}`);
        console.log(`[DUMMY-HW] Server running: ${this.server.isRunning}`);
        
        return this.server;
    }

    stop() {
        if (!this.isActive) return;
        
        console.log('[DUMMY-HW] Stopping dummy hardware simulation');
        
        // Disable simulators
        if (this.laserSimulator) {
            this.laserSimulator.disable();
        }
        if (this.keyboardSimulator) {
            this.keyboardSimulator.disable();
        }
        
        // Stop server
        if (this.server) {
            this.server.stop();
        }
        
        this.isActive = false;
        console.log('[DUMMY-HW] Dummy hardware stopped');
    }

    getServer() {
        return this.server;
    }
}

// Global manager instance
const dummyHardwareManager = new DummyHardwareManager();

// Export for use by cursor-handler.js
window.DummyHardwareManager = DummyHardwareManager;
window.dummyHardwareManager = dummyHardwareManager;

console.log('[DUMMY-HW] Dummy hardware module loaded'); 