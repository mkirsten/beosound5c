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
        // Only log non-laser events to reduce spam
        if (message.type !== 'laser') {
            console.log(`[DUMMY-HW] Broadcasting: ${messageStr}`);
        }
        
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
        // Use centralized constants with fallbacks
        const laser = window.Constants?.laser || {};
        this.MIN_LASER_POS = laser.minPosition || 3;
        this.MID_LASER_POS = laser.midPosition || 72;
        this.MAX_LASER_POS = laser.maxPosition || 123;
        this.currentLaserPosition = laser.defaultPosition || 93;
        this.isEnabled = false;
        this._boundWheelHandler = this.handleWheelEvent.bind(this);
    }

    enable() {
        if (this.isEnabled) return;
        this.isEnabled = true;

        // Add wheel event listener
        document.addEventListener('wheel', this._boundWheelHandler, { passive: false });
        
        // Send initial laser position to set the starting view
        setTimeout(() => {
            const roundedPosition = Math.round(this.currentLaserPosition);
            this.server.sendLaserEvent(roundedPosition);
            
            // Debug logging for initial position
            if (window.LaserPositionMapper) {
                const result = window.LaserPositionMapper.resolveMenuSelection(roundedPosition);
                console.log(`[DUMMY-HW] Initial: position ${roundedPosition} -> ${result.path} (idx ${result.selectedIndex})`);
            }
        }, 100);
    }

    disable() {
        if (!this.isEnabled) return;
        this.isEnabled = false;

        document.removeEventListener('wheel', this._boundWheelHandler);
    }

    handleWheelEvent(event) {
        if (!this.isEnabled || !this.server.isRunning) {
            return;
        }
        
        // Don't intercept wheel events when a webpage iframe is active
        if (event.target.closest('.webpage-iframe')) {
            return; // Let the iframe handle its own scroll events
        }
        
        try {
            // Prevent default scrolling behavior
            event.preventDefault();
            
            // Only process significant movements to reduce noise
            const MIN_DELTA_THRESHOLD = 1; // More sensitive
            if (Math.abs(event.deltaY) < MIN_DELTA_THRESHOLD) {
                return;
            }
            
            // Calculate position change from wheel delta
            const sensitivity = 0.4; // Much more responsive
            const deltaPosition = event.deltaY * sensitivity;
            
            // Update laser position with correct bounds (3-123, same as real hardware)
            const newPosition = Math.max(this.MIN_LASER_POS, Math.min(this.MAX_LASER_POS, this.currentLaserPosition + deltaPosition));
            
            // Only send if position actually changed
            if (Math.abs(newPosition - this.currentLaserPosition) > 0.5) {
                this.currentLaserPosition = newPosition;
                const roundedPosition = Math.round(this.currentLaserPosition);
                
                // Send laser event
                this.server.sendLaserEvent(roundedPosition);
                
                // Debug logging (can be removed later)
                if (window.LaserPositionMapper) {
                    const result = window.LaserPositionMapper.resolveMenuSelection(roundedPosition);
                    console.log(`[DUMMY-HW] Scroll: position ${roundedPosition} -> ${result.path} (idx ${result.selectedIndex})`);
                }
            }
            
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
        this._boundKeyDown = this.handleKeyDown.bind(this);
    }

    enable() {
        if (this.isEnabled) return;
        this.isEnabled = true;

        // Add keyboard event listener
        document.addEventListener('keydown', this._boundKeyDown);
    }

    disable() {
        if (!this.isEnabled) return;
        this.isEnabled = false;

        document.removeEventListener('keydown', this._boundKeyDown);
    }

    handleKeyDown(event) {
        if (!this.isEnabled || !this.server.isRunning) return;
        
        try {
            // Only handle if no input elements or iframes are focused
            if (document.activeElement.tagName === 'INPUT' || 
                document.activeElement.tagName === 'TEXTAREA' ||
                document.activeElement.tagName === 'IFRAME') {
                return;
            }
            
            let handled = false;
            
            // Map keyboard keys to hardware events
            switch(event.key) {
                case 'ArrowLeft':
                    this.server.sendButtonEvent('left');
                    handled = true;
                    break;
                    
                case 'ArrowRight':
                    this.server.sendButtonEvent('right');
                    handled = true;
                    break;
                    
                case 'Enter':
                    this.server.sendButtonEvent('go');
                    handled = true;
                    break;
                    
                case ' ': // Space bar as alternative go button
                    this.server.sendButtonEvent('go');
                    handled = true;
                    break;
                    
                case 'ArrowUp':
                    this.server.sendNavEvent('counter', 20);
                    handled = true;
                    break;
                    
                case 'ArrowDown':
                    this.server.sendNavEvent('clock', 20);
                    handled = true;
                    break;
                    
                case 'PageUp':
                case '+':
                case '=':
                    this.server.sendVolumeEvent('clock', 20);
                    handled = true;
                    break;
                    
                case 'PageDown':
                case '-':
                case '_':
                    this.server.sendVolumeEvent('counter', 20);
                    handled = true;
                    break;
                    
                case 'Escape':
                    this.server.sendButtonEvent('power');
                    handled = true;
                    break;

                case 'k':
                case 'K':
                    // Test trigger for camera overlay
                    if (window.CameraOverlayManager) {
                        window.CameraOverlayManager.show();
                        console.log('[DUMMY-HW] Camera overlay triggered via "k" key');
                    }
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
            return this.server;
        }

        console.log('[DUMMY-HW] Starting dummy hardware simulation');
        
        // Create server
        this.server = new DummyWebSocketServer();
        this.server.start();
        
        // Create simulators
        this.laserSimulator = new LaserPointerSimulator(this.server);
        this.keyboardSimulator = new KeyboardSimulator(this.server);
        
        // Enable simulators
        this.laserSimulator.enable();
        this.keyboardSimulator.enable();
        
        this.isActive = true;
        
        console.log('[DUMMY-HW] Dummy hardware ready - keyboard/trackpad active');
        
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

// Export for use by ws-dispatcher.js
window.DummyHardwareManager = DummyHardwareManager;
window.dummyHardwareManager = dummyHardwareManager;

// Dummy hardware module loaded 