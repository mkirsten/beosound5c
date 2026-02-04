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
            
            // WebSocket configuration - defaults from AppConfig if available
            webSocketUrl: config.webSocketUrl || (typeof AppConfig !== 'undefined' ? AppConfig.websocket.input : 'ws://localhost:8765'),
            webhookUrl: config.webhookUrl || (typeof AppConfig !== 'undefined' ? AppConfig.webhookUrl : 'http://localhost:8767/forward'),
            
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
        await this.loadData();

        // Restore saved position and view mode
        this.restoreState();
        
        this.setupEventListeners(); // Listen for keyboard input
        this.startAnimation(); // Begin the smooth animation loop
        this.updateCounter(); // Show initial counter values
        this.totalItemsDisplay.textContent = this.items.length; // Set total items display
        
        // Force initial render
        this.render();
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
                // Filter out empty playlists (those with no tracks)
                const nonEmptyParents = this.parentData.filter(parent => {
                    const children = parent[this.config.parentKey];
                    return children && Array.isArray(children) && children.length > 0;
                });
                
                console.log(`Filtered out ${this.parentData.length - nonEmptyParents.length} empty playlists`);
                
                // Update parentData to only include non-empty playlists
                this.parentData = nonEmptyParents;
                
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
                        
                        // Also create the breadcrumb element after a delay to ensure DOM is ready
                        setTimeout(() => {
                            this.createBreadcrumbFromRestore();
                        }, 100);
                        
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
            this.items = children.map((child, index) => this.config.childNameMapper(child, index, children));
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
     * Create breadcrumb element when restoring from saved child view
     */
    createBreadcrumbFromRestore() {
        if (!this.selectedParent || this.viewMode !== 'child') return;
        
        // Check if breadcrumb already exists
        if (document.querySelector('.arc-item.breadcrumb')) return;
        
        // Create breadcrumb element
        const breadcrumb = document.createElement('div');
        breadcrumb.className = 'arc-item breadcrumb';
        breadcrumb.dataset.animatedParent = 'true';
        
        // Position at the left - adjusted for fixed-width layout
        const BREADCRUMB_ABSOLUTE_X = -320; // Positioned left but not too far
        breadcrumb.style.transform = `translate(${BREADCRUMB_ABSOLUTE_X}px, 0px) scale(1)`;
        breadcrumb.style.position = 'absolute';
        breadcrumb.style.top = '50%';
        breadcrumb.style.left = '50%';
        breadcrumb.style.marginLeft = '-140px'; // Half of 280px width
        breadcrumb.style.marginTop = '-64px';
        breadcrumb.style.opacity = '1'; // Full opacity
        breadcrumb.style.zIndex = '10';
        breadcrumb.style.pointerEvents = 'auto';
        
        // Add content
        const nameEl = document.createElement('div');
        nameEl.className = 'item-name';
        nameEl.textContent = this.selectedParent.name;
        
        const imgContainer = document.createElement('div');
        imgContainer.className = 'item-image-container';
        
        const imgEl = document.createElement('img');
        imgEl.className = 'item-image';
        imgEl.src = this.selectedParent.image;
        imgEl.loading = 'lazy';
        
        imgContainer.appendChild(imgEl);
        breadcrumb.appendChild(nameEl);
        breadcrumb.appendChild(imgContainer);
        
        this.container.appendChild(breadcrumb);
        
        console.log('Created breadcrumb from restore for:', this.selectedParent.name);
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
            if (event.data && event.data.type === 'button') {
                this.handleButtonFromParent(event.data.button);
            } else if (event.data && event.data.type === 'nav') {
                this.handleNavFromParent(event.data.data);
            } else if (event.data && event.data.type === 'keyboard') {
                this.handleKeyboardFromParent(event.data);
            }
        });
    }
    
    /**
     * Handle keyboard input for navigation
     * Updates target scroll position and resets snap timer
     */
    handleKeyPress(e) {
        const now = Date.now();
        this.lastScrollTime = now; // Record when user last interacted

        if (e.key === 'ArrowUp') {
            // Move up in the list (decrease index) - use base scroll step for keyboard
            this.targetIndex = Math.max(0, this.targetIndex - this.SCROLL_STEP);
            this.setupSnapTimer(); // Reset auto-snap timer
        } else if (e.key === 'ArrowDown') {
            // Move down in the list (increase index) - use base scroll step for keyboard
            this.targetIndex = Math.min(this.items.length - 1, this.targetIndex + this.SCROLL_STEP);
            this.setupSnapTimer(); // Reset auto-snap timer
        } else if (e.key === 'ArrowLeft') {
            // Always send webhook for left button press
            this.sendButtonWebhook('left');

            // Also handle hierarchical navigation if applicable
            if (this.config.viewMode === 'hierarchical' && this.viewMode === 'parent') {
                this.enterChildView();
            }
        } else if (e.key === 'ArrowRight') {
            // Always send webhook for right button press
            this.sendButtonWebhook('right');

            // Also handle hierarchical navigation if applicable
            if (this.config.viewMode === 'hierarchical' && this.viewMode === 'child') {
                this.exitChildView();
            }
        } else if (e.key === 'Enter') {
            // Trigger "go" action (same as WebSocket "go" button)
            this.sendGoWebhook();
        }
    }
    
    /**
     * Handle button events forwarded from parent window (when in iframe)
     */
    handleButtonFromParent(button) {
        if (button === 'left') {
            // Always send webhook for left button press
            this.sendButtonWebhook('left');

            // Also handle hierarchical navigation if applicable
            if (this.config.viewMode === 'hierarchical' && this.viewMode === 'parent') {
                this.enterChildView();
            }
        } else if (button === 'right') {
            // Always send webhook for right button press
            this.sendButtonWebhook('right');

            // Also handle hierarchical navigation if applicable
            if (this.config.viewMode === 'hierarchical' && this.viewMode === 'child') {
                this.exitChildView();
            }
        } else if (button === 'go') {
            // Trigger "go" action (same as keyboard Enter)
            this.sendGoWebhook();
        }
    }
    
    /**
     * Handle keyboard events forwarded from parent window (when in iframe)
     */
    handleKeyboardFromParent(keyboardData) {
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
        
        // Track last rendered position to avoid unnecessary renders
        let lastRenderedIndex = this.currentIndex;
        let lastRenderTime = 0;
        const MIN_RENDER_INTERVAL = 16; // Minimum ms between renders (60fps)
        
        const animate = () => {
            // Smooth interpolation between current and target position
            const diff = this.targetIndex - this.currentIndex;
            const previousIndex = this.currentIndex;
            
            if (Math.abs(diff) < 0.01) {
                // Close enough - just snap to target
                this.currentIndex = this.targetIndex;
            } else {
                // Move smoothly towards target
                this.currentIndex += diff * this.SCROLL_SPEED;
            }
            
            // Check if selection has changed and trigger click
            this.checkForSelectionClick();
            
            // Only render if position has actually changed significantly
            const positionChanged = Math.abs(this.currentIndex - lastRenderedIndex) > 0.001;
            const now = Date.now();
            const enoughTimeElapsed = (now - lastRenderTime) >= MIN_RENDER_INTERVAL;
            
            if (positionChanged && enoughTimeElapsed && !this.container.classList.contains('transitioning-to-parent')) {
                this.render(); // Position all visible items
                lastRenderedIndex = this.currentIndex;
                lastRenderTime = now;
            }
            
            // Always update counter if position changed
            if (previousIndex !== this.currentIndex) {
                this.updateCounter();
            }
            
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
            const opacity = 1; // Always full opacity for all items
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
     * Update existing elements without recreating DOM
     * Returns true if successful, false if full render needed
     */
    updateExistingElements() {
        const existingItems = Array.from(this.container.querySelectorAll('.arc-item:not(.breadcrumb)'));
        const visibleItems = this.getVisibleItems();
        
        // If item count doesn't match, need full render
        if (existingItems.length !== visibleItems.length) {
            return false;
        }
        
        // Check if items are the same (not just count)
        // Compare the actual visible items, not just their IDs
        let needsFullRender = false;
        for (let i = 0; i < existingItems.length; i++) {
            const element = existingItems[i];
            const item = visibleItems[i];
            // Check if this element represents the same item
            if (!element || !item || element.dataset.itemId !== item.id) {
                needsFullRender = true;
                break;
            }
        }
        if (needsFullRender) {
            return false;
        }
        
        // Update positions of existing elements
        existingItems.forEach((element, index) => {
            const item = visibleItems[index];
            if (!item) return;
            
            // Clean up any animation classes that might interfere
            element.classList.remove('playlist-enter', 'track-exit');
            // Remove all delay classes
            for (let i = 1; i <= 9; i++) {
                element.classList.remove(`delay-${i}`);
            }
            
            // Update transform
            element.style.transform = `translate(${item.x}px, ${item.y}px) scale(${item.scale})`;
            element.style.setProperty('opacity', '1', 'important'); // Always full opacity
            element.style.filter = 'none'; // No blur or filters
            
            // Update selected state
            const isSelected = Math.abs(item.index - this.currentIndex) < 0.5;
            const nameEl = element.querySelector('.item-name');
            
            if (isSelected && !element.classList.contains('selected')) {
                element.classList.add('selected');
                if (nameEl) {
                    nameEl.classList.add('selected');
                    nameEl.classList.remove('unselected');
                }
            } else if (!isSelected && element.classList.contains('selected')) {
                element.classList.remove('selected');
                if (nameEl) {
                    nameEl.classList.remove('selected');
                    nameEl.classList.add('unselected');
                }
            }
        });
        
        return true;
    }
    
    /**
     * Render all visible items to the screen
     * This is called every animation frame to update positions and visibility
     */
    render() {
        // Skip logging to reduce noise
        
        // Don't render if we're in the middle of an animation
        if (this.isAnimating) {
            return;
        }
        
        // Try to update existing elements first (more efficient)
        // But only for parent view - child view needs full render for now
        if (this.viewMode === 'parent' && this.updateExistingElements()) {
            return; // Successfully updated without recreating DOM
        }
        
        // If we're in child view, preserve the animated parent item
        if (this.viewMode === 'child') {
            // Only render child items, don't clear the animated parent item
            this.renderChildItems();
            return;
        }
        
        // Clear the container completely to prevent element reuse issues
        // Don't use innerHTML = '' as it's too aggressive, remove children selectively
        const children = Array.from(this.container.children);
        children.forEach(child => {
            // Remove all children except breadcrumbs (in case we're transitioning)
            if (!child.classList.contains('breadcrumb')) {
                child.remove();
            }
        });
        
        const visibleItems = this.getVisibleItems();
        
        // Create fresh DOM elements for each visible item
        visibleItems.forEach((item, index) => {
            // Skip creating this item if it's the one being animated as breadcrumb
            if (this.renderWithSkip && item.id === this.renderWithSkip) {
                return;
            }
            
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
            if (!itemElement.classList.contains('selected')) {
                nameEl.classList.add('unselected');
            } else {
                nameEl.classList.add('selected');
            }
            nameEl.textContent = item.name;
            
            const imgEl = document.createElement('img');
            imgEl.className = 'item-image';
            imgEl.src = item.image;
            imgEl.loading = 'lazy';
            
            // Apply positioning and visual effects ONLY to non-breadcrumb items
            if (!itemElement.classList.contains('breadcrumb')) {
                itemElement.style.transform = `translate(${item.x}px, ${item.y}px) scale(${item.scale})`;
                itemElement.style.setProperty('opacity', '1', 'important'); // Ensure playlist items have full opacity (non-faded appearance)
                itemElement.style.setProperty('filter', 'none', 'important'); // Remove blur for playlist items to keep them bright
                
                // Let CSS handle all styling - just set the classes
                if (itemElement.classList.contains('selected')) {
                    nameEl.classList.add('selected');
                } else {
                    nameEl.classList.add('unselected');
                }
                
                // Override image styling to ensure it's fully bright (like playlist list)
                imgEl.style.setProperty('opacity', '1', 'important'); // Full opacity for playlist artwork - force override
                imgEl.style.setProperty('filter', 'none', 'important'); // Remove any filters that might cause fading - force override
            }
            
            // Add elements to the item container - EXACTLY like music.html
            itemElement.appendChild(nameEl);
            itemElement.appendChild(imgEl);
            
            // If we're in parent transition, add animation classes
            if (this.inParentTransition && !itemElement.dataset.childItem) {
                const index = visibleItems.indexOf(item);
                itemElement.classList.add('playlist-enter', `delay-${Math.min(index + 1, 9)}`);
                itemElement.style.opacity = '0'; // Start hidden
            }
            
            // Add item to the main container
            this.container.appendChild(itemElement);
        });
        
        // Update the counter with current position
        this.updateCounter();
    }
    
    /**
     * Render child items while preserving the animated parent item
     */
    renderChildItems() {
        
        // Remove existing child items but preserve breadcrumb
        const existingChildItems = document.querySelectorAll('.arc-item[data-child-item="true"]');
        existingChildItems.forEach(item => item.remove());
        
        // Don't remove parent-hidden items, we need them for going back
        // Just ensure our tracks have full opacity
        
        const visibleItems = this.getVisibleItems();
        
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
            
            // Create name element with proper classes from the start
            const nameEl = document.createElement('div');
            nameEl.className = 'item-name';
            if (!itemElement.classList.contains('selected')) {
                nameEl.classList.add('unselected');
            } else {
                nameEl.classList.add('selected');
            }
            nameEl.textContent = item.name;
            
            const imgEl = document.createElement('img');
            imgEl.className = 'item-image';
            imgEl.src = item.image;
            imgEl.loading = 'lazy';
            
            // Apply positioning and visual effects ONLY to non-breadcrumb child items
            if (!itemElement.classList.contains('breadcrumb')) {
                itemElement.style.transform = `translate(${item.x}px, ${item.y}px) scale(${item.scale})`;
                itemElement.style.setProperty('opacity', '1', 'important'); // Ensure tracks have full opacity (non-faded appearance)
                itemElement.style.setProperty('filter', 'none', 'important'); // Remove blur for child items to keep them bright
                
                // Classes already set above - no need to set again
                
                // Override image styling to ensure it's fully bright (like playlist list)
                imgEl.style.setProperty('opacity', '1', 'important'); // Full opacity for track artwork - force override
                imgEl.style.setProperty('filter', 'none', 'important'); // Remove any filters that might cause fading - force override
            }
            
            // Add elements to the item container
            itemElement.appendChild(nameEl);
            itemElement.appendChild(imgEl);
            
            // Add item to the main container
            this.container.appendChild(itemElement);
            
            // Force full opacity after adding to DOM to override any inherited styles
            itemElement.style.setProperty('opacity', '1', 'important');
            itemElement.style.setProperty('visibility', 'visible', 'important');
            itemElement.style.setProperty('pointer-events', 'auto', 'important');
        });
        
        // Update the counter with current position
        this.updateCounter();
    }
    
    /**
     * Update the counter display (current item number)
     */
    updateCounter() {
        // Show current item number (1-based instead of 0-based)
        const displayIndex = Math.floor(this.currentIndex) + 1;
        this.currentItemDisplay.textContent = displayIndex;
        
        // Update total count based on current view
        // In parent view: show total playlists
        // In child view: show total tracks in current playlist
        this.totalItemsDisplay.textContent = this.items.length;
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
        
        if (!selectedElement) {
            return;
        }
        
        
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
        selectedElement.style.opacity = '1'; // Ensure full opacity
        selectedElement.style.filter = 'none'; // No filters
        
        // Force a reflow to ensure the transition starts from the current position
        selectedElement.offsetHeight;
        
        
        // Debug: Check element visibility and positioning
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
        console.log('  - getBoundingClientRect:', selectedElement.getBoundingClientRect());
        console.log('  - Is element still in DOM:', document.contains(selectedElement));
        
        return selectedElement;
    }
    
    /**
     * Animate child items appearing with stagger effect
     */
    async staggerListAnimation(items, direction = 'in') {
        if (!items || items.length === 0) {
            return;
        }
        
        
        const staggerDelay = 50;
        const promises = [];
        
        items.forEach((item, index) => {
            const promise = new Promise(resolve => {
                setTimeout(() => {
                    if (direction === 'in') {
                        // For child items, just make them visible (they're already positioned by render())
                        item.style.setProperty('opacity', '1', 'important');
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
    }
    
    /**
     * Animate breadcrumb sliding back and parent items fading in
     */
    async animateChildToParentTransition(breadcrumbElement) {
        if (!breadcrumbElement) return;
        
        
        // Get the actual current screen position
        const rect = breadcrumbElement.getBoundingClientRect();
        const containerRect = this.container.getBoundingClientRect();
        
        // Calculate current position relative to container center
        const currentX = rect.left + rect.width/2 - (containerRect.left + containerRect.width/2);
        const currentY = rect.top + rect.height/2 - (containerRect.top + containerRect.height/2);
        
        
        // Use consistent absolute center position for return
        const CENTER_ABSOLUTE_X = 100; // Always return to same absolute center position
        const returnX = CENTER_ABSOLUTE_X;
        const returnY = 0; // Always return with Y=0 for horizontal movement
        
        
        // Phase 1: Completely fade out tracks
        const trackItems = document.querySelectorAll('.arc-item[data-child-item="true"]');
        trackItems.forEach((item, index) => {
            item.style.transition = `opacity 200ms cubic-bezier(0.25, 0.46, 0.45, 0.94)`;
            item.style.transitionDelay = `${index * 25}ms`;
            item.style.setProperty('opacity', '0', 'important');
        });
        
        await this.delay(400); // Wait longer to ensure tracks are completely gone
        
        // Phase 1.5: Remove all track items from DOM to prevent any overlap
        const trackItemsToRemove = document.querySelectorAll('.arc-item[data-child-item="true"]');
        trackItemsToRemove.forEach(item => item.remove());
        
        // Phase 2: Move breadcrumb back to original position
        
        // Set up element for direct animation
        breadcrumbElement.style.position = 'absolute';
        breadcrumbElement.style.top = '50%';
        breadcrumbElement.style.left = '50%';
        breadcrumbElement.style.marginLeft = '-140px'; // Half of 280px width
        breadcrumbElement.style.marginTop = '-64px';
        breadcrumbElement.style.zIndex = '10';
        
        // Start from current position (no vertical movement)
        breadcrumbElement.style.transform = `translate(${currentX}px, 0px) scale(1)`;
        breadcrumbElement.style.transition = 'none'; // No transition for initial position
        
        // Remove breadcrumb class
        breadcrumbElement.classList.remove('breadcrumb');
        
        // Force a reflow
        breadcrumbElement.offsetHeight;
        
        // Now animate to EXACT original position (direct movement back)
        breadcrumbElement.style.transition = 'transform 400ms cubic-bezier(0.25, 0.46, 0.45, 0.94), opacity 400ms cubic-bezier(0.25, 0.46, 0.45, 0.94)';
        breadcrumbElement.style.transform = `translate(${returnX}px, ${returnY}px) scale(1)`;
        breadcrumbElement.style.opacity = '1'; // Full opacity when returning
        breadcrumbElement.style.filter = 'blur(0px)';

        // Wait for breadcrumb slide-back animation to complete
        await this.delay(400);
        
        // Phase 3: Fade in other playlists (only after breadcrumb is in position and tracks are gone)
        const otherPlaylists = document.querySelectorAll('.arc-item:not(.breadcrumb)');
        otherPlaylists.forEach((item, index) => {
            // Remove any classes that might affect opacity
            item.classList.remove('parent-hidden', 'parent-fade-in', 'playlist-enter');
            item.style.transition = 'opacity 400ms cubic-bezier(0.25, 0.46, 0.45, 0.94)';
            item.style.transitionDelay = `${index * 50}ms`;
            // Use !important to ensure full opacity overrides any CSS rules
            item.style.setProperty('opacity', '1', 'important');
        });
        
        // Wait for playlists to fade in
        await this.delay(400);
        
        // Clean up all playlists to ensure they remain visible
        otherPlaylists.forEach((item) => {
            item.style.transition = '';
            item.style.transitionDelay = '';
            // Ensure opacity remains at full after animation
            item.style.setProperty('opacity', '1', 'important');
        });
        
        // Remove transition classes
        breadcrumbElement.classList.remove('hierarchy-transition');
        
        // Clear stored position (no longer needed since we use consistent absolute positions)
        this.originalBreadcrumbPosition = null;
        
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
        console.log('enterChildView called, selectedParent:', this.selectedParent);
        
        // Check if we have a selected parent item
        if (!this.selectedParent) {
            console.log('No selected parent - finding current selection');
            const selectedIndex = Math.round(this.currentIndex);
            this.selectedParent = this.parentData[selectedIndex];
            if (!this.selectedParent) {
                console.log('No parent data found at index', selectedIndex);
                return;
            }
        }
        
        // Check if the selected parent has children
        if (!this.selectedParent[this.config.parentKey] || this.selectedParent[this.config.parentKey].length === 0) {
            console.log('Selected parent has no children');
            return;
        }
        
        // Prevent multiple simultaneous calls
        if (this.isAnimating) {
            console.log('Already animating - ignoring enterChildView call');
            return;
        }
        
        // Clear snap timer to prevent position snapping during breadcrumb animations
        if (this.snapTimer) {
            clearTimeout(this.snapTimer);
            this.snapTimer = null;
        }
        
        // Pause animation loop during breadcrumb transitions
        if (this.animationFrame) {
            cancelAnimationFrame(this.animationFrame);
            this.animationFrame = null;
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
            console.log('No selected element found - cannot animate');
            // Fallback to basic child loading
            this.loadParentChildren();
            return;
        }
        
        // Start the enhanced child transition
        await this.performEnhancedChildTransition(selectedElement);
        
        } catch (error) {
            console.error('Error in enterChildView:', error);
            // Fallback to basic child loading
            this.loadParentChildren();
        } finally {
            this.isAnimating = false;
            // Resume animation loop after transition
            this.startAnimation();
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
        
        try {
            // Phase 1: Activate hierarchy background
            await this.animateHierarchyTransition('background', 'enter');
            
            // Phase 2: DIRECT animation - move element from current position to left
            
            if (selectedElement) {
                // Get the actual current screen position
                const rect = selectedElement.getBoundingClientRect();
                const containerRect = this.container.getBoundingClientRect();
                
                // Calculate current position relative to container center
                const currentX = rect.left + rect.width/2 - (containerRect.left + containerRect.width/2);
                const currentY = rect.top + rect.height/2 - (containerRect.top + containerRect.height/2);
                
                // Store original position for exact return
                this.originalBreadcrumbPosition = { x: currentX, y: currentY };
                
                // Set consistent absolute positions
                const BREADCRUMB_ABSOLUTE_X = -320; // Always move to same absolute left position
                const CENTER_ABSOLUTE_X = 100; // Always return to same absolute center position
                
                
                // Set up element for direct animation
                selectedElement.style.position = 'absolute';
                selectedElement.style.top = '50%';
                selectedElement.style.left = '50%';
                selectedElement.style.marginLeft = '-140px'; // Half of 280px width
                selectedElement.style.marginTop = '-64px';
                selectedElement.style.zIndex = '10';
                selectedElement.style.pointerEvents = 'auto';
                
                // Start from current position (no vertical movement)
                selectedElement.style.transform = `translate(${currentX}px, 0px) scale(1)`;
                selectedElement.style.transition = 'none'; // No transition for initial position
                
                // Add breadcrumb class
                selectedElement.classList.add('breadcrumb');
                selectedElement.classList.remove('selected');
                
                // Force a reflow
                selectedElement.offsetHeight;
                
                // Phase 3: Hide other playlists (but keep them in DOM)
                const otherPlaylists = document.querySelectorAll('.arc-item:not(.breadcrumb)');
                otherPlaylists.forEach(item => {
                    // Add a class to mark them as hidden parents
                    item.classList.add('parent-hidden');
                    item.style.transition = 'opacity 300ms cubic-bezier(0.25, 0.46, 0.45, 0.94)';
                    item.style.setProperty('opacity', '0', 'important');
                    // Don't remove from DOM - just hide them
                });
                
                // Now animate to breadcrumb position (direct left movement to consistent absolute position)
                selectedElement.style.transition = 'transform 250ms cubic-bezier(0.25, 0.46, 0.45, 0.94), opacity 250ms cubic-bezier(0.25, 0.46, 0.45, 0.94)';
                selectedElement.style.transform = `translate(${BREADCRUMB_ABSOLUTE_X}px, 0px) scale(1)`;
                selectedElement.style.opacity = '1'; // Full opacity
                selectedElement.style.filter = 'blur(0px)';
                
                // Mark it so we can identify it later
                selectedElement.dataset.animatedParent = 'true';

                // Wait for both breadcrumb animation and playlist hiding to complete
                await this.delay(400);
                
            } else {
                // Fallback: create a new breadcrumb if no element to animate
                console.log('⚠️ [DIRECT] No element to animate, creating static breadcrumb');
                this.createBreadcrumbElement();
                
                // Still need to hide other playlists in fallback case
                const otherPlaylists = document.querySelectorAll('.arc-item:not(.breadcrumb)');
                otherPlaylists.forEach(item => {
                    item.style.transition = 'opacity 300ms cubic-bezier(0.25, 0.46, 0.45, 0.94)';
                    item.style.setProperty('opacity', '0', 'important'); // Completely hidden
                });
                await this.delay(300);
            }
            
            // Phase 3.5: Keep playlists in DOM but fully hidden
            // DON'T remove items - they're already hidden with parent-hidden class
            // This allows instant visibility when returning to parent view
            
            // Load children
            this.loadParentChildren();
            
            // Temporarily clear animation flag to allow render
            const wasAnimating = this.isAnimating;
            this.isAnimating = false;
            
            // Force render to ensure tracks are displayed
            this.render();
            
            // Restore animation flag
            this.isAnimating = wasAnimating;
            
            // Phase 4: Fade in tracks (only after playlists are completely hidden)
            await this.delay(100); // Small delay to ensure tracks are rendered and playlists are hidden
            const trackItems = document.querySelectorAll('.arc-item[data-child-item="true"]');
            trackItems.forEach((item, index) => {
                item.style.setProperty('opacity', '0', 'important');
                item.style.transition = `opacity 300ms cubic-bezier(0.25, 0.46, 0.45, 0.94)`;
                item.style.transitionDelay = `${index * 50}ms`;
                
                // Fade in with stagger
                setTimeout(() => {
                    item.style.setProperty('opacity', '1', 'important'); // Full opacity for tracks
                }, 50);
            });
            
        } catch (error) {
            console.error('❌ [DIRECT] Error during direct child transition:', error);
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
        
        // Apply direct breadcrumb positioning for consistency (updated position and opacity)
        const BREADCRUMB_ABSOLUTE_X = -320; // Target breadcrumb X position
        const lockedY = 0; // LOCK Y position to prevent any vertical movement
        
        breadcrumb.style.transform = `translate(${BREADCRUMB_ABSOLUTE_X}px, ${lockedY}px) scale(1)`;
        breadcrumb.style.position = 'absolute';
        breadcrumb.style.top = '50%';
        breadcrumb.style.left = '50%';
        breadcrumb.style.marginLeft = '-140px'; // Half of 280px width
        breadcrumb.style.marginTop = '-64px';
        breadcrumb.style.opacity = '1'; // Full opacity // Higher opacity for better readability
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
        
    }
    
    /**
     * Fallback method to ensure parent items are always visible
     */
    ensureParentItemsVisible() {
        if (this.viewMode === 'parent' && this.parentData && this.parentData.length > 0) {
            this.items = this.parentData.map((parent, index) => ({
                id: parent.id,
                name: parent.name || `Parent ${index + 1}`,
                image: parent.image || 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjQiIGhlaWdodD0iNjQiIHZpZXdCb3g9IjAgMCA2NCA2NCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiBmaWxsPSIjMzMzMzMzIi8+Cjx0ZXh0IHg9IjMyIiB5PSI0MCIgZm9udC1mYW1pbHk9IkFyaWFsLCBzYW5zLXNlcmlmIiBmb250LXNpemU9IjI0IiBmaWxsPSIjZmZmZmZmIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj7imqo8L3RleHQ+Cjwvc3ZnPgo='
            }));
            this.render();
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
            // Use custom mapper for child names - pass all tracks for context
            this.items = childItems.map((item, index) => this.config.childNameMapper(item, index, childItems));
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
        
        // Force render to show tracks immediately
        this.render();
        
        // Update counter to show track count
        this.updateCounter();
        
        console.log('Switched to child view:', this.items.length, 'items');
    }

    /**
     * Exit child view - return to parent selection
     */
    async exitChildView() {
        console.log('exitChildView called, viewMode:', this.viewMode);
        
        if (this.viewMode !== 'child') {
            console.log('Not in child view - ignoring exitChildView call');
            return;
        }
        
        // Prevent multiple simultaneous calls
        if (this.isAnimating) {
            console.log('Already animating - ignoring exitChildView call');
            return;
        }
        
        // Clear snap timer to prevent position snapping during breadcrumb animations
        if (this.snapTimer) {
            clearTimeout(this.snapTimer);
            this.snapTimer = null;
        }
        
        // Pause animation loop during breadcrumb transitions
        if (this.animationFrame) {
            cancelAnimationFrame(this.animationFrame);
            this.animationFrame = null;
        }
        
        this.isAnimating = true;
        
        try {
            // Find the breadcrumb element
            const breadcrumbElement = document.querySelector('.arc-item.breadcrumb');
            if (!breadcrumbElement) {
                console.log('No breadcrumb element found - performing fallback transition');
                await this.performFallbackParentTransition();
                return;
            }
            
            console.log('Found breadcrumb element, performing enhanced transition');
            await this.performEnhancedParentTransition(breadcrumbElement);
        } catch (error) {
            console.error('Error in exitChildView:', error);
            // Fallback to basic parent restoration
            await this.performFallbackParentTransition();
        } finally {
            this.isAnimating = false;
            // Resume animation loop after transition
            this.startAnimation();
            // Restore snap timer functionality
            this.setupSnapTimer();
        }
    }
    
    async performEnhancedParentTransition(breadcrumbElement) {
        try {
            
            // Mark container as transitioning
            this.container.classList.add('transitioning-to-parent');
            
            // First, check if we have hidden parent items
            const hiddenParentItems = Array.from(document.querySelectorAll('.arc-item.parent-hidden'));
            
            if (hiddenParentItems.length > 0) {
                // We have hidden parents - just show them instantly!
                
                // Restore view mode
                this.viewMode = 'parent';
                this.currentIndex = this.savedParentIndex;
                this.targetIndex = this.savedParentIndex;
                
                // Show all hidden parent items instantly
                hiddenParentItems.forEach(item => {
                    item.classList.remove('parent-hidden', 'parent-fade-in', 'playlist-enter',
                                         'stagger-1', 'stagger-2', 'stagger-3', 'stagger-4', 
                                         'stagger-5', 'stagger-6', 'stagger-7', 'stagger-8', 
                                         'stagger-9', 'visible');
                    item.style.display = ''; // Remove display: none
                    item.style.visibility = 'visible';
                    // Use !important to ensure opacity overrides any CSS rules
                    item.style.setProperty('opacity', '1', 'important');
                });
                
                // Get child items to hide
                const childItems = Array.from(document.querySelectorAll('.arc-item[data-child-item="true"]'));
                
                // Apply exit animation to tracks
                childItems.forEach(item => {
                    item.classList.add('track-exit');
                });
                
                // Wait for track exit animation
                await this.delay(300);
                
                // Now remove the tracks
                childItems.forEach(item => item.remove());
            } else {
                // Fallback to old approach if no hidden parents found
                
                // Restore parent data
                this.viewMode = 'parent';
                this.items = this.parentData.map((parent, index) => ({
                    id: parent.id,
                    name: parent.name || `Parent ${index + 1}`,
                    image: parent.image || 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjQiIGhlaWdodD0iNjQiIHZpZXdCb3g9IjAgMCA2NCA2NCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiBmaWxsPSIjMzMzMzMzIi8+Cjx0ZXh0IHg9IjMyIiB5PSI0MCIgZm9udC1mYW1pbHk9IkFyaWFsLCBzYW5zLXNlcmlmIiBmb250LXNpemU9IjI0IiBmaWxsPSIjZmZmZmZmIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj7imqo8L3RleHQ+Cjwvc3ZnPgo='
                }));
                
                // Render parent items
                this.renderWithSkip = this.parentData[this.savedParentIndex].id;
                this.inParentTransition = true;
                this.render();
                this.inParentTransition = false;
                this.renderWithSkip = null;
                
                // Get rendered parent items and show them instantly
                const parentItems = Array.from(document.querySelectorAll('.arc-item:not([data-child-item="true"]):not(.breadcrumb)'));
                parentItems.forEach(item => {
                    // Remove any animation classes that might affect opacity
                    item.classList.remove('parent-hidden', 'parent-fade-in', 'playlist-enter',
                                         'stagger-1', 'stagger-2', 'stagger-3', 'stagger-4', 
                                         'stagger-5', 'stagger-6', 'stagger-7', 'stagger-8', 
                                         'stagger-9', 'visible');
                    // Use !important to ensure opacity overrides any CSS rules
                    item.style.setProperty('opacity', '1', 'important');
                    item.style.visibility = 'visible';
                });
            }
            
            // Get current position before making any changes
            const currentTransform = breadcrumbElement.style.transform;
            
            // Transform breadcrumb to its position among playlists
            breadcrumbElement.classList.remove('breadcrumb');
            breadcrumbElement.classList.add('breadcrumb-slide-right', 'selected');
            
            // Mark breadcrumb as the selected parent item so render() won't create a duplicate
            breadcrumbElement.dataset.itemId = this.parentData[this.savedParentIndex].id;
            breadcrumbElement.dataset.isSelectedParent = 'true';
            
            // Keep the current transform to start from the correct position
            breadcrumbElement.style.transform = currentTransform;
            breadcrumbElement.style.position = 'absolute';
            breadcrumbElement.style.opacity = '1';
            breadcrumbElement.style.filter = 'none';
            
            // Force a reflow to ensure starting position is applied
            breadcrumbElement.offsetHeight;
            
            // Since we want the breadcrumb to slide to center position (where it was selected from)
            // and the parent index is restored to savedParentIndex, the relative position is 0
            const relativePos = 0; // Center position
            
            // Position calculations from getVisibleItems for center item
            const scale = 1.0; // Center item is full scale
            const baseXOffset = 100;
            const x = baseXOffset; // Center position has no additional offset
            const y = 0; // Center position has no vertical offset
            
            // Apply the target transform after a tiny delay to ensure transition works
            requestAnimationFrame(() => {
                breadcrumbElement.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
            });
            
            // Wait for breadcrumb animation only (300ms)
            await this.delay(300); // Just wait for breadcrumb slide, not playlist delays
            
            // First, update the state BEFORE any DOM changes
            this.currentIndex = this.savedParentIndex;
            this.targetIndex = this.savedParentIndex;
            this.selectedParent = null;
            
            // Clean up in the right order
            // 1. Child items should already be removed in the hide/show path
            // For fallback path, remove them here
            const remainingChildItems = Array.from(document.querySelectorAll('.arc-item[data-child-item="true"]'));
            remainingChildItems.forEach(item => item.remove());
            
            // 2. Remove animation classes from breadcrumb and ensure it's fully bright
            breadcrumbElement.classList.remove('breadcrumb-slide-right', 'breadcrumb');
            breadcrumbElement.style.transition = '';
            
            // CRITICAL FIX: Completely clean the breadcrumb element to make it a normal selected item
            const breadcrumbNameEl = breadcrumbElement.querySelector('.item-name');
            if (breadcrumbNameEl) {
                // Force bright white text on the breadcrumb (selected playlist)
                breadcrumbNameEl.style.setProperty('color', 'white', 'important');
                breadcrumbNameEl.style.setProperty('opacity', '1', 'important');
                // Add selected class to match normal behavior
                breadcrumbNameEl.classList.add('selected');
                breadcrumbNameEl.classList.remove('unselected');
            }
            // Force full opacity on breadcrumb element itself
            breadcrumbElement.style.setProperty('opacity', '1', 'important');
            breadcrumbElement.style.setProperty('filter', 'none', 'important');
            
            // 3. Remove transitioning class to resume render
            this.container.classList.remove('transitioning-to-parent');
            
            // 4. The breadcrumb is now just a regular selected item at the center
            // No need to remove it - it's already in the right place
            
            // 5. Clean up animation classes from parent items
            parentItems.forEach(item => {
                item.classList.remove('playlist-enter', 'parent-fade-in', 'delay-1', 'delay-2', 'delay-3', 
                                     'delay-4', 'delay-5', 'delay-6', 'delay-7', 
                                     'delay-8', 'delay-9', 'stagger-1', 'stagger-2', 'stagger-3',
                                     'stagger-4', 'stagger-5', 'stagger-6', 'stagger-7',
                                     'stagger-8', 'stagger-9', 'visible');
                item.style.transition = '';
                // Ensure items remain visible after animation - use !important to override any CSS
                item.style.setProperty('opacity', '1', 'important');
            });
            
            // 6. The breadcrumb is already positioned correctly as the center item
            // Remove its dataset flags to make it a normal item
            delete breadcrumbElement.dataset.isSelectedParent;
            delete breadcrumbElement.dataset.childItem;
            
            // Reset animation state
            this.isAnimating = false; // Allow render to work again
            this.inParentTransition = false; // Clear transition flag
            
            // Now we need to ensure all items are properly positioned
            // Use updateExistingElements to adjust positions without recreating DOM
            const visibleItems = this.getVisibleItems();
            const existingItems = Array.from(this.container.querySelectorAll('.arc-item:not([data-child-item="true"])'));
            
            // Update positions of all items to ensure they're correctly placed
            existingItems.forEach((element, index) => {
                const item = visibleItems.find(vi => vi.id === element.dataset.itemId);
                if (item) {
                    // Apply final positions without animation
                    element.style.transition = 'none';
                    element.style.transform = `translate(${item.x}px, ${item.y}px) scale(${item.scale})`;
                    element.style.opacity = '1';
                    element.style.filter = 'none';
                    
                    // Update selected state
                    if (item.isSelected) {
                        element.classList.add('selected');
                    } else {
                        element.classList.remove('selected');
                    }
                }
            });
            
            // Update the counter
            this.updateCounter();
            
            // Deactivate hierarchy background
            await this.animateHierarchyTransition('background', 'exit');
            
            // Ensure all playlist items that should be visible are actually rendered
            // We need to check if we're missing any items that should be visible
            const allVisibleItems = this.getVisibleItems();
            const currentItems = Array.from(this.container.querySelectorAll('.arc-item:not([data-child-item="true"])'));
            
            // Check if we need to render missing items
            const missingItems = allVisibleItems.filter(item => 
                !currentItems.some(el => el.dataset.itemId === item.id)
            );
            
            if (missingItems.length > 0) {
                console.log(`🔍 [PARALLEL-TRANSITION] Rendering ${missingItems.length} missing items`);
                // We have missing items, need to render them
                this.render();
            } else {
            }
            
            // Force selected state update on existing items
            this.updateSelectedState();
            
        } catch (error) {
            console.error('Error during enhanced parent transition:', error);
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
        
        // Update counter after transition
        this.updateCounter();
        
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
        
        // Update counter to show playlist count
        this.updateCounter();
        
        console.log('Restored parent view data');
    }

    /**
     * Update selected state on existing items without recreating them
     */
    updateSelectedState() {
        const parentItems = Array.from(document.querySelectorAll('.arc-item:not([data-child-item="true"]):not(.breadcrumb)'));
        
        parentItems.forEach(item => {
            const itemId = item.dataset.itemId;
            const itemIndex = this.items.findIndex(dataItem => dataItem.id === itemId);
            
            if (itemIndex !== -1) {
                const isSelected = Math.abs(itemIndex - this.currentIndex) < 0.5;
                const nameEl = item.querySelector('.item-name');
                
                if (isSelected) {
                    // Make this item selected
                    item.classList.add('selected');
                    item.classList.remove('unselected');
                    if (nameEl) {
                        nameEl.classList.add('selected');
                        nameEl.classList.remove('unselected');
                        // Ensure bright white text
                        nameEl.style.setProperty('color', 'white', 'important');
                        nameEl.style.setProperty('opacity', '1', 'important');
                    }
                } else {
                    // Make this item unselected
                    item.classList.remove('selected');
                    item.classList.add('unselected');
                    if (nameEl) {
                        nameEl.classList.remove('selected');
                        nameEl.classList.add('unselected');
                        // Still ensure bright white text (we want all text bright)
                        nameEl.style.setProperty('color', 'white', 'important');
                        nameEl.style.setProperty('opacity', '1', 'important');
                    }
                }
                
                // Ensure item itself has full opacity and remove any filters
                item.style.setProperty('opacity', '1', 'important');
                item.style.setProperty('filter', 'none', 'important');
                item.style.setProperty('visibility', 'visible', 'important');
            }
        });
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

        // Notify emulator via bridge (no-op if not in emulator)
        this.notifyEmulatorOfSelection(id, itemName);
    }

    /**
     * Notify emulator of selection (scenes, playlists, tracks)
     * Uses EmulatorBridge - does nothing if not running in emulator
     */
    notifyEmulatorOfSelection(id, itemName) {
        if (!window.EmulatorBridge?.isInEmulator) return;

        if (this.config.context === 'scenes') {
            window.EmulatorBridge.notifySceneActivated(id, itemName);
        } else if (this.config.context === 'music') {
            if (this.viewMode === 'parent' || this.viewMode === 'single') {
                window.EmulatorBridge.notifyPlaylistSelected(
                    this.parentData[this.currentIndex]?.id,
                    itemName
                );
            } else if (this.viewMode === 'child') {
                window.EmulatorBridge.notifyTrackSelected(
                    this.currentIndex,
                    itemName,
                    this.selectedParent?.id,
                    this.selectedParent?.name
                );
            }
        }
    }
}

// ===== ArcList CLASS ONLY =====
// No automatic initialization - each HTML file controls its own setup