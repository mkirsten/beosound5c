/**
 * ArcList - Interactive Scrollable Gallery
 * 
 * This class creates a smooth scrolling arc-based list of 100 items.
 * Users can navigate with arrow keys, and items are positioned in an arc formation.
 * The center item is highlighted and larger, with items fading and blurring towards the edges.
 */
class ArcList {
    constructor() {
        // ===== CONFIGURATION PARAMETERS =====
        this.SCROLL_SPEED = 0.15; // How fast scrolling animation happens (0.1 = slow, 0.3 = fast)
        this.SCROLL_STEP = 0.333; // How much to scroll per key press (changed from 0.2 to 1 for better navigation)
        this.SNAP_DELAY = 1000; // Milliseconds to wait before snapping to closest item (reduced from 1000)
        this.VISIBLE_ITEMS = 9; // How many items to show at once
        this.MIDDLE_INDEX = Math.floor(this.VISIBLE_ITEMS / 2); // Index of the center item
        
        // ===== STATE VARIABLES =====
        this.currentIndex = 0; // Current smooth scroll position (can be decimal)
        this.targetIndex = 0; // Where we want to scroll to
        this.lastScrollTime = 0; // When user last pressed a key (for auto-snap)
        this.animationFrame = null; // Reference to current animation frame
        
        // ===== DOM ELEMENTS =====
        this.container = document.getElementById('arc-container'); // Main container for items
        this.currentItemDisplay = document.getElementById('current-item'); // Counter display
        this.totalItemsDisplay = document.getElementById('total-items'); // Total count display
        
        // ===== INITIALIZE =====
        this.items = []; // Will be loaded asynchronously
        this.init();
    }
    
    /**
     * Load playlist data from playlists_with_tracks.json
     * Each playlist has: name, id, url, image, and tracks array
     */
    async loadPlaylists() {
        try {
            console.log('Attempting to load playlists from ../playlists_with_tracks.json');
            const response = await fetch('../playlists_with_tracks.json');
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const data = await response.json();
            console.log('Successfully loaded playlists:', data.length, 'playlists found');
            
            // Transform playlist data into the format we need
            const transformedData = data.map(playlist => {
                console.log('Processing playlist:', playlist.name, 'with image:', playlist.image);
                
                // Try to get a better image - use first track's image if available
                let bestImage = playlist.image;
                if (playlist.tracks && playlist.tracks.length > 0 && playlist.tracks[0].image) {
                    bestImage = playlist.tracks[0].image;
                    console.log('Using track image instead of playlist mosaic:', bestImage);
                }
                
                return {
                    id: playlist.id,
                    name: playlist.name,
                    image: bestImage || 'data:image/svg+xml,%3Csvg width="128" height="128" xmlns="http://www.w3.org/2000/svg"%3E%3Crect width="128" height="128" fill="%23333"/%3E%3Ctext x="64" y="64" text-anchor="middle" dy=".3em" fill="white" font-size="16"%3E♪%3C/text%3E%3C/svg%3E'
                };
            });
            
            console.log('Transformed playlists:', transformedData);
            return transformedData;
        } catch (error) {
            console.error('Error loading playlists:', error);
            // Fallback to a few dummy items if loading fails
            return [
                { id: '1', name: 'Error Loading Playlists', image: 'data:image/svg+xml,%3Csvg width="128" height="128" xmlns="http://www.w3.org/2000/svg"%3E%3Crect width="128" height="128" fill="%23ff0000"/%3E%3Ctext x="64" y="64" text-anchor="middle" dy=".3em" fill="white" font-size="16"%3E!%3C/text%3E%3C/svg%3E' }
            ];
        }
    }

    /**
     * Generate 100 random items with names and images
     * Each item has: id, name, and image URL
     * Using reliable placeholder images from picsum.photos
     */
    generateItems() {
        // This method is now replaced by loadPlaylists()
        // Keeping it for reference but it won't be used
        const adjectives = ['Amazing', 'Brilliant', 'Creative', 'Dynamic', 'Epic', 'Fantastic', 'Glorious', 'Incredible', 'Luminous', 'Majestic'];
        const nouns = ['Galaxy', 'Phoenix', 'Thunder', 'Crystal', 'Shadow', 'Flame', 'Storm', 'Ocean', 'Mountain', 'Star'];
        
        return Array.from({ length: 100 }, (_, index) => {
            const name = `${adjectives[Math.floor(Math.random() * adjectives.length)]} ${nouns[Math.floor(Math.random() * nouns.length)]}`;
            const imageId = 100 + index;
            
            return {
                id: Math.floor(Math.random() * 10000000000).toString().padStart(10, '0'),
                name: name.length > 30 ? name.substring(0, 30) : name,
                image: `https://picsum.photos/128/128?random=${imageId}`,
                fallbackImage: `data:image/svg+xml,%3Csvg width='128' height='128' xmlns='http://www.w3.org/2000/svg'%3E%3Crect width='128' height='128' fill='%23${(imageId * 123456).toString(16).slice(-6)}'/%3E%3Ctext x='64' y='64' text-anchor='middle' dy='.3em' fill='white' font-size='16'%3E${index + 1}%3C/text%3E%3C/svg%3E`
            };
        });
    }
    
    /**
     * Initialize the application
     * Sets up event listeners, loads playlists, starts animation loop, updates counter
     */
    async init() {
        console.log('Initializing ArcList...'); // Debug log
        
        // Load playlist data
        this.items = await this.loadPlaylists();
        console.log('Loaded', this.items.length, 'playlists'); // Debug log
        
        this.setupEventListeners(); // Listen for keyboard input
        this.startAnimation(); // Begin the smooth animation loop
        this.updateCounter(); // Show initial counter values
        this.totalItemsDisplay.textContent = this.items.length; // Set total items display
    }
    
    /**
     * Set up keyboard event listeners and auto-snap functionality
     */
    setupEventListeners() {
        // Listen for arrow key presses
        document.addEventListener('keydown', (e) => this.handleKeyPress(e));
        
        // Initialize auto-snap timer (snaps to closest item after user stops scrolling)
        this.snapTimer = null;
        this.setupSnapTimer();
        
        // Initialize WebSocket connection for navigation wheel events
        this.connectWebSocket();
    }
    
    /**
     * Handle keyboard input for navigation
     * Updates target scroll position and resets snap timer
     */
    handleKeyPress(e) {
        const now = Date.now();
        this.lastScrollTime = now; // Record when user last interacted
        
        console.log('Key pressed:', e.key, 'Current target:', this.targetIndex); // Debug log
        
        if (e.key === 'ArrowUp') {
            // Move up in the list (decrease index)
            this.targetIndex = Math.max(0, this.targetIndex - this.SCROLL_STEP);
            this.setupSnapTimer(); // Reset auto-snap timer
            console.log('Moving up to:', this.targetIndex); // Debug log
        } else if (e.key === 'ArrowDown') {
            // Move down in the list (increase index)
            this.targetIndex = Math.min(this.items.length - 1, this.targetIndex + this.SCROLL_STEP);
            this.setupSnapTimer(); // Reset auto-snap timer
            console.log('Moving down to:', this.targetIndex); // Debug log
        }
    }
    
    /**
     * Set up timer that automatically snaps to the closest item
     * This prevents the list from stopping between items
     */
    setupSnapTimer() {
        // Clear existing timer if any
        if (this.snapTimer) {
            clearTimeout(this.snapTimer);
        }
        
        // Set new timer to snap after delay
        this.snapTimer = setTimeout(() => {
            // Only snap if enough time has passed since last user input
            if (Date.now() - this.lastScrollTime >= this.SNAP_DELAY) {
                const closestIndex = Math.round(this.targetIndex); // Find closest whole number
                const clampedIndex = Math.max(0, Math.min(this.items.length - 1, closestIndex)); // Keep within bounds
                this.targetIndex = clampedIndex; // Snap to that position
                console.log('Snapping to:', clampedIndex); // Debug log
            }
        }, this.SNAP_DELAY);
    }
    
    /**
     * Start the main animation loop
     * This runs continuously and smoothly moves items to their target positions
     */
    startAnimation() {
        const animate = () => {
            // Smooth interpolation between current and target position
            const diff = this.targetIndex - this.currentIndex;
            if (Math.abs(diff) < 0.01) {
                // Close enough - just snap to target
                this.currentIndex = this.targetIndex;
            } else {
                // Move smoothly towards target
                this.currentIndex += diff * this.SCROLL_SPEED;
            }
            
            // Update the display
            this.render(); // Position all visible items
            this.updateCounter(); // Update the counter display
            
            // Schedule next frame
            this.animationFrame = requestAnimationFrame(animate);
        };
        
        animate(); // Start the loop
    }
    
    /**
     * Calculate which items should be visible and their positions/properties
     * Returns array of items with their visual properties (position, scale, opacity, etc.)
     */
    getVisibleItems() {
        const visibleItems = [];
        
        // Calculate the range of items to show (centered around currentIndex)
        const centerIndex = Math.round(this.currentIndex);
        
        // Show items from -MIDDLE_INDEX to +MIDDLE_INDEX relative to center
        for (let relativePos = -this.MIDDLE_INDEX; relativePos <= this.MIDDLE_INDEX; relativePos++) {
            const itemIndex = centerIndex + relativePos;
            
            // Skip if item doesn't exist in our data
            if (itemIndex < 0 || itemIndex >= this.items.length) {
                continue;
            }
            
            // Calculate the actual relative position considering smooth scrolling
            const actualRelativePos = relativePos - (this.currentIndex - centerIndex);
            const absPosition = Math.abs(actualRelativePos);
            
            // ===== VISUAL EFFECTS =====
            const scale = Math.max(0.4, 1.0 - (absPosition * 0.15)); // Calculate scale first
            const opacity = Math.max(0.4, 1 - absPosition * 0.15); // Center item is fully visible, edges fade out
            const blur = 0; // No blur for now
            
            // ===== ARC POSITIONING CALCULATIONS =====
            // 🎯 ARC SHAPE CONTROL - Adjust these values to change the arc appearance:
            const maxRadius = 220; // Horizontal offset for spacing (higher = more spread out)
            const horizontalMultiplier = 0.35; // How much items curve to the right (0.1 = straight, 0.5 = very curved)
            const baseXOffset = 120; // 🎯 BASE X POSITION - Move entire arc left/right (higher = more to the right)
            const x = baseXOffset + (Math.abs(actualRelativePos) * maxRadius * horizontalMultiplier); // Horizontal spacing multiplier
            
            // 🎯 VERTICAL SPACING CONTROL - Adjust these values to change vertical spacing:
            const baseItemSize = 128; // Base size in pixels
            const scaledItemSize = baseItemSize * scale; // Actual size after scaling
            const minSpacing = scaledItemSize + 20; // Add 20px padding between items
            const y = actualRelativePos * minSpacing; // Dynamic spacing based on scale
            
            // Add item to visible list with all its properties
            visibleItems.push({
                ...this.items[itemIndex], // Include original item data (id, name, image)
                index: itemIndex, // Original index in the items array
                relativePosition: actualRelativePos, // Position relative to center
                x, // Horizontal position
                y, // Vertical position
                scale, // Size multiplier
                opacity, // Transparency
                blur, // Blur amount
                isSelected: Math.abs(actualRelativePos) < 0.5 // Is this the center/selected item?
            });
        }
        
        // Sort by relative position to ensure consistent order
        visibleItems.sort((a, b) => a.relativePosition - b.relativePosition);
        
        return visibleItems;
    }
    
    /**
     * Create and configure an image element for an item
     * Handles loading states and fallbacks properly
     */
    createImageElement(item) {
        const img = document.createElement('img');
        img.className = 'item-image';
        img.alt = item.name;
        img.loading = 'lazy';
        
        // Add unique data attribute to prevent caching issues
        img.dataset.itemId = item.id;
        
        console.log('Creating image for item:', item.name, 'with src:', item.image);
        
        // Handle image loading
        img.onload = () => {
            console.log('✅ Image loaded successfully for:', item.name);
            img.removeAttribute('data-loading');
        };
        
        img.onerror = () => {
            console.error('❌ Image failed to load for:', item.name, 'src:', item.image);
            
            // Try to create a better fallback based on the item name
            const fallbackColor = this.getColorFromName(item.name);
            const fallbackText = item.name.substring(0, 2).toUpperCase();
            
            // Create a more interesting fallback with the item's name
            const fallbackSvg = `data:image/svg+xml,%3Csvg width='128' height='128' xmlns='http://www.w3.org/2000/svg'%3E%3Crect width='128' height='128' fill='%23${fallbackColor}'/%3E%3Ctext x='64' y='64' text-anchor='middle' dy='.3em' fill='white' font-size='20' font-family='Arial, sans-serif'%3E${fallbackText}%3C/text%3E%3C/svg%3E`;
            
            console.log('🔄 Using fallback image for:', item.name, 'with color:', fallbackColor, 'text:', fallbackText);
            console.log('🔄 Fallback SVG URL:', fallbackSvg.substring(0, 100) + '...');
            
            // Test with a simple known-working image first
            if (item.name.includes('test')) {
                img.src = 'data:image/svg+xml,%3Csvg width="128" height="128" xmlns="http://www.w3.org/2000/svg"%3E%3Crect width="128" height="128" fill="%23ff0000"/%3E%3Ctext x="64" y="64" text-anchor="middle" dy=".3em" fill="white" font-size="20"%3ETEST%3C/text%3E%3C/svg%3E';
            } else {
                img.src = fallbackSvg;
            }
        };
        
        img.setAttribute('data-loading', 'true');
        console.log('🔄 Setting image src to:', item.image);
        img.src = item.image;
        
        // TEMPORARY: Make image more visible for debugging
        img.style.border = '2px solid red';
        img.style.backgroundColor = 'rgba(255, 0, 0, 0.3)';
        img.style.zIndex = '1000';
        img.style.position = 'relative';
        img.style.width = '128px';
        img.style.height = '128px';
        img.style.display = 'block';
        img.style.objectFit = 'cover';
        img.style.visibility = 'visible';
        img.style.opacity = '1';
        img.style.maxWidth = 'none';
        img.style.maxHeight = 'none';
        img.style.minWidth = '128px';
        img.style.minHeight = '128px';
        img.style.overflow = 'visible';
        img.style.clip = 'auto';
        img.style.clipPath = 'none';
        
        return img;
    }
    
    /**
     * Generate a consistent color from a string (for fallback images)
     */
    getColorFromName(name) {
        // Simple hash function to generate consistent colors
        let hash = 0;
        for (let i = 0; i < name.length; i++) {
            hash = name.charCodeAt(i) + ((hash << 5) - hash);
        }
        
        // Convert to hex color (avoiding too light or too dark colors)
        const hue = Math.abs(hash) % 360;
        const saturation = 60 + (Math.abs(hash) % 20); // 60-80%
        const lightness = 40 + (Math.abs(hash) % 20); // 40-60%
        
        // Convert HSL to hex (simplified)
        const colors = [
            '4A90E2', '50C878', 'FF6B6B', 'FFD93D', '6C5CE7',
            'A8E6CF', 'FF8B94', 'FFD3B6', 'FFAAA5', 'DCEDC8',
            'FFEAA7', 'DDA0DD', '98D8C8', 'F7DC6F', 'BB8FCE'
        ];
        
        return colors[Math.abs(hash) % colors.length];
    }
    
    /**
     * Render all visible items to the screen
     * This is called every animation frame to update positions and visibility
     */
    render() {
        // Clear the container completely to prevent element reuse issues
        this.container.innerHTML = '';
        
        const visibleItems = this.getVisibleItems();
        
        // Create fresh DOM elements for each visible item
        visibleItems.forEach((item, index) => {
            // Create main container for this item - EXACTLY like music.html
            const itemElement = document.createElement('div');
            itemElement.className = 'arc-item';
            itemElement.dataset.itemId = item.id; // Add unique identifier
            
            // Add selected class if this is the center item
            if (Math.abs(item.index - this.currentIndex) < 0.5) {
                itemElement.classList.add('selected');
            }
            
            // Create and configure the image - EXACTLY like music.html
            const imageContainer = document.createElement('div');
            imageContainer.className = 'item-image-container';
            if (itemElement.classList.contains('selected')) {
                imageContainer.classList.add('selected');
            }
            
            // Create image EXACTLY like music.html
            const nameEl = document.createElement('div');
            nameEl.className = 'item-name';
            nameEl.textContent = item.name;
            
            const imgEl = document.createElement('img');
            imgEl.className = 'item-image';
            imgEl.src = item.image;
            imgEl.loading = 'lazy';
            
            // Apply positioning and visual effects
            itemElement.style.transform = `translate(${item.x}px, ${item.y}px) scale(${item.scale})`;
            itemElement.style.opacity = item.opacity;
            itemElement.style.filter = `blur(${item.blur}px)`;
            
            // Add elements to the item container - EXACTLY like music.html
            itemElement.appendChild(nameEl);
            itemElement.appendChild(imgEl);
            
            // Add item to the main container
            this.container.appendChild(itemElement);
        });
    }
    
    /**
     * Update the counter display (current item number)
     */
    updateCounter() {
        // Show current item number (1-based instead of 0-based)
        const displayIndex = Math.floor(this.currentIndex) + 1;
        this.currentItemDisplay.textContent = displayIndex;
        console.log('Counter updated:', displayIndex, '/', this.items.length); // Debug log
    }
    
    /**
     * Clean up resources when the app is destroyed
     * (Currently not used, but good practice)
     */
    destroy() {
        if (this.animationFrame) {
            cancelAnimationFrame(this.animationFrame); // Stop animation loop
        }
        if (this.snapTimer) {
            clearTimeout(this.snapTimer); // Clear auto-snap timer
        }
    }
    
    /**
     * WebSocket connection for navigation wheel events
     */
    connectWebSocket() {
        try {
            this.ws = new WebSocket('ws://localhost:8765');
            
            this.ws.onopen = () => {
                console.log('WebSocket connected');
            };
            
            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleWebSocketMessage(data);
                } catch (error) {
                    console.error('Error parsing WebSocket message:', error);
                }
            };
            
            this.ws.onclose = () => {
                console.log('WebSocket disconnected');
                // Attempt to reconnect after 2 seconds
                setTimeout(() => this.connectWebSocket(), 2000);
            };
            
            this.ws.onerror = (error) => {
                console.error('WebSocket error:', error);
            };
        } catch (error) {
            console.error('Error creating WebSocket:', error);
        }
    }
    
    /**
     * Handle WebSocket messages for navigation wheel events
     */
    handleWebSocketMessage(data) {
        // Log all received WebSocket messages
        console.log('Received WebSocket message:', data);
        
        // Listen for navigation wheel events (not volume or laser)
        if (data.type === 'nav' && data.data) {
            const direction = data.data.direction; // 'clock' or 'counter'
            
            // Check boundaries before scrolling
            const atTop = this.targetIndex <= 0;
            const atBottom = this.targetIndex >= this.items.length - 1;
            const scrollingUp = direction === 'counter';
            const scrollingDown = direction === 'clock';
            
            // Don't scroll if at boundaries
            if ((atTop && scrollingUp) || (atBottom && scrollingDown)) {
                console.log('At boundary - not scrolling');
                return;
            }
            
            // Handle the scroll
            if (scrollingDown) {
                // Scroll down
                this.targetIndex = Math.min(this.items.length - 1, this.targetIndex + this.SCROLL_STEP);
                this.setupSnapTimer(); // Reset auto-snap timer
                console.log('WebSocket: Moving down to:', this.targetIndex);
            } else if (scrollingUp) {
                // Scroll up
                this.targetIndex = Math.max(0, this.targetIndex - this.SCROLL_STEP);
                this.setupSnapTimer(); // Reset auto-snap timer
                console.log('WebSocket: Moving up to:', this.targetIndex);
            }
            
            // Send click command back to server (rate limited)
            this.sendClickCommand();
        }
    }
    
    /**
     * Send click command back to server (rate limited)
     */
    sendClickCommand() {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
        
        const now = Date.now();
        const CLICK_THROTTLE_MS = 50;
        
        // Rate limiting: only send if at least 200ms have passed since last send
        if (now - (this.lastClickTime || 0) < CLICK_THROTTLE_MS) {
            console.log('Click command throttled - too soon');
            return;
        }
        
        this.lastClickTime = now;
        
        const message = {
            type: 'command',
            command: 'click',
            params: {}
        };
        
        this.ws.send(JSON.stringify(message));
        console.log('Sent click command to server');
    }
}

// ===== INITIALIZE THE APPLICATION =====
// Wait for the page to fully load, then create the ArcList
document.addEventListener('DOMContentLoaded', () => {
    console.log('DOM loaded, initializing ArcList...'); // Debug log
    new ArcList();
});