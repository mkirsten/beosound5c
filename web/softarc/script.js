/**
 * ArcList - Interactive Scrollable Gallery
 * 
 * This class creates a smooth scrolling arc-based list of 100 items.
 * Users can navigate with arrow keys, and items are positioned in an arc formation.
 * The center item is highlighted and larger, with items fading and blurring towards the edges.
 */
class ArcList {
    constructor(config = {}) {
        // ===== CONFIGURATION PARAMETERS =====
        this.config = {
            // Data source configuration
            dataSource: config.dataSource || '../data.json', // URL to JSON data
            dataType: config.dataType || 'generic', // 'generic', 'parent_child', 'custom'
            itemMapper: config.itemMapper || null, // Custom function to map data to items
            
            // View configuration
            viewMode: config.viewMode || 'single', // 'single' or 'hierarchical' (like parent->child)
            parentKey: config.parentKey || 'children', // Key for child items in hierarchical mode
            parentNameKey: config.parentNameKey || 'name', // Key for parent item names
            childNameMapper: config.childNameMapper || null, // Custom function to format child names
            
            // Storage configuration
            storagePrefix: config.storagePrefix || 'arclist', // Prefix for localStorage keys
            
            // WebSocket configuration
            webSocketUrl: config.webSocketUrl || 'ws://localhost:8765',
            webhookUrl: config.webhookUrl || 'http://homeassistant.local:8123/api/webhook/beosound5c',
            
            // UI configuration
            title: config.title || 'Arc List',
            context: config.context || 'music',
            
            // Default values
            ...config
        };
        
        // ===== ANIMATION PARAMETERS =====
        this.SCROLL_SPEED = 0.5; // How fast scrolling animation happens (0.1 = slow, 0.3 = fast)
        this.SCROLL_STEP = 0.5; // How much to scroll per key press (changed from 0.2 to 1 for better navigation)
        this.SNAP_DELAY = 1000; // Milliseconds to wait before snapping to closest item (reduced from 1000)
        this.MIDDLE_INDEX = 4; // How many items to show on each side of center (4 = 9 total items visible)
        
        // ===== STATE VARIABLES =====
        this.items = []; // Current items to display
        this.currentIndex = 0; // Current center item index (can be fractional for smooth scrolling)
        this.targetIndex = 0; // Target index we're scrolling towards
        this.lastScrollTime = 0; // When user last pressed a key (for auto-snap)
        this.animationFrame = null; // Reference to current animation frame
        this.previousIndex = null; // Store previous center index
        this.lastClickedItemId = null; // Track the last item that was clicked
        
        // ===== POSITION MEMORY =====
        this.STORAGE_KEY_PARENT = `${this.config.storagePrefix}_parent_position`;
        this.STORAGE_KEY_CHILD = `${this.config.storagePrefix}_child_position`;
        this.STORAGE_KEY_VIEW_MODE = `${this.config.storagePrefix}_view_mode`;
        this.STORAGE_KEY_SELECTED_PARENT = `${this.config.storagePrefix}_selected_parent`;
        
        // State management for hierarchical view
        this.viewMode = this.config.viewMode === 'hierarchical' ? 'parent' : 'single';
        this.selectedParent = null;
        this.parentData = []; // Store full data with children
        this.savedParentIndex = 0; // Remember position when viewing children
        
        // Animation state
        this.isAnimating = false; // Prevent render loop from interfering with animations
        
        // ===== DOM ELEMENTS =====
        this.container = document.getElementById('arc-container'); // Main container for items
        this.currentItemDisplay = document.getElementById('current-item'); // Counter display
        this.totalItemsDisplay = document.getElementById('total-items'); // Total count display
        
        // Check if required DOM elements exist
        if (!this.container) {
            console.error('Required DOM element "arc-container" not found');
            return;
        }
        if (!this.currentItemDisplay) {
            console.error('Required DOM element "current-item" not found');
            return;
        }
        if (!this.totalItemsDisplay) {
            console.error('Required DOM element "total-items" not found');
            return;
        }
        
        // ===== INITIALIZE =====
        this.init();
    }
    
    /**
     * Initialize the application
     * Sets up event listeners, loads data, starts animation loop, updates counter
     */
    async init() {
        console.log('Initializing ArcList...'); // Debug log
        
        // Validate DOM elements are still available
        if (!this.container || !this.currentItemDisplay || !this.totalItemsDisplay) {
            console.error('Required DOM elements not available during initialization');
            return;
        }
        
        // Load data
        console.log('🔍 [INIT] About to load data from:', this.config.dataSource);
        await this.loadData();
        console.log('🔍 [INIT] Loaded', this.items.length, 'items from', this.config.dataSource);
        console.log('🔍 [INIT] Items preview:', this.items.slice(0, 3).map(item => item.name));
        
        // Restore saved position and view mode
        this.restoreState();
        
        this.setupEventListeners(); // Listen for keyboard input
        this.startAnimation(); // Begin the smooth animation loop
        this.updateCounter(); // Show initial counter values
        this.totalItemsDisplay.textContent = this.items.length; // Set total items display
    }
    
    /**
     * Load data from data source
     * Each parent item can contain child items in hierarchical mode
     */
    async loadData() {
        try {
            const response = await fetch(this.config.dataSource);
            this.parentData = await response.json();
            
            // Convert data to our items format based on configuration
            if (this.config.itemMapper) {
                // Use custom mapper function
                this.items = this.config.itemMapper(this.parentData);
            } else if (this.config.dataType === 'parent_child') {
                // Default parent/child format - preserve child data for hierarchical navigation
                this.items = this.parentData.map((parent, index) => ({
                    id: parent.id,
                    name: parent[this.config.parentNameKey] || `Item ${index + 1}`,
                    image: parent.image || 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjQiIGhlaWdodD0iNjQiIHZpZXdCb3g9IjAgMCA2NCA2NCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiBmaWxsPSIjMzMzMzMzIi8+Cjx0ZXh0IHg9IjMyIiB5PSI0MCIgZm9udC1mYW1pbHk9IkFyaWFsLCBzYW5zLXNlcmlmIiBmb250LXNpemU9IjI0IiBmaWxsPSIjZmZmZmZmIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj7imqo8L3RleHQ+Cjwvc3ZnPgo=',
                    [this.config.parentKey]: parent[this.config.parentKey] // Preserve child data
                }));
            } else if (this.config.dataType === 'custom') {
                // Assume data is already in the correct format
                this.items = this.parentData;
            } else {
                // Generic fallback
                this.items = this.parentData.map((item, index) => ({
                    id: item.id || `item-${index}`,
                    name: item.name || item.title || `Item ${index + 1}`,
                    image: item.image || item.thumbnail || 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjQiIGhlaWdodD0iNjQiIHZpZXdCb3g9IjAgMCA2NCA2NCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiBmaWxsPSIjMzMzMzMzIi8+Cjx0ZXh0IHg9IjMyIiB5PSI0MCIgZm9udC1mYW1pbHk9IkFyaWFsLCBzYW5zLXNlcmlmIiBmb250LXNpemU9IjI0IiBmaWxsPSIjZmZmZmZmIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj7imqo8L3RleHQ+Cjwvc3ZnPgo='
                }));
            }
            
            console.log('Loaded', this.items.length, 'items from', this.config.dataSource);
        } catch (error) {
            console.error('Error loading data:', error);
            // Fallback to dummy data if loading fails
            this.items = [
                { id: 'fallback-1', name: 'Error Loading Data', image: 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjQiIGhlaWdodD0iNjQiIHZpZXdCb3g9IjAgMCA2NCA2NCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiBmaWxsPSIjZmYwMDAwIi8+Cjx0ZXh0IHg9IjMyIiB5PSI0MCIgZm9udC1mYW1pbHk9IkFyaWFsLCBzYW5zLXNlcmlmIiBmb250LXNpemU9IjI0IiBmaWxsPSIjZmZmZmZmIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj4hPC90ZXh0Pgo8L3N2Zz4K' }
            ];
        }
    }
    
    /**
     * Save current state to localStorage
     */
    saveState() {
        try {
            localStorage.setItem(this.STORAGE_KEY_VIEW_MODE, this.viewMode);
            if (this.viewMode === 'parent') {
                localStorage.setItem(this.STORAGE_KEY_PARENT, this.currentIndex.toString());
            } else if (this.viewMode === 'child') {
                localStorage.setItem(this.STORAGE_KEY_CHILD, this.currentIndex.toString());
                if (this.selectedParent) {
                    localStorage.setItem(this.STORAGE_KEY_SELECTED_PARENT, JSON.stringify({
                        id: this.selectedParent.id,
                        name: this.selectedParent.name,
                        savedParentIndex: this.savedParentIndex
                    }));
                }
            }
            // State saved silently
        } catch (error) {
            console.error('Error saving state:', error);
        }
    }

    /**
     * Restore state from localStorage
     */
    restoreState() {
        try {
            const savedViewMode = localStorage.getItem(this.STORAGE_KEY_VIEW_MODE);
            
            if (savedViewMode === 'child') {
                // Restore child view
                const savedSelectedParent = localStorage.getItem(this.STORAGE_KEY_SELECTED_PARENT);
                const savedChildPosition = localStorage.getItem(this.STORAGE_KEY_CHILD);
                
                if (savedSelectedParent && savedChildPosition) {
                    const parentInfo = JSON.parse(savedSelectedParent);
                    const childIndex = parseFloat(savedChildPosition);
                    
                    // Find the parent in our data
                    const parent = this.parentData.find(p => p.id === parentInfo.id);
                    if (parent) {
                        this.selectedParent = parent;
                        this.savedParentIndex = parentInfo.savedParentIndex || 0;
                        this.viewMode = 'child';
                        
                        // Load children and set position
                        this.loadParentChildrenFromRestore(childIndex);
                        console.log('Restored child view:', parent.name, 'position:', childIndex);
                        return;
                    }
                }
            }
            
            // Restore parent view (default)
            const savedParentPosition = localStorage.getItem(this.STORAGE_KEY_PARENT);
            if (savedParentPosition) {
                const position = parseFloat(savedParentPosition);
                this.currentIndex = Math.max(0, Math.min(this.items.length - 1, position));
                this.targetIndex = this.currentIndex;
                console.log('Restored parent position:', position);
            }
        } catch (error) {
            console.error('Error restoring state:', error);
        }
    }

    /**
     * Load playlist songs when restoring from saved state
     */
    loadParentChildrenFromRestore(childIndex) {
        if (!this.selectedParent || !this.selectedParent[this.config.parentKey]) {
            console.error('No children found for parent during restore');
            return;
        }
        
        // Convert children to items format
        const children = this.selectedParent[this.config.parentKey];
        if (this.config.childNameMapper) {
            this.items = children.map(this.config.childNameMapper);
        } else {
            this.items = children.map(child => ({
                id: child.id,
                name: child.name || child.title || 'Unnamed Item',
                image: child.image || 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjQiIGhlaWdodD0iNjQiIHZpZXdCb3g9IjAgMCA2NCA2NCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiBmaWxsPSIjMzMzMzMzIi8+Cjx0ZXh0IHg9IjMyIiB5PSI0MCIgZm9udC1mYW1pbHk9IkFyaWFsLCBzYW5zLXNlcmlmIiBmb250LXNpemU9IjI0IiBmaWxsPSIjZmZmZmZmIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj7imqo8L3RleHQ+Cjwvc3ZnPgo='
            }));
        }
        
        // Set position
        this.currentIndex = Math.max(0, Math.min(this.items.length - 1, childIndex));
        this.targetIndex = this.currentIndex;
        
        // Update display
        this.totalItemsDisplay.textContent = this.items.length;
        console.log('Loaded', this.items.length, 'children for restore');
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
        
        // Save state periodically and on page unload
        setInterval(() => this.saveState(), 1000); // Save every second
        window.addEventListener('beforeunload', () => this.saveState());
        
        // Listen for events from parent window (when in iframe)
        window.addEventListener('message', (event) => {
            console.log(`🔍 [IFRAME] DEBUG: Message received from parent:`, event.data);
            console.log(`🔍 [IFRAME] DEBUG: Event origin:`, event.origin);
            
            if (event.data && event.data.type === 'button') {
                console.log(`✅ [IFRAME] Button event from parent: ${event.data.button}`);
                this.handleButtonFromParent(event.data.button);
            } else if (event.data && event.data.type === 'nav') {
                console.log(`✅ [IFRAME] Nav event from parent:`, event.data.data);
                this.handleNavFromParent(event.data.data);
            } else if (event.data && event.data.type === 'keyboard') {
                console.log(`✅ [IFRAME] Keyboard event from parent: ${event.data.key}`);
                this.handleKeyboardFromParent(event.data);
            } else {
                console.log(`❌ [IFRAME] Unknown message type or malformed message:`, event.data);
            }
        });
    }
    
    /**
     * Handle keyboard input for navigation
     * Updates target scroll position and resets snap timer
     */
    handleKeyPress(e) {
        console.log(`🎹 [IFRAME] Key press received: ${e.key} (code: ${e.code})`);
        
        const now = Date.now();
        this.lastScrollTime = now; // Record when user last interacted
        
        if (e.key === 'ArrowUp') {
            console.log(`🎹 [IFRAME] ArrowUp: ${this.targetIndex} -> ${Math.max(0, this.targetIndex - this.SCROLL_STEP)}`);
            // Move up in the list (decrease index) - use base scroll step for keyboard
            this.targetIndex = Math.max(0, this.targetIndex - this.SCROLL_STEP);
            this.setupSnapTimer(); // Reset auto-snap timer
        } else if (e.key === 'ArrowDown') {
            console.log(`🎹 [IFRAME] ArrowDown: ${this.targetIndex} -> ${Math.min(this.items.length - 1, this.targetIndex + this.SCROLL_STEP)}`);
            // Move down in the list (increase index) - use base scroll step for keyboard
            this.targetIndex = Math.min(this.items.length - 1, this.targetIndex + this.SCROLL_STEP);
            this.setupSnapTimer(); // Reset auto-snap timer
        } else if (e.key === 'ArrowLeft') {
            // Always send webhook for left button press
            console.log('🎹 [IFRAME] Left arrow pressed - sending webhook');
            this.sendButtonWebhook('left');
            
            // Also handle hierarchical navigation if applicable
            if (this.config.viewMode === 'hierarchical' && this.viewMode === 'parent') {
                console.log('🎹 [IFRAME] Also entering child view (hierarchical mode)');
                this.enterChildView();
            }
        } else if (e.key === 'ArrowRight') {
            // Always send webhook for right button press  
            console.log('🎹 [IFRAME] Right arrow pressed - sending webhook');
            this.sendButtonWebhook('right');
            
            // Also handle hierarchical navigation if applicable
            if (this.config.viewMode === 'hierarchical' && this.viewMode === 'child') {
                console.log('🎹 [IFRAME] Also exiting to parent (hierarchical mode)');
                this.exitChildView();
            }
        } else if (e.key === 'Enter') {
            // Trigger "go" action (same as WebSocket "go" button)
            console.log('🎹 [IFRAME] Enter pressed - sending webhook');
            this.sendGoWebhook();
        } else {
            console.log(`🎹 [IFRAME] Unhandled key: ${e.key}`);
        }
    }
    
    /**
     * Handle button events forwarded from parent window (when in iframe)
     */
    handleButtonFromParent(button) {
        console.log(`🔍 [IFRAME] DEBUG: Processing button from parent:`, button, 'current view mode:', this.viewMode);
        console.log(`🔍 [IFRAME] DEBUG: config.viewMode:`, this.config.viewMode);
        console.log(`🔍 [IFRAME] DEBUG: this.viewMode:`, this.viewMode);
        
        if (button === 'left') {
            // Always send webhook for left button press
            console.log('✅ [IFRAME] Parent button: Left pressed - sending webhook');
            this.sendButtonWebhook('left');
            
            // Also handle hierarchical navigation if applicable
            if (this.config.viewMode === 'hierarchical' && this.viewMode === 'parent') {
                console.log('✅ [IFRAME] Parent button: Also entering child view (hierarchical mode)');
                this.enterChildView();
            } else {
                console.log(`❌ [IFRAME] Conditions not met for enterChildView - viewMode: ${this.viewMode}, config.viewMode: ${this.config.viewMode}`);
            }
        } else if (button === 'right') {
            // Always send webhook for right button press
            console.log('Parent button: Right pressed - sending webhook');
            this.sendButtonWebhook('right');
            
            // Also handle hierarchical navigation if applicable
            if (this.config.viewMode === 'hierarchical' && this.viewMode === 'child') {
                console.log('Parent button: Also exiting to parent (hierarchical mode)');
                this.exitChildView();
            }
        } else if (button === 'go') {
            // Trigger "go" action (same as keyboard Enter)
            console.log('Parent button: Go pressed - sending webhook');
            this.sendGoWebhook();
        }
    }
    
    /**
     * Handle keyboard events forwarded from parent window (when in iframe)
     */
    handleKeyboardFromParent(keyboardData) {
        console.log('Processing keyboard from parent:', keyboardData.key);
        
        // Create a synthetic keyboard event object that matches what handleKeyPress expects
        const syntheticEvent = {
            key: keyboardData.key,
            code: keyboardData.code,
            ctrlKey: keyboardData.ctrlKey,
            shiftKey: keyboardData.shiftKey,
            altKey: keyboardData.altKey,
            metaKey: keyboardData.metaKey,
            preventDefault: () => {}, // Dummy function
            stopPropagation: () => {} // Dummy function
        };
        
        // Call the existing handleKeyPress method
        this.handleKeyPress(syntheticEvent);
    }
    
    /**
     * Handle navigation events forwarded from parent window (when in iframe)
     */
    handleNavFromParent(data) {
        const direction = data.direction; // 'clock' or 'counter'
        const speed = data.speed || 1; // Speed parameter from server
        
        console.log('Processing nav from parent:', direction, 'speed:', speed);
        
        // Calculate scroll step based on speed (same logic as WebSocket handling)
        const speedMultiplier = Math.min(speed / 10, 5); // Cap at 5x speed
        const scrollStep = this.SCROLL_STEP * speedMultiplier;
        
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
        
        // Handle the scroll with speed-based step
        if (scrollingDown) {
            // Scroll down
            this.targetIndex = Math.min(this.items.length - 1, this.targetIndex + scrollStep);
            this.setupSnapTimer(); // Reset auto-snap timer
        } else if (scrollingUp) {
            // Scroll up
            this.targetIndex = Math.max(0, this.targetIndex - scrollStep);
            this.setupSnapTimer(); // Reset auto-snap timer
        }
        
        // If animation isn't running (test environment), update immediately
        if (!this.animationFrame) {
            this.currentIndex = this.targetIndex;
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
                // Snapping to closest item
            }
        }, this.SNAP_DELAY);
    }
    
    /**
     * Start the main animation loop
     * This runs continuously and smoothly moves items to their target positions
     */
    startAnimation() {
        console.log('🔍 [ANIMATION] Starting animation loop with', this.items.length, 'items');
        
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
            
            // Check if selection has changed and trigger click
            this.checkForSelectionClick();
            
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
            const baseXOffset = 100; // 🎯 BASE X POSITION - Move entire arc left/right (higher = more to the right)
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
            const fallbackColor = "4A90E2";
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
        
        return img;
    }
    
    /**
     * Render all visible items to the screen
     * This is called every animation frame to update positions and visibility
     */
    render() {
        console.log('🔍 [RENDER] render() called, isAnimating:', this.isAnimating, 'viewMode:', this.viewMode);
        
        // Don't render if we're in the middle of an animation
        if (this.isAnimating) {
            console.log('🔍 [RENDER] Skipping render due to animation');
            return;
        }
        
        // If we're in child view, preserve the animated parent item
        if (this.viewMode === 'child') {
            // Only render child items, don't clear the animated parent item
            this.renderChildItems();
            return;
        }
        
        // Clear the container completely to prevent element reuse issues
        console.log('🔍 [RENDER] Clearing container, current children:', this.container.children.length);
        // Don't use innerHTML = '' as it's too aggressive, remove children selectively
        const children = Array.from(this.container.children);
        children.forEach(child => {
            // Remove all children except breadcrumbs (in case we're transitioning)
            if (!child.classList.contains('breadcrumb')) {
                child.remove();
            }
        });
        
        const visibleItems = this.getVisibleItems();
        console.log('🔍 [RENDER] Got visibleItems:', visibleItems.length);
        
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
            console.log('🔍 [RENDER] Adding item to container:', item.name);
            this.container.appendChild(itemElement);
        });
        
        console.log('🔍 [RENDER] Finished rendering, container children:', this.container.children.length);
    }
    
    /**
     * Render child items while preserving the animated parent item
     */
    renderChildItems() {
        console.log('🔍 [RENDER-CHILD] Starting renderChildItems');
        
        // Find the breadcrumb element (animated parent) in our container
        const breadcrumb = this.container.querySelector('.arc-item.breadcrumb');
        console.log('🔍 [RENDER-CHILD] Found breadcrumb:', breadcrumb);
        
        // Clear container but preserve breadcrumb
        const children = Array.from(this.container.children);
        children.forEach(child => {
            if (!child.classList.contains('breadcrumb')) {
                child.remove();
            }
        });
        
        const visibleItems = this.getVisibleItems();
        
        // Create fresh DOM elements for each visible child item
        visibleItems.forEach((item, index) => {
            // Create main container for this child item
            const itemElement = document.createElement('div');
            itemElement.className = 'arc-item';
            itemElement.dataset.itemId = item.id;
            itemElement.dataset.childItem = 'true'; // Mark as child item for easy removal
            
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
            
            // Create image
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
            
            // Add elements to the item container
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
        // Removed excessive debug logging that was called 60fps from animation loop
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
            // WebSocket logging control - only log successful connections
            const ENABLE_WEBSOCKET_LOGGING = true;
            
            this.ws = new WebSocket(this.config.webSocketUrl);
            
            const timeout = setTimeout(() => {
                if (this.ws.readyState === WebSocket.CONNECTING) {
                    this.ws.close();
                    this.ws = null;
                }
            }, 2000); // 2 second timeout
            
            this.ws.onopen = () => {
                clearTimeout(timeout);
                if (ENABLE_WEBSOCKET_LOGGING) {
                    console.log('Main server WebSocket connected');
                }
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
                clearTimeout(timeout);
                const wasConnected = this.ws !== null;
                this.ws = null;
                // Only attempt to reconnect if we had a successful connection before
                if (wasConnected) {
                    setTimeout(() => this.connectWebSocket(), 5000);
                }
            };
            
            this.ws.onerror = () => {
                clearTimeout(timeout);
                this.ws = null;
                // Silently fail - main server not available (standalone mode)
            };
        } catch (error) {
            this.ws = null;
            // Silently fail - main server not available (standalone mode)
        }
    }
    
    /**
     * Handle WebSocket messages for navigation wheel events
     */
    handleWebSocketMessage(data) {
        // Log all received WebSocket messages
        console.log('Received WebSocket message:', data);
        
        // Handle button messages for parent selection and back navigation
        if (data.type === 'button' && data.data && data.data.button) {
            const button = data.data.button;
            console.log('Button event received:', button, 'current view mode:', this.viewMode);
            
            if (button === 'left' && this.viewMode === 'parent') {
                console.log('Left button pressed in parent mode - entering child view');
                // Select parent to show children
                this.enterChildView();
                return;
            } else if (button === 'right' && this.viewMode === 'child') {
                console.log('Right button pressed in child mode - exiting to parent');
                // Go back to parent
                this.exitChildView();
                return;
            } else if (button === 'go') {
                console.log('Go button pressed - sending webhook');
                // Send webhook with appropriate ID
                this.sendGoWebhook();
                return;
            } else {
                console.log('Button pressed but no action taken:', button, 'view mode:', this.viewMode);
            }
        }
        
        // Listen for navigation wheel events (not volume or laser)
        if (data.type === 'nav' && data.data) {
            const direction = data.data.direction; // 'clock' or 'counter'
            const speed = data.data.speed || 1; // Speed parameter from server
            
            // Calculate scroll step based on speed
            // Speed ranges from 1-127, convert to scroll step
            const speedMultiplier = Math.min(speed / 10, 5); // Cap at 5x speed
            const scrollStep = this.SCROLL_STEP * speedMultiplier;
            
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
            
            // Handle the scroll with speed-based step
            if (scrollingDown) {
                // Scroll down
                this.targetIndex = Math.min(this.items.length - 1, this.targetIndex + scrollStep);
                this.setupSnapTimer(); // Reset auto-snap timer
                // Removed excessive WebSocket scroll logging
            } else if (scrollingUp) {
                // Scroll up
                this.targetIndex = Math.max(0, this.targetIndex - scrollStep);
                this.setupSnapTimer(); // Reset auto-snap timer
                // Removed excessive WebSocket scroll logging
            }
            
            // Send click command back to server (rate limited)
            //this.sendClickCommand();
        }
    }
    
    /**
     * Send click command back to server (rate limited)
     */
    sendClickCommand() {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
        
        try {
            const now = Date.now();
            const CLICK_THROTTLE_MS = 50; // 50ms throttle
            
            // Rate limiting: only send if at least 50ms have passed since last send
            if (now - (this.lastClickTime || 0) < CLICK_THROTTLE_MS) {
                return;
            }
            
            this.lastClickTime = now;
            
            const message = {
                type: 'command',
                command: 'click',
                params: {}
            };
            
            this.ws.send(JSON.stringify(message));
        } catch (error) {
            // Silently fail if sending fails
        }
    }
    
    /**
     * Check if an item is passing through the selected position and trigger click
     */
    checkForSelectionClick() {
        const centerIndex = Math.round(this.currentIndex);
        const currentItem = this.items[centerIndex];
        
        // Only trigger if we have a valid item and it's different from the last clicked item
        if (currentItem && currentItem.id !== this.lastClickedItemId) {
            // Selection changed - removed excessive logging
            this.sendClickCommand();
            this.lastClickedItemId = currentItem.id;
        }
    }

    /**
     * Enhanced animation orchestration helper methods
     */
    
    /**
     * Orchestrate smooth hierarchy transition animations
     */
    async animateHierarchyTransition(phase, direction = 'enter') {
        const hierarchyBg = document.getElementById('hierarchy-background');
        
        if (phase === 'background') {
            // Activate/deactivate hierarchy background
            if (direction === 'enter') {
                hierarchyBg?.classList.add('active');
            } else {
                hierarchyBg?.classList.remove('active');
            }
            await this.delay(100);
        }
    }
    
    /**
     * Animate parent item transforming to breadcrumb
     */
    async animateParentToChildTransition(selectedElement) {
        console.log('🔍 [BREADCRUMB] animateParentToChildTransition called with element:', selectedElement);
        
        if (!selectedElement) {
            console.log('❌ [BREADCRUMB] No selected element provided');
            return;
        }
        
        console.log('🔍 [BREADCRUMB] Current element classes before:', selectedElement.className);
        console.log('🔍 [BREADCRUMB] Current element transform before:', selectedElement.style.transform);
        
        // Store the current transform to transition from
        const currentTransform = selectedElement.style.transform || 'translate(100px, 0px) scale(1)';
        
        // Remove selected class and add breadcrumb class for smooth transition
        selectedElement.classList.remove('selected');
        selectedElement.classList.add('breadcrumb', 'hierarchy-transition');
        
        // Set up the transition first
        selectedElement.style.transition = 'all 400ms cubic-bezier(0.25, 0.46, 0.45, 0.94)';
        selectedElement.style.zIndex = '10';
        selectedElement.style.pointerEvents = 'auto';
        
        // Clear inline transform and other arc positioning styles to allow CSS to take over
        selectedElement.style.transform = '';
        selectedElement.style.opacity = '';
        selectedElement.style.filter = '';
        
        // Force a reflow to ensure the transition starts from the current position
        selectedElement.offsetHeight;
        
        console.log('✅ [BREADCRUMB] Added breadcrumb classes for smooth transition');
        console.log('🔍 [BREADCRUMB] Element classes after:', selectedElement.className);
        
        // Debug: Check element visibility and positioning
        console.log('🔍 [BREADCRUMB] Element visibility check:');
        console.log('  - offsetWidth:', selectedElement.offsetWidth);
        console.log('  - offsetHeight:', selectedElement.offsetHeight);
        console.log('  - offsetLeft:', selectedElement.offsetLeft);
        console.log('  - offsetTop:', selectedElement.offsetTop);
        console.log('  - getBoundingClientRect:', selectedElement.getBoundingClientRect());
        console.log('  - computedStyle display:', window.getComputedStyle(selectedElement).display);
        console.log('  - computedStyle visibility:', window.getComputedStyle(selectedElement).visibility);
        console.log('  - computedStyle opacity:', window.getComputedStyle(selectedElement).opacity);
        console.log('  - computedStyle zIndex:', window.getComputedStyle(selectedElement).zIndex);
        
        // Wait for breadcrumb animation to complete
        await this.delay(400);
        
        // Check again after animation
        console.log('🔍 [BREADCRUMB] Element visibility after animation:');
        console.log('  - getBoundingClientRect:', selectedElement.getBoundingClientRect());
        console.log('  - Is element still in DOM:', document.contains(selectedElement));
        
        console.log('✅ [BREADCRUMB] Breadcrumb animation completed');
        return selectedElement;
    }
    
    /**
     * Animate child items appearing with stagger effect
     */
    async staggerListAnimation(items, direction = 'in') {
        if (!items || items.length === 0) {
            console.log('🔍 [STAGGER] No items to animate');
            return;
        }
        
        console.log('🔍 [STAGGER] Starting stagger animation for', items.length, 'items');
        
        const staggerDelay = 50;
        const promises = [];
        
        items.forEach((item, index) => {
            const promise = new Promise(resolve => {
                setTimeout(() => {
                    console.log('🔍 [STAGGER] Animating item', index, item);
                    if (direction === 'in') {
                        // For child items, just make them visible (they're already positioned by render())
                        item.style.opacity = '1';
                        item.style.transform = item.style.transform || 'translate(0, 0)';
                        item.style.transition = 'opacity 300ms ease-out, transform 300ms ease-out';
                        item.style.transitionDelay = `${index * staggerDelay}ms`;
                    } else {
                        item.classList.add('parent-fade-in', `stagger-${Math.min(index + 1, 9)}`);
                        requestAnimationFrame(() => {
                            item.classList.add('visible');
                        });
                    }
                    resolve();
                }, index * staggerDelay);
            });
            promises.push(promise);
        });
        
        await Promise.all(promises);
        await this.delay(400); // Wait for all animations to complete
        console.log('🔍 [STAGGER] Stagger animation completed');
    }
    
    /**
     * Animate breadcrumb sliding back and parent items fading in
     */
    async animateChildToParentTransition(breadcrumbElement) {
        if (!breadcrumbElement) return;
        
        // Remove breadcrumb class to slide back
        breadcrumbElement.classList.remove('breadcrumb');
        
        // Wait for breadcrumb slide-back animation
        await this.delay(400);
        
        // Remove transition classes
        breadcrumbElement.classList.remove('hierarchy-transition');
    }
    
    /**
     * Utility delay function for animation timing
     */
    delay(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    /**
     * Enter child view - show children from the selected parent
     */
    async enterChildView() {
        console.log('enterChildView called, current mode:', this.viewMode, 'currentIndex:', this.currentIndex);
        console.log('parentData:', this.parentData);
        console.log('parentData length:', this.parentData ? this.parentData.length : 'null');
        
        console.log('Checking conditions: viewMode:', this.viewMode, 'parentData exists:', !!this.parentData, 'currentIndex:', this.currentIndex, 'rounded:', Math.round(this.currentIndex));
        console.log('parentData length:', this.parentData ? this.parentData.length : 'no data');
        console.log('parentData[rounded] exists:', this.parentData ? !!this.parentData[Math.round(this.currentIndex)] : 'no data');
        
        if (this.viewMode !== 'parent' || !this.parentData || !this.parentData[Math.round(this.currentIndex)]) {
            console.log('Cannot enter child view - conditions not met');
            console.log('viewMode:', this.viewMode, 'parentData exists:', !!this.parentData, 'currentIndex:', this.currentIndex, 'rounded:', Math.round(this.currentIndex));
            return;
        }
        
        // Check if the selected playlist has tracks
        const selectedPlaylist = this.parentData[Math.round(this.currentIndex)];
        console.log('Selected playlist for child view:', selectedPlaylist.name, 'has tracks:', selectedPlaylist[this.config.parentKey] ? selectedPlaylist[this.config.parentKey].length : 'none');
        if (!selectedPlaylist[this.config.parentKey] || selectedPlaylist[this.config.parentKey].length === 0) {
            console.log('Cannot enter child view - selected playlist has no tracks');
            console.log('Playlist:', selectedPlaylist.name, 'tracks:', selectedPlaylist[this.config.parentKey]);
            return;
        }
        
        // Prevent multiple simultaneous calls
        if (this.isAnimating) {
            console.log('Already animating - ignoring enterChildView call');
            return;
        }
        
        this.isAnimating = true;
        
        // Ensure animation flag is reset even if we return early
        try {
        
        // Save current parent position
        this.savedParentIndex = Math.round(this.currentIndex);
        this.selectedParent = this.parentData[Math.round(this.currentIndex)];
        console.log('Selected parent:', this.selectedParent.name);
        
        // Switch to child view mode immediately to fix view mode transition
        this.viewMode = 'child';
        console.log('Set viewMode to child, current viewMode:', this.viewMode);
        
        // NOTE: Don't call render() here as child items haven't been loaded yet
        
        // Find the selected element for animation with error handling
        let selectedElement = null;
        
        try {
            selectedElement = document.querySelector('.arc-item.selected');
            if (selectedElement) {
                console.log('DEBUG: Found selected element with classes:', selectedElement.className);
                console.log('DEBUG: Element dataset:', selectedElement.dataset);
            }
            console.log('Found selected element:', selectedElement);
        } catch (error) {
            console.log('Error finding selected element:', error);
            selectedElement = null;
        }
        
        if (!selectedElement) {
            console.log('No selected element found for animation - trying alternative approach');
            // Fallback: try to find by data attribute
            const centerIndex = Math.round(this.currentIndex);
            let fallbackElement = null;
            
            try {
                fallbackElement = document.querySelector(`[data-item-id="${this.items[centerIndex]?.id}"]`);
            } catch (error) {
                console.log('Error finding fallback element:', error);
                fallbackElement = null;
            }
            
            if (fallbackElement) {
                console.log('Using fallback element for animation');
                await this.performEnhancedChildTransition(fallbackElement);
            } else {
                console.log('No element found at all - creating breadcrumb and loading children');
                // Create a breadcrumb even when no element exists to animate
                this.createBreadcrumbElement();
                this.loadParentChildren();
            }
        } else {
            await this.performEnhancedChildTransition(selectedElement);
        }
        
        } catch (error) {
            console.error('Error in enterChildView:', error);
            // Reset view mode to parent on error
            this.viewMode = 'parent';
        } finally {
            // Always reset animation flag
            this.isAnimating = false;
        }
    }
    
    /**
     * Emergency fallback - call this if parent items disappear
     */
    emergencyRestoreParentView() {
        console.log('🚨 [EMERGENCY] Restoring parent view');
        this.viewMode = 'parent';
        this.isAnimating = false;
        
        if (this.parentData && this.parentData.length > 0) {
            this.items = this.parentData.map((parent, index) => ({
                id: parent.id,
                name: parent.name || `Parent ${index + 1}`,
                image: parent.image || 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjQiIGhlaWdodD0iNjQiIHZpZXdCb3g9IjAgMCA2NCA2NCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiBmaWxsPSIjMzMzMzMzIi8+Cjx0ZXh0IHg9IjMyIiB5PSI0MCIgZm9udC1mYW1pbHk9IkFyaWFsLCBzYW5zLXNlcmlmIiBmb250LXNpemU9IjI0IiBmaWxsPSIjZmZmZmZmIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj7imqo8L3RleHQ+Cjwvc3ZnPgo='
            }));
        }
        
        this.render();
        console.log('🚨 [EMERGENCY] Parent view restored');
    }
    
    async performEnhancedChildTransition(selectedElement) {
        console.log('🔍 [ENHANCED] performEnhancedChildTransition starting with element:', selectedElement);
        
        try {
            // Phase 1: Activate hierarchy background
            console.log('🔍 [ENHANCED] Phase 1: Activating hierarchy background');
            await this.animateHierarchyTransition('background', 'enter');
            
            // Phase 2: Transform the EXISTING selected element into breadcrumb
            console.log('🔍 [ENHANCED] Phase 2: Animating selected element to breadcrumb position');
            
            if (selectedElement) {
                // Set up the transition first
                selectedElement.style.transition = 'all 400ms cubic-bezier(0.25, 0.46, 0.45, 0.94)';
                selectedElement.style.zIndex = '10';
                selectedElement.style.pointerEvents = 'auto';
                
                // Add breadcrumb class to existing element
                selectedElement.classList.add('breadcrumb');
                selectedElement.classList.remove('selected');
                
                // Clear inline transform and other arc positioning styles to allow CSS to take over
                selectedElement.style.transform = '';
                selectedElement.style.opacity = '';
                selectedElement.style.filter = '';
                
                // Force a reflow to ensure the transition starts from the current position
                selectedElement.offsetHeight;
                
                // Mark it so we can identify it later
                selectedElement.dataset.animatedParent = 'true';
                
                console.log('✅ [ENHANCED] Added breadcrumb class and cleared inline styles for smooth transition');
            } else {
                // Fallback: create a new breadcrumb if no element to animate
                console.log('⚠️ [ENHANCED] No element to animate, creating static breadcrumb');
                this.createBreadcrumbElement();
            }
            
            // Load children
            this.loadParentChildren();
            
            console.log('✅ [ENHANCED] Enhanced child view transition completed');
        } catch (error) {
            console.error('❌ [ENHANCED] Error during enhanced child transition:', error);
            // Fallback to basic child loading if animation fails
            this.loadParentChildren();
        } finally {
            // Always reset animation flag
            this.isAnimating = false;
        }
    }
    
    /**
     * Create a simple breadcrumb element for testing
     */
    createBreadcrumbElement() {
        const container = this.container;
        if (!container) return;
        
        // Create breadcrumb element
        const breadcrumb = document.createElement('div');
        breadcrumb.className = 'arc-item breadcrumb';
        // Don't set inline styles - let CSS handle all positioning
        breadcrumb.style.zIndex = '10';
        breadcrumb.style.pointerEvents = 'auto';
        
        // Add content
        const nameEl = document.createElement('div');
        nameEl.className = 'item-name';
        nameEl.textContent = this.selectedParent ? this.selectedParent.name : 'Selected Playlist';
        
        const imgEl = document.createElement('img');
        imgEl.className = 'item-image';
        imgEl.src = this.selectedParent ? this.selectedParent.image : '';
        imgEl.loading = 'lazy';
        
        breadcrumb.appendChild(nameEl);
        breadcrumb.appendChild(imgEl);
        
        container.appendChild(breadcrumb);
        
        console.log('✅ Created breadcrumb element for:', this.selectedParent ? this.selectedParent.name : 'Unknown');
    }
    
    /**
     * Fallback method to ensure parent items are always visible
     */
    ensureParentItemsVisible() {
        if (this.viewMode === 'parent' && this.parentData && this.parentData.length > 0) {
            console.log('🔍 [FALLBACK] Ensuring parent items are visible');
            this.items = this.parentData.map((parent, index) => ({
                id: parent.id,
                name: parent.name || `Parent ${index + 1}`,
                image: parent.image || 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjQiIGhlaWdodD0iNjQiIHZpZXdCb3g9IjAgMCA2NCA2NCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiBmaWxsPSIjMzMzMzMzIi8+Cjx0ZXh0IHg9IjMyIiB5PSI0MCIgZm9udC1mYW1pbHk9IkFyaWFsLCBzYW5zLXNlcmlmIiBmb250LXNpemU9IjI0IiBmaWxsPSIjZmZmZmZmIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj7imqo8L3RleHQ+Cjwvc3ZnPgo='
            }));
            this.render();
            console.log('🔍 [FALLBACK] Parent items rendered:', this.items.length);
        }
    }

    // REMOVED: Duplicate renderChildItems() method that was causing infinite loop
    // The original renderChildItems() method is defined earlier in the file
    
    /**
     * Load children from the selected parent
     */
    loadParentChildren() {
        if (!this.selectedParent || !this.selectedParent[this.config.parentKey]) {
            console.error('No child items found for selected parent');
            return;
        }
        
        const childItems = this.selectedParent[this.config.parentKey];
        
        // Convert child items to our format
        if (this.config.childNameMapper) {
            // Use custom mapper for child names
            this.items = childItems.map(item => this.config.childNameMapper(item));
        } else {
            // Default mapping
            this.items = childItems.map(item => ({
                id: item.id,
                name: item.name || item.title || 'Unnamed Item',
                image: item.image || item.thumbnail || 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjQiIGhlaWdodD0iNjQiIHZpZXdCb3g9IjAgMCA2NCA2NCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiBmaWxsPSIjMzMzMzMzIi8+Cjx0ZXh0IHg9IjMyIiB5PSI0MCIgZm9udC1mYW1pbHk9IkFyaWFsLCBzYW5zLXNlcmlmIiBmb250LXNpemU9IjI0IiBmaWxsPSIjZmZmZmZmIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj7imqo8L3RleHQ+Cjwvc3ZnPgo='
            }));
        }
        
        this.viewMode = 'child';
        this.currentIndex = 0;
        this.targetIndex = 0;
        // Don't call render() directly - let the animation loop handle it
        console.log('Switched to child view:', this.items.length, 'items');
    }

    /**
     * Exit child view - return to parent selection
     */
    async exitChildView() {
        if (this.viewMode !== 'child') return;
        
        console.log('Exiting child view, returning to parent:', this.savedParentIndex);
        
        // Set animation state to prevent render interference
        this.isAnimating = true;
        
        // Find the breadcrumb element (the animated parent item) with error handling
        let breadcrumbElement = null;
        
        try {
            breadcrumbElement = document.querySelector('.arc-item.breadcrumb');
        } catch (error) {
            console.log('Error finding breadcrumb element:', error);
            breadcrumbElement = null;
        }
        
        if (breadcrumbElement) {
            await this.performEnhancedParentTransition(breadcrumbElement);
        } else {
            console.log('No breadcrumb element found - using fallback transition');
            await this.performFallbackParentTransition();
        }
    }
    
    async performEnhancedParentTransition(breadcrumbElement) {
        try {
            // Phase 1: Fade out child items
            const childItems = Array.from(document.querySelectorAll('.arc-item[data-child-item="true"]'));
            childItems.forEach(item => {
                item.classList.add('parent-fade-out');
            });
            
            // Phase 2: Animate breadcrumb back to normal position
            breadcrumbElement.classList.remove('breadcrumb');
            breadcrumbElement.classList.add('selected');
            delete breadcrumbElement.dataset.animatedParent;
            
            // Wait for animations to complete
            await this.delay(200);
            
            // Phase 2: Slide breadcrumb back to center
            await this.animateChildToParentTransition(breadcrumbElement);
            
            // Phase 3: Restore parent data and items
            this.restoreParentView();
            
            // Phase 4: Deactivate hierarchy background
            await this.animateHierarchyTransition('background', 'exit');
            
            // Phase 5: Animate parent items back with stagger effect
            const parentItems = Array.from(document.querySelectorAll('.arc-item:not([data-child-item="true"])'));
            await this.staggerListAnimation(parentItems, 'out');
            
            console.log('Enhanced parent view transition completed');
        } catch (error) {
            console.error('Error during enhanced parent transition:', error);
            // Fallback to basic parent restoration
            await this.performFallbackParentTransition();
        }
    }
    
    async performFallbackParentTransition() {
        // Fallback method similar to original implementation
        console.log('Using fallback parent transition');
        
        // Restore parent data and view
        this.restoreParentView();
        
        // Simple delay then render
        await this.delay(300);
        this.render();
        console.log('Fallback parent transition completed');
    }
    
    restoreParentView() {
        // Restore parent items
        this.items = this.parentData.map((parent, index) => ({
            id: parent.id,
            name: parent.name || `Parent ${index + 1}`,
            image: parent.image || 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjQiIGhlaWdodD0iNjQiIHZpZXdCb3g9IjAgMCA2NCA2NCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiBmaWxsPSIjMzMzMzMzIi8+Cjx0ZXh0IHg9IjMyIiB5PSI0MCIgZm9udC1mYW1pbHk9IkFyaWFsLCBzYW5zLXNlcmlmIiBmb250LXNpemU9IjI0IiBmaWxsPSIjZmZmZmZmIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj7imqo8L3RleHQ+Cjwvc3ZnPgo='
        }));
        
        this.viewMode = 'parent';
        this.currentIndex = this.savedParentIndex; // Return to exact same position
        this.targetIndex = this.savedParentIndex;
        this.selectedParent = null;
        
        // Re-enable rendering
        this.isAnimating = false;
        console.log('Restored parent view data');
    }

    /**
     * Send webhook for button presses (left/right)
     */
    async sendButtonWebhook(button) {
        console.log(`🟡 [IFRAME-WEBHOOK] sendButtonWebhook called for: ${button}`);
        
        // For button webhooks, we don't need an item ID, just use "1" as default
        const webhookData = {
            device_type: "Panel",
            panel_context: this.config.context,
            button: button,
            id: "1"
        };
        
        console.log(`🟢 [IFRAME-WEBHOOK] Sending ${button} button webhook to: ${this.config.webhookUrl}`);
        console.log(`🟢 [IFRAME-WEBHOOK] Payload:`, JSON.stringify(webhookData, null, 2));
        
        const startTime = Date.now();
        
        // Send webhook to Home Assistant
        try {
            const response = await fetch(this.config.webhookUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(webhookData)
            });
            
            const duration = Date.now() - startTime;
            
            if (response.ok) {
                console.log(`✅ [IFRAME-WEBHOOK] SUCCESS: ${button} button webhook sent successfully (${duration}ms):`, webhookData);
            } else {
                console.log(`❌ [IFRAME-WEBHOOK] FAILED: ${button} button webhook failed with status ${response.status} ${response.statusText} (${duration}ms)`);
            }
        } catch (error) {
            const duration = Date.now() - startTime;
            console.log(`🔴 [IFRAME-WEBHOOK] ERROR: ${button} button webhook - ${error.message} (${duration}ms)`);
            console.log(`🔴 [IFRAME-WEBHOOK] Error details:`, error);
        }
    }

    /**
     * Send webhook with appropriate ID based on current view mode
     */
    async sendGoWebhook() {
        console.log(`🟡 [IFRAME-WEBHOOK] sendGoWebhook called - viewMode: ${this.viewMode}, items: ${this.items.length}`);
        
        if (this.items.length === 0) {
            console.log(`🔴 [IFRAME-WEBHOOK] No items available - aborting webhook`);
            return;
        }
        
        let id;
        let itemName;
        let webhookData;
        
        // Get appropriate ID based on current mode
        if (this.viewMode === 'parent' || this.viewMode === 'single') {
            // Send parent item ID
            const currentItem = this.parentData[this.currentIndex] || this.items[this.currentIndex];
            if (!currentItem) {
                console.log(`🔴 [IFRAME-WEBHOOK] No current item found at index ${this.currentIndex}`);
                return;
            }
            
            id = currentItem.id;
            itemName = currentItem.name || currentItem[this.config.parentNameKey];
            
            // For music context, prepend Spotify URI prefix for playlists
            if (this.config.context === 'music') {
                id = `spotify:playlist:${id}`;
                console.log(`🟡 [IFRAME-WEBHOOK] Preparing webhook for playlist: ${itemName}, Spotify ID: ${id}`);
            } else {
                console.log(`🟡 [IFRAME-WEBHOOK] Preparing webhook for parent item: ${itemName}, ID: ${id}`);
            }
            
            // Use standardized format for all contexts
            webhookData = {
                device_type: "Panel",
                panel_context: this.config.context,
                button: "go",
                id: id
            };
        } else if (this.viewMode === 'child') {
            // Send child item ID
            const currentChild = this.selectedParent[this.config.parentKey][this.currentIndex];
            if (!currentChild) {
                console.log(`🔴 [IFRAME-WEBHOOK] No current child item found at index ${this.currentIndex}`);
                return;
            }
            
            id = currentChild.id;
            itemName = currentChild.name || currentChild.title;
            
            // For music context, prepend Spotify URI prefix for tracks and include parent playlist ID
            if (this.config.context === 'music') {
                id = `spotify:track:${id}`;
                const parentPlaylistId = `spotify:playlist:${this.selectedParent.id}`;
                console.log(`🟡 [IFRAME-WEBHOOK] Preparing webhook for track: ${itemName}, Spotify ID: ${id}, Parent Playlist: ${parentPlaylistId}`);
                
                // Include parent_id for music tracks
                webhookData = {
                    device_type: "Panel",
                    panel_context: this.config.context,
                    button: "go",
                    id: id,
                    parent_id: parentPlaylistId
                };
            } else {
                console.log(`🟡 [IFRAME-WEBHOOK] Preparing webhook for child item: ${itemName}, ID: ${id}`);
                
                // Use standardized format for non-music child items
                webhookData = {
                    device_type: "Panel",
                    panel_context: this.config.context,
                    button: "go",
                    id: id
                };
            }
        } else {
            console.log(`🔴 [IFRAME-WEBHOOK] Unknown view mode: ${this.viewMode} - aborting webhook`);
            return;
        }
        
        console.log(`🟢 [IFRAME-WEBHOOK] Sending webhook to: ${this.config.webhookUrl}`);
        console.log(`🟢 [IFRAME-WEBHOOK] Payload:`, JSON.stringify(webhookData, null, 2));
        
        const startTime = Date.now();
        
        // Send webhook to Home Assistant
        try {
            const response = await fetch(this.config.webhookUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(webhookData)
            });
            
            const duration = Date.now() - startTime;
            
            if (response.ok) {
                console.log(`✅ [IFRAME-WEBHOOK] SUCCESS: Webhook sent successfully (${duration}ms):`, webhookData);
            } else {
                console.log(`❌ [IFRAME-WEBHOOK] FAILED: Webhook failed with status ${response.status} ${response.statusText} (${duration}ms)`);
            }
        } catch (error) {
            const duration = Date.now() - startTime;
            console.log(`🔴 [IFRAME-WEBHOOK] ERROR: ${error.message} (${duration}ms)`);
            console.log(`🔴 [IFRAME-WEBHOOK] Error details:`, error);
        }
    }
}

// ===== ArcList CLASS ONLY =====
// No automatic initialization - each HTML file controls its own setup