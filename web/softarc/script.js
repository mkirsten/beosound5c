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
            const response = await fetch('../playlists_with_tracks.json');
            const data = await response.json();
            
            // Transform playlist data into the format we need
            return data.map(playlist => ({
                id: playlist.id,
                name: playlist.name,
                image: playlist.image || 'data:image/svg+xml,%3Csvg width="128" height="128" xmlns="http://www.w3.org/2000/svg"%3E%3Crect width="128" height="128" fill="%23333"/%3E%3Ctext x="64" y="64" text-anchor="middle" dy=".3em" fill="white" font-size="16"%3E♪%3C/text%3E%3C/svg%3E'
            }));
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
            // Items curve to the right side of the screen
            const maxRadius = 180; // Horizontal offset for spacing
            const x = Math.abs(actualRelativePos) * maxRadius * 0.4; // Horizontal spacing multiplier
            
            // Dynamic spacing based on item scale
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
        
        // Handle image loading
        img.onload = () => {
            img.removeAttribute('data-loading');
        };
        
        img.onerror = () => {
            // Fallback to a simple placeholder
            img.src = `data:image/svg+xml,%3Csvg width='128' height='128' xmlns='http://www.w3.org/2000/svg'%3E%3Crect width='128' height='128' fill='%23333'/%3E%3Ctext x='64' y='64' text-anchor='middle' dy='.3em' fill='white' font-size='16'%3E♪%3C/text%3E%3C/svg%3E`;
        };
        
        img.setAttribute('data-loading', 'true');
        img.src = item.image;
        
        return img;
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
        visibleItems.forEach(item => {
            // Create main container for this item
            const itemElement = document.createElement('div');
            itemElement.className = 'arc-item';
            itemElement.dataset.itemId = item.id; // Add unique identifier
            
            // Add selected class if this is the center item
            if (Math.abs(item.index - this.currentIndex) < 0.5) {
                itemElement.classList.add('selected');
            }
            
            // Create and configure the image
            const imageContainer = document.createElement('div');
            imageContainer.className = 'item-image-container';
            if (itemElement.classList.contains('selected')) {
                imageContainer.classList.add('selected');
            }
            
            const img = this.createImageElement(item);
            imageContainer.appendChild(img);
            
            // Create overlay for selected items
            const overlay = document.createElement('div');
            overlay.className = 'item-overlay';
            if (itemElement.classList.contains('selected')) {
                overlay.classList.add('selected');
            }
            imageContainer.appendChild(overlay);
            
            // Create and configure the name label
            const nameElement = document.createElement('div');
            nameElement.className = 'item-name';
            nameElement.textContent = item.name;
            if (itemElement.classList.contains('selected')) {
                nameElement.classList.add('selected');
            } else {
                nameElement.classList.add('unselected');
            }
            
            // Apply positioning and visual effects
            itemElement.style.transform = `translate(${item.x}px, ${item.y}px) scale(${item.scale})`;
            itemElement.style.opacity = item.opacity;
            itemElement.style.filter = `blur(${item.blur}px)`;
            
            // Add elements to the item container
            itemElement.appendChild(nameElement);
            itemElement.appendChild(imageContainer);
            
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
}

// ===== INITIALIZE THE APPLICATION =====
// Wait for the page to fully load, then create the ArcList
document.addEventListener('DOMContentLoaded', () => {
    console.log('DOM loaded, initializing ArcList...'); // Debug log
    new ArcList();
});
