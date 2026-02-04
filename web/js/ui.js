class UIStore {
    constructor() {
        this.wheelPointerAngle = 180;
        this.topWheelPosition = 0;
        this.isNowPlayingOverlayActive = false;
        this.selectedMenuItem = -1;
        
        // Initialize laser position from constants (matches cursor-handler.js)
        this.laserPosition = window.Constants?.laser?.defaultPosition || 93;
        
        // Debug info
        this.debugEnabled = true;
        this.debugVisible = false;
        this.wsMessages = [];
        this.maxWsMessages = 50;
        
        // Media info
        this.mediaInfo = {
            title: '—',
            artist: '—',
            album: '—',
            artwork: '',
            state: 'idle'
        };
        
        // SHOWING view media info (fetched from backend)
        this.appleTVMediaInfo = {
            title: '—',
            friendly_name: '—',
            app_name: '—',
            artwork: '',
            state: 'unknown'
        };
        
        // Artwork cache delegated to ArtworkManager
        
        // Menu items from centralized constants
        this.menuItems = (window.Constants?.menuItems || [
            {title: 'SHOWING', path: 'menu/showing'},
            {title: 'SYSTEM', path: 'menu/system'},
            {title: 'SECURITY', path: 'menu/security'},
            {title: 'SCENES', path: 'menu/scenes'},
            {title: 'MUSIC', path: 'menu/music'},
            {title: 'PLAYING', path: 'menu/playing'}
        ]).map(item => ({title: item.title, path: item.path}));

        // Get constants from centralized config
        const c = window.Constants || {};
        this.radius = c.arc?.radius || 1000;
        this.angleStep = c.arc?.menuAngleStep || 5;
        
        // Menu visibility state (simplified)
        this.menuVisible = true;
        
        // Initialize views first
        this.views = {
            'menu/showing': {
                title: 'SHOWING',
                content: `
                    <div id="status-page" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; color: white; text-align: center; background-color: rgba(0,0,0,0.4);">
                        <div id="apple-tv-artwork-container" style="width: 60%; aspect-ratio: 1; margin: 20px; position: relative; display: flex; justify-content: center; align-items: center; overflow: hidden; border-radius: 8px; box-shadow: 0 5px 15px rgba(0,0,0,0.3);">
                            <img id="apple-tv-artwork" src="" alt="Apple TV Media" style="width: 100%; height: 100%; object-fit: contain; transition: opacity 0.6s ease;">
                        </div>
                        <div id="apple-tv-media-info" style="width: 80%; padding: 10px;">
                            <div id="apple-tv-media-title" style="font-size: 24px; font-weight: bold; margin-bottom: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">—</div>
                            <div id="apple-tv-media-details" style="font-size: 18px; margin-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">—</div>
                            <div id="apple-tv-state">Unknown</div>
                        </div>
                    </div>`
            },
            'menu/music': {
                title: 'Playlists',
                content: `
                    <div id="music-container" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;">
                        <iframe id="music-iframe" src="softarc/music.html" style="width: 100%; height: 100%; border: none; border-radius: 8px; box-shadow: 0 5px 15px rgba(0,0,0,0.3);" allowfullscreen></iframe>
                    </div>
                `
            },
            'menu/system': {
                title: 'System',
                content: `
                    <div id="system-container" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;">
                        <iframe id="system-iframe" src="softarc/system.html" style="width: 100%; height: 100%; border: none; border-radius: 8px; box-shadow: 0 5px 15px rgba(0,0,0,0.3);" allowfullscreen></iframe>
                    </div>
                `
            },
            'menu/scenes': {
                title: 'Scenes',
                content: `
                    <div id="scenes-container" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;">
                        <iframe id="scenes-iframe" src="softarc/scenes.html" style="width: 100%; height: 100%; border: none; border-radius: 8px; box-shadow: 0 5px 15px rgba(0,0,0,0.3);" allowfullscreen></iframe>
                    </div>
                `
            },
            'menu/security': {
                title: 'SECURITY',
                content: `
                    <div id="security-container" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; pointer-events: none;">
                        <iframe id="security-iframe"
                                style="width: 100%; height: 100%; border: none; border-radius: 8px; box-shadow: 0 5px 15px rgba(0,0,0,0.3); pointer-events: auto;"
                                allowfullscreen
                                tabindex="0"></iframe>
                    </div>
                `
            },
            'menu/playing': {
                title: 'PLAYING',
                content: `
                    <div id="now-playing" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; color: white; text-align: center;">
                        <div id="artwork-container" style="width: 60%; aspect-ratio: 1; margin: 20px; position: relative; display: flex; justify-content: center; align-items: center; overflow: hidden; border-radius: 8px; box-shadow: 0 5px 15px rgba(0,0,0,0.3);">
                            <img id="now-playing-artwork" src="" alt="Album Art" style="width: 100%; height: 100%; object-fit: cover; transition: opacity 0.6s ease;">
                        </div>
                        <div id="media-info" style="width: 80%; padding: 10px;">
                            <div id="media-title" style="font-size: 24px; font-weight: bold; margin-bottom: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">—</div>
                            <div id="media-artist" style="font-size: 18px; margin-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">—</div>
                            <div id="media-album" style="font-size: 16px; opacity: 0.8; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">—</div>
                        </div>
                    </div>`
            },
        };

        // Set initial route
        this.currentRoute = 'menu/playing';
        this.currentView = null;

        // Initialize UI
        this.initializeUI();
        this.setupEventListeners();
        this.updateView();
        
        // Ensure menu starts visible
        setTimeout(() => {
            this.ensureMenuVisible();
        }, 100);
        
        // Media info will be received via WebSocket from media server

        // Set up Apple TV media info refresh for SHOWING view
        this.setupAppleTVMediaInfoRefresh();
    }
    
    // Image preloading delegated to ArtworkManager
    preloadAndCacheImage(url) {
        if (window.ArtworkManager) {
            return window.ArtworkManager.preloadImage(url);
        }
        return Promise.resolve(null);
    }
    
    // REMOVED: requestMediaUpdate - now using push-based updates from media server
    // Media server automatically pushes updates when:
    // 1. Client connects
    // 2. Track changes  
    // 3. External control detected
    
    // Handle media update from WebSocket
    handleMediaUpdate(data, reason = 'update') {
        // Only log the reason, not the full data object
        console.log(`[MEDIA-WS] ${reason}: ${data.title} - ${data.artist}`);
        
        // Update media info
        this.mediaInfo = {
            title: data.title || '—',
            artist: data.artist || '—',
            album: data.album || '—',
            artwork: data.artwork || '',
            state: data.state || 'unknown',
            position: data.position || '0:00',
            duration: data.duration || '0:00'
        };
        
        // Update the now playing view if it's active
        if (this.currentRoute === 'menu/playing') {
            this.updateNowPlayingView();
        }
    }
    
    // Update the now playing view with current media info
    updateNowPlayingView() {
        const artworkEl = document.getElementById('now-playing-artwork');
        const titleEl = document.getElementById('media-title');
        const artistEl = document.getElementById('media-artist');
        const albumEl = document.getElementById('media-album');
        const playPauseBtn = document.getElementById('play-pause');

        if (!titleEl || !artistEl || !albumEl) return;

        // Update text elements
        titleEl.textContent = this.mediaInfo.title;
        artistEl.textContent = this.mediaInfo.artist;
        albumEl.textContent = this.mediaInfo.album;

        // Update play/pause button based on state
        if (playPauseBtn) {
            playPauseBtn.textContent = this.mediaInfo.state === 'playing' ? '⏸' : '▶️';
        }

        // Use centralized artwork manager for display
        if (artworkEl && window.ArtworkManager) {
            window.ArtworkManager.displayArtwork(artworkEl, this.mediaInfo.artwork, 'noArtwork');
        }
    }

    // Fetch Apple TV media info from backend (which proxies HA)
    async fetchAppleTVMediaInfo() {
        try {
            const response = await fetch('http://localhost:8767/appletv');
            if (!response.ok) return;

            const data = await response.json();
            this.appleTVMediaInfo = {
                title: data.title || '—',
                friendly_name: data.friendly_name || '—',
                app_name: data.app_name || '—',
                artwork: data.artwork || '',
                state: data.state
            };

            if (this.currentRoute === 'menu/showing') {
                this.updateAppleTVMediaView();
            }
        } catch (error) {
            console.error('Error fetching Apple TV info:', error);
        }
    }

    // Update the SHOWING view with Apple TV media info
    updateAppleTVMediaView() {
        const artworkEl = document.getElementById('apple-tv-artwork');
        const titleEl = document.getElementById('apple-tv-media-title');
        const detailsEl = document.getElementById('apple-tv-media-details');
        const stateEl = document.getElementById('apple-tv-state');

        if (titleEl) titleEl.textContent = this.appleTVMediaInfo.title;
        if (detailsEl) detailsEl.textContent = this.appleTVMediaInfo.app_name;
        if (stateEl) stateEl.textContent = this.appleTVMediaInfo.state;

        // Use centralized artwork manager for display
        if (artworkEl && window.ArtworkManager) {
            window.ArtworkManager.displayArtwork(artworkEl, this.appleTVMediaInfo.artwork, 'showing');
        }
    }

    // Set up periodic refresh for Apple TV media info
    setupAppleTVMediaInfoRefresh() {
        // Fetch immediately
        this.fetchAppleTVMediaInfo();

        // Then refresh every 5 seconds
        setInterval(() => {
            this.fetchAppleTVMediaInfo();
        }, 5000);
    }

    // Update the SHOWING view (called when navigating to view)
    updateShowingView() {
        // Use cached info immediately, fetch will update async
        this.updateAppleTVMediaView();
        // Also trigger a fresh fetch
        this.fetchAppleTVMediaInfo();
    }
    
    // Initialize UI
    initializeUI() {
        // Draw initial arcs
        const mainArc = document.getElementById('mainArc');
        mainArc.setAttribute('d', arcs.drawArc(arcs.cx, arcs.cy, this.radius, 158, 202));

        // Volume arc removed - no longer needed
        // const volumeArc = document.getElementById('volumeArc');
        // this.updateVolumeArc();

        // Setup menu items
        this.renderMenuItems();
        this.updatePointer();
    }


    updatePointer() {
        const pointerDot = document.getElementById('pointerDot');
        const pointerLine = document.getElementById('pointerLine');
        const mainMenu = document.getElementById('mainMenu');
        
        const point = arcs.getArcPoint(this.radius, 0, this.wheelPointerAngle);
        const transform = `rotate(${this.wheelPointerAngle - 90}deg)`;
        
        [pointerDot, pointerLine].forEach(element => {
            element.setAttribute('cx', point.x);
            element.setAttribute('cy', point.y);
            element.style.transformOrigin = `${point.x}px ${point.y}px`;
            element.style.transform = transform;
        });

        // Toggle slide-out class based on angle range
        if (mainMenu) {
            if (this.wheelPointerAngle > 203 || this.wheelPointerAngle < 155) {
                mainMenu.classList.add('slide-out');
            } else {
                mainMenu.classList.remove('slide-out');
            }
        }
    }

    renderMenuItems() {
        const menuContainer = document.getElementById('menuItems');
        menuContainer.innerHTML = '';
        
        this.menuItems.forEach((item, index) => {
            const itemElement = document.createElement('div');
            itemElement.className = 'list-item';
            itemElement.textContent = item.title;
            
            const itemAngle = this.getStartItemAngle() + index * this.angleStep;
            const position = arcs.getArcPoint(this.radius, 20, itemAngle);
            
            Object.assign(itemElement.style, {
                position: 'absolute',
                left: `${position.x - 100}px`,
                top: `${position.y - 25}px`,
                width: '100px',
                height: '50px',
                cursor: 'pointer'
            });

            itemElement.addEventListener('mouseenter', () => {
                // Always update pointer angle and check selection (isSelectedItem has its own overlay logic)
                console.log(`[HOVER DEBUG] Mouse entered item ${index} (${item.title}) - setting angle to ${itemAngle}`);
                this.wheelPointerAngle = itemAngle;
                // Legacy method removed - using laser position system
                this.handleWheelChange();
            });

            // Check if this item should be selected based on laser position
            if (this.isSelectedItemForLaserPosition(index)) {
                itemElement.classList.add('selectedItem');
            }

            menuContainer.appendChild(itemElement);
        });
    }

    getStartItemAngle() {
        const totalSpan = this.angleStep * (this.menuItems.length - 1);
        return 180 - totalSpan / 2;
    }

    
    isSelectedItemForLaserPosition(index) {
        // Use laser position mapper to determine if this menu item should be highlighted
        if (!this.laserPosition || !window.LaserPositionMapper) {
            return false;
        }
        
        const { getViewForLaserPosition } = window.LaserPositionMapper;
        const viewInfo = getViewForLaserPosition(this.laserPosition);
        
        // Only highlight if we're in a menu view (not overlay) and this is the selected item
        if (viewInfo.isOverlay) {
            return false;
        }
        
        // Check if this menu item matches the current view
        const expectedPath = this.menuItems[index].path;
        return viewInfo.path === expectedPath;
    }
    
    updateMenuHighlighting() {
        // Efficiently update menu item highlighting without recreating DOM elements
        const menuContainer = document.getElementById('menuItems');
        if (!menuContainer) return;
        
        const menuItems = menuContainer.querySelectorAll('.list-item');
        
        menuItems.forEach((itemElement, index) => {
            if (this.isSelectedItemForLaserPosition(index)) {
                itemElement.classList.add('selectedItem');
            } else {
                itemElement.classList.remove('selectedItem');
            }
        });
    }



    // Send click command to server (graceful fallback)
    sendClickCommand() {
        try {
            const ws = new WebSocket(AppConfig.websocket.input);
            
            const timeout = setTimeout(() => {
                ws.close();
            }, 1000); // 1 second timeout
            
            ws.onopen = () => {
                clearTimeout(timeout);
                const message = {
                    type: 'command',
                    command: 'click',
                    params: {}
                };
                ws.send(JSON.stringify(message));
                console.log('Sent click command to server');
                ws.close();
            };
            
            ws.onerror = () => {
                clearTimeout(timeout);
                // Silently fail - main server not available (standalone mode)
            };
            
            ws.onclose = () => {
                clearTimeout(timeout);
            };
        } catch (error) {
            // Silently fail - main server not available (standalone mode)
        }
    }

    setupEventListeners() {
        document.addEventListener('keydown', (event) => {
            switch (event.key) {
                case "ArrowUp":
                    this.topWheelPosition = -1;
                    this.handleWheelChange();
                    break;
                case "ArrowDown":
                    this.topWheelPosition = 1;
                    this.handleWheelChange();
                    break;
                case "ArrowLeft":
                    if (this.currentRoute === 'menu/playing') {
                        // Webhook handled by dummy hardware system
                    } else {
                        // Forward left button to active iframe for hierarchical navigation
                        this.forwardButtonToActiveIframe('left');
                        // Also forward keyboard event for iframe handling
                        this.forwardKeyboardToActiveIframe(event);
                    }
                    break;
                case "ArrowRight":
                    if (this.currentRoute === 'menu/playing') {
                        // Webhook handled by dummy hardware system
                    } else {
                        // Forward right button to active iframe for hierarchical navigation
                        this.forwardButtonToActiveIframe('right');
                        // Also forward keyboard event for iframe handling
                        this.forwardKeyboardToActiveIframe(event);
                    }
                    break;
                case "Enter":
                    // Forward Enter key to active iframe for "go" functionality
                    if (this.currentRoute !== 'menu/playing') {
                        this.forwardKeyboardToActiveIframe(event);
                    }
                    break;
            }
        });

        document.addEventListener('mousemove', (event) => {
            const mainMenu = document.getElementById('mainMenu');
            if (!mainMenu) return;

            const rect = mainMenu.getBoundingClientRect();
            const centerX = arcs.cx - rect.left;
            const centerY = arcs.cy - rect.top;
            
            const dx = event.clientX - rect.left - centerX;
            const dy = event.clientY - rect.top - centerY;
            let angle = Math.atan2(dy, dx) * 180 / Math.PI + 90;
            if (angle < 0) angle += 360;

            if ((angle >= 158 && angle <= 202) || 
                (angle >= 0 && angle <= 30) ||
                (angle >= 330 && angle <= 360)) {
                this.wheelPointerAngle = angle;
                this.handleWheelChange();
            }
        });

        // Volume wheel handling removed - wheel events now ONLY control laser pointer
        // Volume can be controlled via left/right arrow keys when not in now playing view

        document.getElementById('menuItems').addEventListener('click', (event) => {
            const clickedItem = event.target.closest('.list-item');
            if (!clickedItem) return;

            const index = Array.from(clickedItem.parentElement.children).indexOf(clickedItem);
            const itemAngle = this.getStartItemAngle() + index * this.angleStep;
            this.wheelPointerAngle = itemAngle;
            // Legacy method removed - using laser position system
            this.handleWheelChange();
            
            // Send click command to server
            this.sendClickCommand();
        });
    }

    handleWheelChange() {
        // Ensure wheelPointerAngle is within valid bounds (150-210)
        const oldAngle = this.wheelPointerAngle;
        this.wheelPointerAngle = Math.max(150, Math.min(210, this.wheelPointerAngle));
        
        // Debug logging for fast scrolling
        if (Math.abs(oldAngle - this.wheelPointerAngle) > 5) {
            console.log(`[DEBUG] Fast scroll detected: ${oldAngle.toFixed(1)} -> ${this.wheelPointerAngle.toFixed(1)}`);
        }
        
        // Navigation wheel should NOT affect laser position - it's for softarc navigation within views
        // topWheelPosition is handled by iframe forwarding in cursor-handler.js
        if (this.topWheelPosition !== 0) {
            console.log(`[DEBUG] Navigation wheel: ${this.topWheelPosition > 0 ? 'clockwise' : 'counterclockwise'} (topWheelPosition: ${this.topWheelPosition})`);
            // Navigation wheel events are forwarded to iframe pages by cursor-handler.js
            // They should NOT modify the laser position - that's the laser pointer's job
        }
        
        // Laser position system is now the only supported method
        if (!this.laserPosition || !window.LaserPositionMapper) {
            console.error('[UI] Laser position system required but not available');
            return;
        }
        
        this.handleWheelChangeWithMapper();

        this.updatePointer();
        this.renderMenuItems();
        this.topWheelPosition = 0;
    }
    
    handleWheelChangeWithMapper() {
        const { getViewForLaserPosition } = window.LaserPositionMapper;
        
        // Get view mapping from laser position
        const viewInfo = getViewForLaserPosition(this.laserPosition);
        
        // Ensure we have valid view info
        if (!viewInfo || !viewInfo.path) {
            console.error(`[DEBUG] Invalid view info for position ${this.laserPosition}:`, viewInfo);
            return;
        }
        
        console.log(`[DEBUG] Laser position ${this.laserPosition} -> ${viewInfo.path} (${viewInfo.reason})`);
        
        // Handle menu visibility based on whether we're in an overlay
        if (viewInfo.isOverlay) {
            // Should hide menu
            if (this.menuVisible) {
                this.setMenuVisible(false);
            }
        } else {
            // Should show menu
            if (!this.menuVisible) {
                this.setMenuVisible(true);
            }
        }
        
        // DETERMINISTIC NAVIGATION: Position always determines view
        // Only navigate if the view actually changed (prevents flicker)
        const viewChanged = this.currentRoute !== viewInfo.path;
        
        console.log(`[DEBUG] Position ${this.laserPosition} -> ${viewInfo.path} (${viewInfo.reason}) ${viewChanged ? '[NAVIGATE]' : '[SAME]'}`);
        
        if (viewChanged) {
            this.navigateToView(viewInfo.path);
        }
        
        // Update state AFTER navigation (not before) to track current position
        if (viewInfo.isOverlay) {
            this.isNowPlayingOverlayActive = true;
        } else {
            // Not in overlay zone
            this.isNowPlayingOverlayActive = false;
            
            // Update selected menu item state
            if (viewInfo.menuItem) {
                this.selectedMenuItem = viewInfo.menuItem.index;
            }
        }

        // Click feedback when navigating to a new page (not on every laser movement)
        if (viewChanged) {
            console.log(`[CLICK] View changed to ${viewInfo.path} - sending click`);
            this.sendClickCommand();
        }

        // Update menu highlighting to reflect current laser position
        this.updateMenuHighlighting();
    }
    

    // Simplified menu visibility control
    setMenuVisible(visible) {
        if (this.menuVisible === visible) return; // No change needed
        
        this.menuVisible = visible;
        
        // Get menu elements
        const menuElements = this.getMenuElements();
        if (menuElements.length === 0) {
            console.warn('No menu elements found for visibility control');
            return;
        }
        
        // Update visibility immediately
        menuElements.forEach(element => {
            element.style.transition = 'none';
            element.style.display = visible ? 'block' : 'none';
            element.style.opacity = visible ? '1' : '0';
            element.style.transform = 'translateX(0px)';
        });
        
        // Ensure content stays visible
        this.ensureContentVisible();
    }
    
    // Get menu elements for animation
    getMenuElements() {
        const menuItems = document.getElementById('menuItems');
        const mainArc = document.querySelector('#mainMenu svg');
        const anglePointer = document.getElementById('anglePointer');
        return [menuItems, mainArc, anglePointer].filter(el => el);
    }
    
    // Ensure content area stays visible during all animations
    ensureContentVisible() {
        const contentArea = document.getElementById('contentArea');
        if (contentArea) {
            // Only ensure visibility and position, don't interfere with opacity transitions
            // that might be happening for artwork or other content
            contentArea.style.transform = 'translateX(0px)';
            contentArea.style.visibility = 'visible';
            
            // Don't force opacity to 1 or remove transitions as this can interfere
            // with artwork fade-in/fade-out animations
            
            // Force a reflow to ensure styles are applied
            contentArea.offsetHeight;
        }
    }
    
    

    navigateToView(path) {
        // Update route immediately to prevent repeated navigation triggers
        this.currentRoute = path;

        // For overlay transitions, update immediately to prevent content hiding
        const isOverlayTransition = path === 'menu/playing' || path === 'menu/showing';

        if (isOverlayTransition) {
            // Overlay transitions: update immediately and ensure content stays visible
            this.updateView();
            this.ensureContentVisible(); // Force content to stay visible
        } else {
            // Regular menu navigation: use fade transition
            const contentArea = document.getElementById('contentArea');
            if (contentArea) {
                contentArea.style.opacity = 0;
                setTimeout(() => {
                    this.updateView();
                }, 250);
            } else {
                this.updateView();
            }
        }
    }

    updateView() {
        
        const contentArea = document.getElementById('contentArea');
        if (!contentArea) {
            console.error('Content area not found');
            return;
        }

        const view = this.views[this.currentRoute];
        if (!view) {
            console.error('View not found for route:', this.currentRoute);
            // Fallback to playing view if route not found
            this.currentRoute = 'menu/playing';
            this.updateView();
            return;
        }

        // Update content while it's faded out
        contentArea.innerHTML = view.content;
        
        // Immediately update with cached info for playing view
        if (this.currentRoute === 'menu/playing') {
            this.updateNowPlayingView();
            // Media info will be pushed automatically by media server
        }
        // Update SHOWING view with static fallback
        else if (this.currentRoute === 'menu/showing') {
            this.updateShowingView();
        }
        
        // If navigating to security view, set up the iframe
        if (this.currentRoute === 'menu/security') {
            const securityIframe = document.getElementById('security-iframe');
            const securityContainer = document.getElementById('security-container');
            const mainMenu = document.getElementById('mainMenu');
            
            if (securityIframe) {
                // Set the iframe source to the Home Assistant camera dashboard
                // Configured in web/js/config.js
                const haUrl = window.AppConfig?.homeAssistant?.url || 'http://homeassistant.local:8123';
                const securityDashboard = window.AppConfig?.homeAssistant?.securityDashboard;

                if (securityDashboard) {
                    securityIframe.src = `${haUrl}/${securityDashboard}&kiosk`;
                } else {
                    console.log('No security dashboard configured');
                }
                
                // Make iframe fully interactive
                securityIframe.style.pointerEvents = 'auto';
                securityIframe.style.zIndex = '1000';
                securityIframe.style.position = 'relative';
                securityIframe.setAttribute('tabindex', '0');
                
                // Ensure all parent containers don't interfere with clicks
                if (securityContainer) {
                    securityContainer.style.pointerEvents = 'none';
                }
                if (contentArea) {
                    contentArea.style.pointerEvents = 'none';
                }
                if (mainMenu) {
                    mainMenu.style.pointerEvents = 'none';
                }
                
                // Add a loading indicator if needed
                securityIframe.onload = () => {
                    securityIframe.classList.add('loaded');
                    console.log('Security iframe loaded successfully');
                    // Give the iframe focus so it can receive keyboard input
                    setTimeout(() => {
                        securityIframe.focus();
                        console.log('Security iframe focused');
                    }, 200);
                };
                
                securityIframe.onerror = (error) => {
                    console.error('Error loading security camera dashboard:', error);
                };
                
                // Force iframe to be interactive
                setTimeout(() => {
                    securityIframe.style.pointerEvents = 'auto';
                    securityIframe.style.isolation = 'isolate';
                    console.log('Security iframe pointer events enabled');
                }, 100);
            }
        } else {
            // Reset pointer events for other views
            const mainMenu = document.getElementById('mainMenu');
            if (contentArea) {
                contentArea.style.pointerEvents = 'auto';
            }
            if (mainMenu) {
                mainMenu.style.pointerEvents = 'auto';
            }
        }
        
        this.setupContentScroll();
        
        // Fade the content back in (but not for overlay transitions where we want it always visible)
        const isOverlayView = this.currentRoute === 'menu/playing' || this.currentRoute === 'menu/showing';
        if (isOverlayView) {
            // For overlay views, ensure content is immediately visible
            contentArea.style.opacity = 1;
        } else {
            // For regular navigation, fade back in
            setTimeout(() => {
                contentArea.style.opacity = 1;
            }, 50);
        }
    }

    setupContentScroll() {
        const flowContainer = document.querySelector('.arc-content-flow');
        if (!flowContainer) return;

        let scrollPosition = 0;
        const angleStep = 10;
        const radius = 300;

        // Add visual indicator for scrolling
        const scrollIndicator = document.createElement('div');
        scrollIndicator.className = 'scroll-indicator';
        scrollIndicator.innerHTML = '<span>Scroll with wheel</span>';
        scrollIndicator.style.cssText = 'position: absolute; bottom: 15px; right: 15px; background: rgba(0,0,0,0.5); color: white; padding: 5px 10px; border-radius: 4px; font-size: 12px; opacity: 0.7; pointer-events: none; transition: opacity 0.3s ease;';
        flowContainer.appendChild(scrollIndicator);
        
        // Fade out the indicator after a few seconds
        setTimeout(() => {
            scrollIndicator.style.opacity = '0';
        }, 3000);

        const updateFlowItems = () => {
            const items = document.querySelectorAll('.flow-item');
            items.forEach((item, index) => {
                const itemAngle = 180 + (index * angleStep) - scrollPosition;
                const position = arcs.getArcPoint(radius, 20, itemAngle);
                
                Object.assign(item.style, {
                    position: 'absolute',
                    left: `${position.x - 200}px`,
                    top: `${position.y - 25}px`,
                    opacity: Math.abs(itemAngle - 180) < 20 ? 1 : 0.5,
                    transform: `scale(${Math.abs(itemAngle - 180) < 20 ? 1 : 0.9})`,
                    fontWeight: Math.abs(itemAngle - 180) < 2 ? 'bold' : 'normal'
                });
            });
        };

        // Handle wheel events for content scrolling
        flowContainer.addEventListener('wheel', (event) => {
            // Show scroll indicator briefly when user uses mouse wheel
            scrollIndicator.style.opacity = '0.7';
            setTimeout(() => {
                scrollIndicator.style.opacity = '0';
            }, 1500);
            
            event.preventDefault();
            const totalItems = document.querySelectorAll('.flow-item').length;
            const maxScroll = (totalItems - 1) * angleStep;
            
            if (event.deltaY > 0 && scrollPosition < maxScroll) {
                scrollPosition += angleStep;
            } else if (event.deltaY < 0 && scrollPosition > 0) {
                scrollPosition -= angleStep;
            }
            
            updateFlowItems();
        });

        // Initial position
        updateFlowItems();
    }

    // Set the current laser position
    setLaserPosition(position) {
        this.laserPosition = position;
    }
    
    /**
     * Forward button press to active iframe for hierarchical navigation
     */
    forwardButtonToActiveIframe(button) {
        if (window.IframeMessenger) {
            window.IframeMessenger.sendButtonEvent(this.currentRoute, button);
        }
    }

    /**
     * Forward keyboard event to active iframe for enhanced navigation
     */
    forwardKeyboardToActiveIframe(event) {
        if (window.IframeMessenger) {
            window.IframeMessenger.sendKeyboardEvent(this.currentRoute, event);
        }
    }
}

// Initialize UIStore and make functions globally accessible
document.addEventListener('DOMContentLoaded', () => {
    // Create the UI store and make it globally accessible
    const uiStore = new UIStore();
    window.uiStore = uiStore;
    
    // Make the sendClickCommand function globally accessible
    window.sendClickCommand = () => {
        if (window.uiStore) {
            window.uiStore.sendClickCommand();
        } else {
            console.error('UIStore not initialized yet');
        }
    };

    // Fade out splash screen after UI is ready
    const timeouts = window.Constants?.timeouts || {};
    setTimeout(() => {
        const splash = document.getElementById('splash-overlay');
        if (splash) {
            splash.classList.add('fade-out');
            // Remove from DOM after animation completes
            setTimeout(() => {
                splash.classList.add('hidden');
            }, timeouts.splashRemoveDelay || 800);
        }
    }, timeouts.splashFadeDelay || 500); // Brief delay to ensure UI is rendered
}); 
