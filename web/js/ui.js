class UIStore {
    constructor() {
        this.volume = 50;
        this.wheelPointerAngle = 180;
        this.topWheelPosition = 0;
        this.isNowPlayingOverlayActive = false;
        this.selectedMenuItem = -1;
        
        // Initialize laser position to 93 (matches cursor-handler.js)
        this.laserPosition = 93;
        
        // Debug info
        this.debugEnabled = true;
        this.debugVisible = false;
        this.wsMessages = [];
        this.maxWsMessages = 50;
        
        // HA integration settings - ONLY for Apple TV display data fetching (read-only)
        this.HA_URL = 'http://homeassistant.local:8123';
        this.HA_TOKEN = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNTU1MjM0NmIzMTA0NTQxOWU4ZjczYmM3YjE4YzNiOSIsImlhdCI6MTc0NjA5ODMxMiwiZXhwIjoyMDYxNDU4MzEyfQ.ZDszs4w_8_bkcIy24cvwntEsyjCzy2VODjthZRpQvaQ';
        
        // Media info
        this.mediaInfo = {
            title: '—',
            artist: '—',
            album: '—',
            artwork: '',
            state: 'idle'
        };
        
        // Apple TV media info
        this.appleTVMediaInfo = {
            title: '—',
            artist: '—',
            album: '—',
            artwork: '',
            state: 'unknown'
        };
        
        // In-memory artwork cache
        this.artworkCache = {};
        
        this.menuItems = [
            {title: 'SHOWING', path: 'menu/showing'},
            {title: 'SETTINGS', path: 'menu/settings'},
            {title: 'SECURITY', path: 'menu/security'},
            {title: 'SCENES', path: 'menu/scenes'},
            {title: 'MUSIC', path: 'menu/music'},
            {title: 'PLAYING', path: 'menu/playing'}
        ];

        // Constants
        this.radius = 1000;
        this.angleStep = 5;
        
        // Menu animation state
        this.menuAnimationState = 'visible'; // 'visible', 'sliding-out', 'hidden', 'sliding-in'
        this.menuAnimationTimeout = null;
        
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
                            <div id="apple-tv-state">Unknown</span></div>
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
            'menu/settings': {
                title: 'Settings',
                content: `
                    <div id="settings-container" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;">
                        <iframe id="settings-iframe" src="softarc/settings.html" style="width: 100%; height: 100%; border: none; border-radius: 8px; box-shadow: 0 5px 15px rgba(0,0,0,0.3);" allowfullscreen></iframe>
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
                                tabindex="0"
                                sandbox="allow-same-origin allow-scripts allow-forms allow-pointer-lock allow-popups allow-popups-to-escape-sandbox"
                                allow="camera; microphone; geolocation"></iframe>
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
            'menu/nowshowing': {
                title: 'NOW SHOWING',
                content: `
                    <div id="status-page" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; color: white; text-align: center; background-color: rgba(0,0,0,0.4);">
                        <div id="apple-tv-artwork-container" style="width: 60%; aspect-ratio: 1; margin: 20px; position: relative; display: flex; justify-content: center; align-items: center; overflow: hidden; border-radius: 8px; box-shadow: 0 5px 15px rgba(0,0,0,0.3);">
                            <img id="apple-tv-artwork" src="" alt="Apple TV Media" style="width: 100%; height: 100%; object-fit: contain; transition: opacity 0.6s ease;">
                        </div>
                        <div id="apple-tv-media-info" style="width: 80%; padding: 10px;">
                            <div id="apple-tv-media-title" style="font-size: 24px; font-weight: bold; margin-bottom: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">—</div>
                            <div id="apple-tv-media-details" style="font-size: 18px; margin-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">—</div>
                            <div id="apple-tv-state">Unknown</span></div>
                        </div>
                    </div>`
            }
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
        
        // Start fetching Apple TV media info
        this.setupAppleTVMediaInfoRefresh();
    }
    
    // Helper to preload and cache images with better error handling
    preloadAndCacheImage(url) {
        return new Promise((resolve, reject) => {
            if (!url) return resolve(null);
            if (this.artworkCache[url] && this.artworkCache[url].complete) {
                return resolve(this.artworkCache[url]);
            }
            
            // First check if the URL returns any data
            fetch(url, { headers: { 'Authorization': 'Bearer ' + this.HA_TOKEN } })
                .then(response => {
                    if (!response.ok) {
                        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                    }
                    return response.blob();
                })
                .then(blob => {
                    if (blob.size === 0) {
                        throw new Error('Artwork URL returned 0 bytes (HA proxy issue)');
                    }
                    
                    // Create object URL from blob and load image
                    const objectUrl = URL.createObjectURL(blob);
                    const img = new window.Image();
                    img.onload = () => {
                        this.artworkCache[url] = img;
                        resolve(img);
                        // Clean up object URL after loading
                        URL.revokeObjectURL(objectUrl);
                    };
                    img.onerror = () => {
                        URL.revokeObjectURL(objectUrl);
                        reject(new Error('Failed to load image from blob'));
                    };
                    img.src = objectUrl;
                })
                .catch(error => {
                    console.warn(`Artwork loading failed for ${url}:`, error.message);
                    reject(error);
                });
        });
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
        
        if (!artworkEl || !titleEl || !artistEl || !albumEl) return;
        
        // Update text elements
        titleEl.textContent = this.mediaInfo.title;
        artistEl.textContent = this.mediaInfo.artist;
        albumEl.textContent = this.mediaInfo.album;
        
        // Update play/pause button based on state
        if (playPauseBtn) {
            playPauseBtn.textContent = this.mediaInfo.state === 'playing' ? '⏸' : '▶️';
        }
        
        // Handle artwork display
        const artworkUrl = this.mediaInfo.artwork;
        
        if (artworkUrl) {
            // Check if it's a data URL (from direct Sonos API)
            if (artworkUrl.startsWith('data:')) {
                // Direct data URL - set immediately
                if (artworkEl.src !== artworkUrl) {
                    artworkEl.style.opacity = 0;
                    setTimeout(() => {
                        artworkEl.src = artworkUrl;
                        setTimeout(() => { artworkEl.style.opacity = 1; }, 20);
                    }, 100);
                }
            } else {
                // Regular URL (from HA) - use caching system
                if (this.artworkCache[artworkUrl] && this.artworkCache[artworkUrl].complete) {
                    if (artworkEl.src !== this.artworkCache[artworkUrl].src) {
                        artworkEl.style.opacity = 0;
                        setTimeout(() => {
                            artworkEl.src = this.artworkCache[artworkUrl].src;
                            setTimeout(() => { artworkEl.style.opacity = 1; }, 20);
                        }, 100);
                    }
                } else {
                    // Preload and cache for next time
                    this.preloadAndCacheImage(artworkUrl).then(img => {
                        if (img && artworkEl.src !== img.src) {
                            artworkEl.style.opacity = 0;
                            setTimeout(() => {
                                artworkEl.src = img.src;
                                setTimeout(() => { artworkEl.style.opacity = 1; }, 20);
                            }, 100);
                        }
                    }).catch(error => {
                        console.error('Error loading now playing artwork:', error.message);
                        if (error.message.includes('0 bytes')) {
                            console.warn('Home Assistant media player proxy returned 0 bytes - this is a known issue with Sonos artwork URLs');
                        }
                        // Set a default placeholder image
                        artworkEl.src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Crect width='200' height='200' fill='%23333'/%3E%3Ctext x='100' y='100' font-family='Arial' font-size='14' fill='%23999' text-anchor='middle' dominant-baseline='middle'%3EArtwork%3C/text%3E%3Ctext x='100' y='120' font-family='Arial' font-size='14' fill='%23999' text-anchor='middle' dominant-baseline='middle'%3EUnavailable%3C/text%3E%3C/svg%3E";
                        artworkEl.style.opacity = 1;
                    });
                }
            }
        } else {
            // Show placeholder when no artwork URL is available
            artworkEl.src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Crect width='200' height='200' fill='%23333'/%3E%3Ctext x='100' y='100' font-family='Arial' font-size='14' fill='%23999' text-anchor='middle' dominant-baseline='middle'%3ENo Artwork%3C/text%3E%3Ctext x='100' y='120' font-family='Arial' font-size='14' fill='%23999' text-anchor='middle' dominant-baseline='middle'%3EAvailable%3C/text%3E%3C/svg%3E";
            artworkEl.style.opacity = 1;
        }
    }
    
    // Fetch Apple TV media information from Home Assistant
    async fetchAppleTVMediaInfo() {
        // Removed fetch logging
        try {
            const response = await fetch(`${this.HA_URL}/api/states/media_player.loft_apple_tv`, {
                headers: { 'Authorization': 'Bearer ' + this.HA_TOKEN }
            });
            
            if (!response.ok) {
                console.error(`Error fetching Apple TV data: ${response.status} ${response.statusText}`);
                return;
            }
            
            const data = await response.json();
            // Removed data received logging
            
            const artworkUrl = data.attributes.entity_picture ? this.HA_URL + data.attributes.entity_picture : '';
            
            // Preload and cache artwork
            if (artworkUrl) this.preloadAndCacheImage(artworkUrl);
            
            // Store the Apple TV media info
            this.appleTVMediaInfo = {
                title: data.attributes.media_title || '—',
                friendly_name: data.attributes.friendly_name || '—',
                app_name: data.attributes.app_name || '—',
                artwork: artworkUrl,
                state: data.state
            };
            
            // Removed processed info logging
            
            // Update the Apple TV media view if it's active
            if (this.currentRoute === 'menu/showing') {
                this.updateAppleTVMediaView();
            }
            // Removed route logging
        } catch (error) {
            console.error('Error fetching Apple TV media info:', error);
        }
    }
    
    // Update the Apple TV media view with current info
    updateAppleTVMediaView() {
        // Removed view update logging
        const artworkEl = document.getElementById('apple-tv-artwork');
        const titleEl = document.getElementById('apple-tv-media-title');
        const detailsEl = document.getElementById('apple-tv-media-details');
        const stateEl = document.getElementById('apple-tv-state');
        
        if (!artworkEl) {
            console.error("Artwork element not found");
            return;
        }
        if (!titleEl || !detailsEl) {
            console.error("Media info elements not found");
            return;
        }
        
        if (!this.appleTVMediaInfo) {
            console.error("No Apple TV media info available");
            return;
        }
        
        // Update text elements
        if (titleEl) titleEl.textContent = this.appleTVMediaInfo.title || '—';
        if (detailsEl) detailsEl.textContent = this.appleTVMediaInfo.app_name + " showing on " + this.appleTVMediaInfo.friendly_name || '—';
        if (stateEl) stateEl.textContent = this.appleTVMediaInfo.state || 'Unknown';
        
        // Use cached image if available and loaded
        const artworkUrl = this.appleTVMediaInfo.artwork;
        if (artworkUrl && this.artworkCache[artworkUrl] && this.artworkCache[artworkUrl].complete) {
            if (artworkEl.src !== this.artworkCache[artworkUrl].src) {
                artworkEl.style.opacity = 0;
                setTimeout(() => {
                    artworkEl.src = this.artworkCache[artworkUrl].src;
                    setTimeout(() => { artworkEl.style.opacity = 1; }, 20);
                }, 100);
            }
        } else if (artworkUrl) {
            // Preload and cache for next time
            this.preloadAndCacheImage(artworkUrl).then(img => {
                if (img && artworkEl.src !== img.src) {
                    artworkEl.style.opacity = 0;
                    setTimeout(() => {
                        artworkEl.src = img.src;
                        setTimeout(() => { artworkEl.style.opacity = 1; }, 20);
                    }, 100);
                }
            }).catch(error => {
                console.error('Error loading Apple TV artwork:', error.message);
                if (error.message.includes('0 bytes')) {
                    console.warn('Home Assistant media player proxy returned 0 bytes for Apple TV artwork');
                }
                // Set a default placeholder image
                artworkEl.src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Crect width='200' height='200' fill='%23222'/%3E%3Ctext x='100' y='100' font-family='Arial' font-size='14' fill='%23999' text-anchor='middle' dominant-baseline='middle'%3EArtwork%3C/text%3E%3Ctext x='100' y='120' font-family='Arial' font-size='14' fill='%23999' text-anchor='middle' dominant-baseline='middle'%3EUnavailable%3C/text%3E%3C/svg%3E";
                artworkEl.style.opacity = 1;
            });
        } else {
            // Show a placeholder if no artwork
            artworkEl.src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Crect width='200' height='200' fill='%23222'/%3E%3Ctext x='100' y='100' font-family='Arial' font-size='20' fill='%23999' text-anchor='middle' dominant-baseline='middle'%3ENo Artwork%3C/text%3E%3C/svg%3E";
            artworkEl.style.opacity = 1;
        }
    }
    
    // Set up periodic refresh of Apple TV media info
    setupAppleTVMediaInfoRefresh() {
        console.log("Setting up Apple TV media refresh");
        // Initial fetch
        this.fetchAppleTVMediaInfo();
        
        // Refresh every 5 seconds
        setInterval(() => {
            // Removed periodic refresh logging
            this.fetchAppleTVMediaInfo();
        }, 5000);
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

    updateVolumeArc() {
        // Volume arc removed - this function is now a no-op
        const volumeArc = document.getElementById('volumeArc');
        if (!volumeArc) {
            // Element doesn't exist, just return without error
            return;
        }
        
        // If the element exists, update it (for backward compatibility)
        const startAngle = 95;
        const endAngle = 265;
        const volumeAngle = ((this.volume - 0) * (endAngle - startAngle)) / (100 - 0) + startAngle;
        volumeArc.setAttribute('d', arcs.drawArc(arcs.cx, arcs.cy, 270, startAngle, volumeAngle));
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
                this.isSelectedItem(index);
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

    isSelectedItem(index) {
        const itemAngle = this.getStartItemAngle() + index * this.angleStep;
        const isSelected = Math.abs(this.wheelPointerAngle - itemAngle) <= 2;
        
        // Debug logging
        if (isSelected) {
            console.log(`[MENU DEBUG] Item ${index} (${this.menuItems[index].title}) - angle: ${itemAngle}, current: ${this.wheelPointerAngle.toFixed(1)}, selectedMenuItem: ${this.selectedMenuItem}`);
        }
        
        // Trigger navigation if selected and not already selected
        if (isSelected && this.selectedMenuItem !== index) {
            console.log(`[MENU DEBUG] Navigating to ${this.menuItems[index].title} (${this.menuItems[index].path})`);
            this.selectedMenuItem = index;
            this.navigateToView(this.menuItems[index].path);
            
            // Send click command to server
            this.sendClickCommand();
        }
        return isSelected;
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
            const ws = new WebSocket('ws://localhost:8765/ws');
            
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
                        this.volume = Math.max(0, this.volume - 5);
                        this.updateVolumeArc();
                    }
                    break;
                case "ArrowRight":
                    if (this.currentRoute === 'menu/playing') {
                        // Webhook handled by dummy hardware system
                    } else {
                        this.volume = Math.min(100, this.volume + 5);
                        this.updateVolumeArc();
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
            this.isSelectedItem(index);
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
        
        // Use laser position mapper if laser position is available
        if (this.laserPosition && window.LaserPositionMapper) {
            this.handleWheelChangeWithMapper();
        } else {
            // Fallback to original angle-based logic
            this.handleWheelChangeOriginal();
        }

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
            if (this.menuAnimationState === 'visible' || this.menuAnimationState === 'sliding-in') {
                this.startMenuSlideOut();
            }
        } else {
            // Should show menu
            if (this.menuAnimationState === 'hidden' || this.menuAnimationState === 'sliding-out') {
                this.startMenuSlideIn();
            } else if (this.menuAnimationState === 'visible') {
                // Make sure menu is actually visible (reset any stuck states)
                this.ensureMenuVisible();
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
            
            // Fetch media info if needed (only when view changes)
            if (viewChanged && viewInfo.path === 'menu/showing') {
                this.fetchAppleTVMediaInfo();
            }
        } else {
            // Not in overlay zone
            this.isNowPlayingOverlayActive = false;
            
            // Update selected menu item state
            if (viewInfo.menuItem) {
                this.selectedMenuItem = viewInfo.menuItem.index;
                
                // Send click command only when exactly on menu item AND view changed
                if (viewInfo.reason === 'menu_item_selected' && viewChanged) {
                    this.sendClickCommand();
                }
            }
            
            // Update menu highlighting to reflect current laser position
            this.updateMenuHighlighting();
        }
    }
    
    handleWheelChangeOriginal() {
        // Original logic for fallback when laser position mapper not available
        // Define transition zones for menu sliding
        const bottomOverlayStart = 200;  // Moved down from 203 (210 is max)
        const bottomTransitionStart = 192; // Moved down from 195
        const topOverlayStart = 160;     // Moved up from 155 (150 is min)
        const topTransitionStart = 168;  // Moved up from 163
        
        // Determine if we should be in overlay zone
        const shouldBeInOverlayZone = this.wheelPointerAngle > bottomTransitionStart || this.wheelPointerAngle < topTransitionStart;
        const shouldBeInFullOverlay = this.wheelPointerAngle > bottomOverlayStart || this.wheelPointerAngle < topOverlayStart;
        
        // Handle time-based menu sliding animations with better state management
        if (shouldBeInOverlayZone) {
            // Should hide menu
            if (this.menuAnimationState === 'visible' || this.menuAnimationState === 'sliding-in') {
                this.startMenuSlideOut();
            }
        } else {
            // Should show menu
            if (this.menuAnimationState === 'hidden' || this.menuAnimationState === 'sliding-out') {
                this.startMenuSlideIn();
            } else if (this.menuAnimationState === 'visible') {
                // Make sure menu is actually visible (reset any stuck states)
                this.ensureMenuVisible();
            }
        }
        
        // Handle overlay activation/deactivation
        if (shouldBeInFullOverlay) {
            if (this.wheelPointerAngle > bottomOverlayStart && !this.isNowPlayingOverlayActive) {
                // Bottom overlay - now playing
                console.log(`[DEBUG] Activating bottom overlay (now playing) at angle ${this.wheelPointerAngle.toFixed(1)}`);
                this.isNowPlayingOverlayActive = true;
                this.navigateToView('menu/playing');
                // Media info will be pushed automatically by media server
            } else if (this.wheelPointerAngle < topOverlayStart && !this.isNowPlayingOverlayActive) {
                // Top overlay - now showing
                console.log(`[DEBUG] Activating top overlay (now showing) at angle ${this.wheelPointerAngle.toFixed(1)}`);
                this.isNowPlayingOverlayActive = true;
                this.navigateToView('menu/showing');
                this.fetchAppleTVMediaInfo();
            }
        } else if (this.isNowPlayingOverlayActive && !shouldBeInFullOverlay) {
            // Exit overlay - always return to playing view (the expected behavior)
            console.log(`[DEBUG] Exiting overlay at angle ${this.wheelPointerAngle.toFixed(1)}`);
            this.isNowPlayingOverlayActive = false;
            // Always go to playing view when exiting overlay
            this.selectedMenuItem = 5; // Index of PLAYING menu item
            console.log(`[DEBUG] Navigating to: menu/playing`);
            this.navigateToView('menu/playing');
        }
    }

    // Hide menu immediately (no animation)
    startMenuSlideOut() {
        if (this.menuAnimationState === 'hidden') return;
        
        this.menuAnimationState = 'hidden';
        
        // Clear any existing timeout
        if (this.menuAnimationTimeout) {
            clearTimeout(this.menuAnimationTimeout);
        }
        
        // Get menu elements
        const menuElements = this.getMenuElements();
        if (menuElements.length === 0) {
            console.warn('No menu elements found for hiding');
            return;
        }
        
        // Simply hide the menu without animation
        menuElements.forEach(element => {
            element.style.transition = 'none';
            element.style.display = 'none';
        });
        
        // Ensure content stays visible
        this.ensureContentVisible();
    }
    
    // Show menu immediately (no animation)
    startMenuSlideIn() {
        if (this.menuAnimationState === 'visible') return;
        
        this.menuAnimationState = 'visible';
        
        // Clear any existing timeout
        if (this.menuAnimationTimeout) {
            clearTimeout(this.menuAnimationTimeout);
        }
        
        // Get menu elements
        const menuElements = this.getMenuElements();
        if (menuElements.length === 0) {
            console.warn('No menu elements found for showing');
            return;
        }
        
        // Simply show the menu without animation
        menuElements.forEach(element => {
            element.style.transition = 'none';
            element.style.display = 'block';
            element.style.transform = 'translateX(0px)';
            element.style.opacity = '1';
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
    
    // Ensure menu is visible and reset any stuck states
    ensureMenuVisible() {
        const menuElements = this.getMenuElements();
        
        menuElements.forEach(element => {
            element.style.transition = 'none'; // Remove transitions for immediate effect
            element.style.transform = 'translateX(0px)';
            element.style.opacity = '1';
            element.style.visibility = 'visible';
        });
        
        // Force a reflow
        if (menuElements.length > 0) {
            menuElements[0].offsetHeight;
        }
        
        // Reset state
        this.menuAnimationState = 'visible';
        
        // Clear any pending timeouts
        if (this.menuAnimationTimeout) {
            clearTimeout(this.menuAnimationTimeout);
            this.menuAnimationTimeout = null;
        }
    }
    
    // Cubic easing function for smooth animations
    easeInOutCubic(t) {
        return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
    }

    navigateToView(path) {
        
        // For overlay transitions, update immediately to prevent content hiding
        const isOverlayTransition = path === 'menu/playing' || path === 'menu/showing';
        
        if (isOverlayTransition) {
            // Overlay transitions: update immediately and ensure content stays visible
            this.currentRoute = path;
            this.updateView();
            this.ensureContentVisible(); // Force content to stay visible
        } else {
            // Regular menu navigation: use fade transition
            const contentArea = document.getElementById('contentArea');
            if (contentArea) {
                contentArea.style.opacity = 0;
                setTimeout(() => {
                    this.currentRoute = path;
                    this.updateView();
                }, 250);
            } else {
                this.currentRoute = path;
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
        // Immediately update with cached info for showing view
        else if (this.currentRoute === 'menu/showing') {
            this.updateAppleTVMediaView();
            this.fetchAppleTVMediaInfo();
        }
        
        // If navigating to security view, set up the iframe
        if (this.currentRoute === 'menu/security') {
            const securityIframe = document.getElementById('security-iframe');
            const securityContainer = document.getElementById('security-container');
            const mainMenu = document.getElementById('mainMenu');
            
            if (securityIframe) {
                // Set the iframe source to the Home Assistant camera dashboard
                securityIframe.src = `${this.HA_URL}/dashboard-cameras/home&kiosk`;
                
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
}); 
