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
            dataSource: config.dataSource || '../playlists_with_tracks.json', // URL to JSON data
            dataType: config.dataType || 'playlists', // 'playlists', 'songs', 'custom'
            itemMapper: config.itemMapper || null, // Custom function to map data to items
            
            // View configuration
            viewMode: config.viewMode || 'single', // 'single' or 'hierarchical' (like playlists->songs)
            parentKey: config.parentKey || 'tracks', // Key for child items in hierarchical mode
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
        this.SCROLL_SPEED = 0.05; // How fast scrolling animation happens (0.1 = slow, 0.3 = fast)
        this.SCROLL_STEP = 0.333; // How much to scroll per key press (changed from 0.2 to 1 for better navigation)
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
        this.STORAGE_KEY_PLAYLIST = `${this.config.storagePrefix}_playlist_position`;
        this.STORAGE_KEY_SONGS = `${this.config.storagePrefix}_songs_position`;
        this.STORAGE_KEY_VIEW_MODE = `${this.config.storagePrefix}_view_mode`;
        this.STORAGE_KEY_SELECTED_PLAYLIST = `${this.config.storagePrefix}_selected_playlist`;
        
        // State management for hierarchical view
        this.viewMode = this.config.viewMode === 'hierarchical' ? 'playlists' : 'single';
        this.selectedPlaylist = null;
        this.playlistData = []; // Store full data with children
        this.savedPlaylistIndex = 0; // Remember position when viewing children
        
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
     * Sets up event listeners, loads playlists, starts animation loop, updates counter
     */
    async init() {
        console.log('Initializing ArcList...'); // Debug log
        
        // Validate DOM elements are still available
        if (!this.container || !this.currentItemDisplay || !this.totalItemsDisplay) {
            console.error('Required DOM elements not available during initialization');
            return;
        }
        
        // Load playlist data
        await this.loadPlaylists();
        console.log('Loaded', this.items.length, 'items from', this.config.dataSource);
        
        // Restore saved position and view mode
        this.restoreState();
        
        this.setupEventListeners(); // Listen for keyboard input
        this.startAnimation(); // Begin the smooth animation loop
        this.updateCounter(); // Show initial counter values
        this.totalItemsDisplay.textContent = this.items.length; // Set total items display
    }
    
    /**
     * Load playlist data from playlists_with_tracks.json
     * Each playlist has: name, id, url, image, and tracks array
     */
    async loadPlaylists() {
        try {
            const response = await fetch(this.config.dataSource);
            this.playlistData = await response.json();
            
            // Convert data to our items format based on configuration
            if (this.config.itemMapper) {
                // Use custom mapper function
                this.items = this.config.itemMapper(this.playlistData);
            } else if (this.config.dataType === 'playlists') {
                // Default playlist format
                this.items = this.playlistData.map((playlist, index) => ({
                    id: playlist.id,
                    name: playlist[this.config.parentNameKey] || `Item ${index + 1}`,
                    image: playlist.image || 'https://via.placeholder.com/64x64/333333/ffffff?text=♪'
                }));
            } else if (this.config.dataType === 'custom') {
                // Assume data is already in the correct format
                this.items = this.playlistData;
            } else {
                // Generic fallback
                this.items = this.playlistData.map((item, index) => ({
                    id: item.id || `item-${index}`,
                    name: item.name || item.title || `Item ${index + 1}`,
                    image: item.image || item.thumbnail || 'https://via.placeholder.com/64x64/333333/ffffff?text=♪'
                }));
            }
            
            console.log('Loaded', this.items.length, 'items from', this.config.dataSource);
        } catch (error) {
            console.error('Error loading data:', error);
            // Fallback to dummy data if loading fails
            this.items = [
                { id: 'fallback-1', name: 'Error Loading Data', image: 'https://via.placeholder.com/64x64/ff0000/ffffff?text=!' }
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
     * Save current state to localStorage
     */
    saveState() {
        try {
            localStorage.setItem(this.STORAGE_KEY_VIEW_MODE, this.viewMode);
            if (this.viewMode === 'playlists') {
                localStorage.setItem(this.STORAGE_KEY_PLAYLIST, this.currentIndex.toString());
            } else if (this.viewMode === 'songs') {
                localStorage.setItem(this.STORAGE_KEY_SONGS, this.currentIndex.toString());
                if (this.selectedPlaylist) {
                    localStorage.setItem(this.STORAGE_KEY_SELECTED_PLAYLIST, JSON.stringify({
                        id: this.selectedPlaylist.id,
                        name: this.selectedPlaylist.name,
                        savedPlaylistIndex: this.savedPlaylistIndex
                    }));
                }
            }
            console.log('State saved:', this.viewMode, this.currentIndex);
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
            
            if (savedViewMode === 'songs') {
                // Restore songs view
                const savedSelectedPlaylist = localStorage.getItem(this.STORAGE_KEY_SELECTED_PLAYLIST);
                const savedSongsPosition = localStorage.getItem(this.STORAGE_KEY_SONGS);
                
                if (savedSelectedPlaylist && savedSongsPosition) {
                    const playlistInfo = JSON.parse(savedSelectedPlaylist);
                    const songsIndex = parseFloat(savedSongsPosition);
                    
                    // Find the playlist in our data
                    const playlist = this.playlistData.find(p => p.id === playlistInfo.id);
                    if (playlist) {
                        this.selectedPlaylist = playlist;
                        this.savedPlaylistIndex = playlistInfo.savedPlaylistIndex || 0;
                        this.viewMode = 'songs';
                        
                        // Load songs and set position
                        this.loadPlaylistSongsFromRestore(songsIndex);
                        console.log('Restored songs view:', playlist.name, 'position:', songsIndex);
                        return;
                    }
                }
            }
            
            // Restore playlists view (default)
            const savedPlaylistPosition = localStorage.getItem(this.STORAGE_KEY_PLAYLIST);
            if (savedPlaylistPosition) {
                const position = parseFloat(savedPlaylistPosition);
                this.currentIndex = Math.max(0, Math.min(this.items.length - 1, position));
                this.targetIndex = this.currentIndex;
                console.log('Restored playlist position:', position);
            }
        } catch (error) {
            console.error('Error restoring state:', error);
        }
    }

    /**
     * Load playlist songs when restoring from saved state
     */
    loadPlaylistSongsFromRestore(songsIndex) {
        if (!this.selectedPlaylist || !this.selectedPlaylist.tracks) {
            console.error('No tracks found for playlist during restore');
            return;
        }
        
        // Convert tracks to items format
        this.items = this.selectedPlaylist.tracks.map(track => ({
            id: track.id,
            name: `${track.artist} - ${track.name}`,
            image: track.image || 'https://via.placeholder.com/64x64/333333/ffffff?text=♪'
        }));
        
        // Set position
        this.currentIndex = Math.max(0, Math.min(this.items.length - 1, songsIndex));
        this.targetIndex = this.currentIndex;
        
        // Update display
        this.totalItemsDisplay.textContent = this.items.length;
        console.log('Loaded', this.items.length, 'songs for restore');
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
            // Move up in the list (decrease index) - use base scroll step for keyboard
            this.targetIndex = Math.max(0, this.targetIndex - this.SCROLL_STEP);
            this.setupSnapTimer(); // Reset auto-snap timer
            console.log('Keyboard: Moving up to:', this.targetIndex); // Debug log
        } else if (e.key === 'ArrowDown') {
            // Move down in the list (increase index) - use base scroll step for keyboard
            this.targetIndex = Math.min(this.items.length - 1, this.targetIndex + this.SCROLL_STEP);
            this.setupSnapTimer(); // Reset auto-snap timer
            console.log('Keyboard: Moving down to:', this.targetIndex); // Debug log
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
        // Don't render if we're in the middle of an animation
        if (this.isAnimating) {
            return;
        }
        
        // If we're in song view, preserve the animated playlist item
        if (this.viewMode === 'songs') {
            // Only render song items, don't clear the animated playlist item
            this.renderSongItems();
            return;
        }
        
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
     * Render song items while preserving the animated playlist item
     */
    renderSongItems() {
        // Hide all playlist items except the animated one
        const playlistItems = document.querySelectorAll('.arc-item:not([data-song-item="true"])');
        playlistItems.forEach(item => {
            // Only hide if it's not the animated playlist item
            if (!item.dataset.animatedPlaylist) {
                // Hide non-animated playlist items
                item.style.display = 'none';
            }
        });
        
        // Remove any existing song items
        const songItems = document.querySelectorAll('.arc-item[data-song-item="true"]');
        songItems.forEach(item => item.remove());
        
        const visibleItems = this.getVisibleItems();
        
        // Create fresh DOM elements for each visible song item
        visibleItems.forEach((item, index) => {
            // Create main container for this song item
            const itemElement = document.createElement('div');
            itemElement.className = 'arc-item';
            itemElement.dataset.itemId = item.id;
            itemElement.dataset.songItem = 'true'; // Mark as song item for easy removal
            
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
            this.ws = new WebSocket(this.config.webSocketUrl);
            
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
        
        // Handle button messages for playlist selection and back navigation
        if (data.type === 'button' && data.data && data.data.button) {
            const button = data.data.button;
            console.log('Button event received:', button, 'current view mode:', this.viewMode);
            
            if (button === 'left' && this.viewMode === 'playlists') {
                console.log('Left button pressed in playlist mode - entering playlist view');
                // Select playlist to show songs
                this.enterPlaylistView();
                return;
            } else if (button === 'right' && this.viewMode === 'songs') {
                console.log('Right button pressed in song mode - exiting to playlists');
                // Go back to playlists
                this.exitPlaylistView();
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
                console.log('WebSocket: Moving down to:', this.targetIndex, 'speed:', speed, 'step:', scrollStep);
            } else if (scrollingUp) {
                // Scroll up
                this.targetIndex = Math.max(0, this.targetIndex - scrollStep);
                this.setupSnapTimer(); // Reset auto-snap timer
                console.log('WebSocket: Moving up to:', this.targetIndex, 'speed:', speed, 'step:', scrollStep);
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
        
        /*
        const now = Date.now();
        const CLICK_THROTTLE_MS = 5;
        
        // Rate limiting: only send if at least 50ms have passed since last send
        if (now - (this.lastClickTime || 0) < CLICK_THROTTLE_MS) {
            return;
        }
        
        this.lastClickTime = now;
        */
        const message = {
            type: 'command',
            command: 'click',
            params: {}
        };
        
        this.ws.send(JSON.stringify(message));
    }
    
    /**
     * Check if an item is passing through the selected position and trigger click
     */
    checkForSelectionClick() {
        const centerIndex = Math.round(this.currentIndex);
        const currentItem = this.items[centerIndex];
        
        // Only trigger if we have a valid item and it's different from the last clicked item
        if (currentItem && currentItem.id !== this.lastClickedItemId) {
            console.log('Selected item changed to:', currentItem.name);
            this.sendClickCommand();
            this.lastClickedItemId = currentItem.id;
        }
    }

    /**
     * Enter playlist view - show songs from the selected playlist
     */
    enterPlaylistView() {
        console.log('enterPlaylistView called, current mode:', this.viewMode, 'currentIndex:', this.currentIndex);
        
        if (this.viewMode !== 'playlists' || !this.playlistData[this.currentIndex]) {
            console.log('Cannot enter playlist view - conditions not met');
            return;
        }
        
        // Save current playlist position
        this.savedPlaylistIndex = this.currentIndex;
        this.selectedPlaylist = this.playlistData[this.currentIndex];
        console.log('Selected playlist:', this.selectedPlaylist.name);
        
        // Ensure the render is up to date first
        this.render();
        
        // Animate current playlist 200px left
        const selectedElement = document.querySelector('.arc-item.selected');
        console.log('Found selected element:', selectedElement);
        
        if (selectedElement) {
            // Get current transform and add the left translation
            const currentTransform = selectedElement.style.transform || '';
            selectedElement.style.transform = currentTransform + ' translateX(-200px)';
            selectedElement.style.transition = 'transform 0.3s ease';
            selectedElement.dataset.animatedPlaylist = 'true'; // Mark as animated playlist item
            console.log('Applied animation to selected element');
        } else {
            console.log('No selected element found for animation - trying alternative approach');
            // Fallback: try to find by data attribute
            const centerIndex = Math.round(this.currentIndex);
            const fallbackElement = document.querySelector(`[data-item-id="${this.items[centerIndex]?.id}"]`);
            if (fallbackElement) {
                const currentTransform = fallbackElement.style.transform || '';
                fallbackElement.style.transform = currentTransform + ' translateX(-200px)';
                fallbackElement.style.transition = 'transform 0.3s ease';
                fallbackElement.dataset.animatedPlaylist = 'true'; // Mark as animated playlist item
                console.log('Applied animation to fallback element');
            } else {
                console.log('No element found at all - skipping animation');
            }
        }
        
        // Load songs after animation
        setTimeout(() => {
            console.log('Loading playlist songs after animation');
            this.loadPlaylistSongs();
        }, 300);
    }

    /**
     * Load songs from the selected playlist
     */
    loadPlaylistSongs() {
        if (!this.selectedPlaylist || !this.selectedPlaylist[this.config.parentKey]) {
            console.error('No child items found for selected item');
            return;
        }
        
        const childItems = this.selectedPlaylist[this.config.parentKey];
        
        // Convert child items to our format
        if (this.config.childNameMapper) {
            // Use custom mapper for child names
            this.items = childItems.map(item => this.config.childNameMapper(item));
        } else {
            // Default mapping
            this.items = childItems.map(item => ({
                id: item.id,
                name: item.name || `${item.artist || ''} - ${item.title || item.name || 'Unknown'}`.trim(),
                image: item.image || item.thumbnail || 'https://via.placeholder.com/64x64/333333/ffffff?text=♪'
            }));
        }
        
        this.viewMode = 'songs';
        this.currentIndex = 0;
        this.targetIndex = 0;
        this.render();
        console.log('Switched to child view:', this.items.length, 'items');
    }

    /**
     * Exit playlist view - return to playlist selection
     */
    exitPlaylistView() {
        if (this.viewMode !== 'songs') return;
        
        console.log('Exiting playlist view, returning to playlist:', this.savedPlaylistIndex);
        
        // Set animation state to prevent render interference
        this.isAnimating = true;
        
        // Find the animated playlist item and animate it back
        const animatedPlaylistItem = document.querySelector('.arc-item[data-animated-playlist="true"]');
        if (animatedPlaylistItem) {
            console.log('Found animated playlist item, animating back');
            
            // Animate the playlist item back to its original position
            const currentTransform = animatedPlaylistItem.style.transform;
            // Remove the translateX(-200px) part
            const originalTransform = currentTransform.replace(/ translateX\(-200px\)/, '');
            animatedPlaylistItem.style.transform = originalTransform;
            animatedPlaylistItem.style.transition = 'transform 0.3s ease';
            
            // Remove the animated marker
            delete animatedPlaylistItem.dataset.animatedPlaylist;
        }
        
        // After animation, restore playlist view
        setTimeout(() => {
            // Show all playlist items again
            const playlistItems = document.querySelectorAll('.arc-item:not([data-song-item="true"])');
            playlistItems.forEach(item => {
                item.style.display = '';
            });
            
            // Restore playlist items
            this.items = this.playlistData.map((playlist, index) => ({
                id: playlist.id,
                name: playlist.name || `Playlist ${index + 1}`,
                image: playlist.image || 'https://via.placeholder.com/64x64/333333/ffffff?text=♪'
            }));
            
            this.viewMode = 'playlists';
            this.currentIndex = this.savedPlaylistIndex; // Return to exact same position
            this.targetIndex = this.savedPlaylistIndex;
            this.selectedPlaylist = null;
            
            // Re-enable rendering and render the playlist view
            this.isAnimating = false;
            this.render();
            console.log('Switched back to playlist view');
        }, 300);
    }

    /**
     * Send webhook with appropriate ID based on current view mode
     */
    async sendGoWebhook() {
        if (this.items.length === 0) return;
        
        let id;
        let itemName;
        
        // Get appropriate ID based on current mode
        if (this.viewMode === 'playlists' || this.viewMode === 'single') {
            // Send parent item ID
            const currentItem = this.playlistData[this.currentIndex] || this.items[this.currentIndex];
            if (!currentItem) return;
            
            id = currentItem.id;
            itemName = currentItem.name || currentItem[this.config.parentNameKey];
            console.log('Sending webhook for item:', itemName, 'ID:', id);
        } else if (this.viewMode === 'songs') {
            // Send child item ID
            const currentChild = this.selectedPlaylist[this.config.parentKey][this.currentIndex];
            if (!currentChild) return;
            
            id = currentChild.id;
            itemName = currentChild.name || currentChild.title;
            console.log('Sending webhook for child item:', itemName, 'ID:', id);
        } else {
            return;
        }
        
        // Send webhook to Home Assistant
        try {
            const webhookData = {
                device_type: "Panel",
                button: "go",
                panel_context: this.config.context,
                id: id,
                name: itemName
            };
            
            const response = await fetch(this.config.webhookUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(webhookData)
            });
            
            if (response.ok) {
                console.log('Webhook sent successfully:', webhookData);
            } else {
                console.error('Webhook failed with status:', response.status);
            }
        } catch (error) {
            console.error('Error sending webhook:', error);
        }
    }
}

// ===== INITIALIZE THE APPLICATION =====
// Wait for the page to fully load, then create the ArcList
document.addEventListener('DOMContentLoaded', () => {
    console.log('DOM loaded, initializing ArcList...'); // Debug log
    
    // Debug: Check for any existing elements that might cause conflicts
    const existingElements = document.querySelectorAll('[id*="arc"], [id*="volume"]');
    if (existingElements.length > 0) {
        console.log('Found existing elements that might conflict:', existingElements);
    }
    
    // Example configurations for different use cases:
    
    // 1. Music playlists (current setup)
    const musicConfig = {
        dataSource: '../playlists_with_tracks.json',
        dataType: 'playlists',
        viewMode: 'hierarchical',
        parentKey: 'tracks',
        parentNameKey: 'name',
        storagePrefix: 'music_arclist',
        title: 'Music',
        context: 'music',
        childNameMapper: (track) => ({
            id: track.id,
            name: `${track.artist} - ${track.name}`,
            image: track.image || 'https://via.placeholder.com/64x64/333333/ffffff?text=♪'
        })
    };
    
    // 2. Simple list (no hierarchy)
    const simpleConfig = {
        dataSource: '../simple_items.json',
        dataType: 'custom',
        viewMode: 'single',
        storagePrefix: 'simple_arclist',
        title: 'Simple List',
        context: 'simple'
    };
    
    // 3. Custom data with custom mapper
    const customConfig = {
        dataSource: '../custom_data.json',
        dataType: 'custom',
        itemMapper: (data) => data.map(item => ({
            id: item.customId,
            name: item.displayName,
            image: item.iconUrl
        })),
        viewMode: 'single',
        storagePrefix: 'custom_arclist',
        title: 'Custom Data',
        context: 'custom'
    };
    
    // Initialize with music configuration (current setup)
    new ArcList(musicConfig);
    
    // To use a different configuration, uncomment one of these:
    // new ArcList(simpleConfig);
    // new ArcList(customConfig);
});