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
        
        // HA integration settings
        this.HA_URL = 'http://homeassistant.local:8123';
        this.HA_TOKEN = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNTU1MjM0NmIzMTA0NTQxOWU4ZjczYmM3YjE4YzNiOSIsImlhdCI6MTc0NjA5ODMxMiwiZXhwIjoyMDYxNDU4MzEyfQ.ZDszs4w_8_bkcIy24cvwntEsyjCzy2VODjthZRpQvaQ';
     
        this.ENTITY = 'media_player.church_dining';
        
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
        
        // Initialize views first
        this.views = {
            'menu': {
                title: 'HOME',
                content: ''
            },
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
                        <iframe id="music-iframe" src="softarc/index.html" style="width: 100%; height: 100%; border: none; border-radius: 8px; box-shadow: 0 5px 15px rgba(0,0,0,0.3);" allowfullscreen></iframe>
                    </div>
                `
            },
            'menu/settings': {
                title: 'Settings',
                content: `
                    <div class="arc-content-flow scrollable-content">
                        <div class="flow-items">
                            <div class="flow-item">Music Track 1</div>
                            <div class="flow-item">Music Track 2</div>
                            <div class="flow-item">Music Track 3</div>
                            <div class="flow-item">Music Track 4</div>
                            <div class="flow-item">Music Track 5</div>
                            <div class="flow-item">Music Track 6</div>
                            <div class="flow-item">Music Track 7</div>
                            <div class="flow-item">Music Track 8</div>
                            <div class="flow-item">Music Track 9</div>
                            <div class="flow-item">Music Track 10</div>
                            <div class="flow-item">Music Track 11</div>
                            <div class="flow-item">Music Track 12</div>
                            <div class="flow-item">Music Track 13</div>
                            <div class="flow-item">Music Track 14</div>
                            <div class="flow-item">Music Track 15</div>
                        </div>
                    </div>`
            },
            'menu/security': {
                title: 'SECURITY',
                content: `
                    <div id="security-container" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;">
                        <iframe id="security-iframe" style="width: 100%; height: 100%; border: none; border-radius: 8px; box-shadow: 0 5px 15px rgba(0,0,0,0.3);" allowfullscreen></iframe>
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
        
        // Start fetching media info
        this.fetchMediaInfo();
        this.setupMediaInfoRefresh();
        
        // Start fetching Apple TV media info
        this.setupAppleTVMediaInfoRefresh();
    }
    
    // Helper to preload and cache images
    preloadAndCacheImage(url) {
        return new Promise((resolve, reject) => {
            if (!url) return resolve(null);
            if (this.artworkCache[url] && this.artworkCache[url].complete) {
                return resolve(this.artworkCache[url]);
            }
            const img = new window.Image();
            img.onload = () => {
                this.artworkCache[url] = img;
                resolve(img);
            };
            img.onerror = reject;
            img.src = url;
        });
    }
    
    // Fetch media information from Home Assistant
    async fetchMediaInfo() {
        try {
            //console.log(`Fetching media state from ${this.HA_URL}/api/states/${this.ENTITY}`);
            const response = await fetch(`${this.HA_URL}/api/states/${this.ENTITY}`, {
                headers: { 'Authorization': 'Bearer ' + this.HA_TOKEN }
            });
            
            const data = await response.json();
            const artworkUrl = data.attributes.entity_picture ? this.HA_URL + data.attributes.entity_picture : '';
            
            // Preload and cache artwork
            if (artworkUrl) this.preloadAndCacheImage(artworkUrl);
            
            // Update media info
            this.mediaInfo = {
                title: data.attributes.media_title || '—',
                artist: data.attributes.media_artist || '—',
                album: data.attributes.media_album_name || '—',
                artwork: artworkUrl,
                state: data.state
            };
            
            // Update volume if available
            if (data.attributes.volume_level !== undefined) {
                this.volume = Math.round(data.attributes.volume_level * 100);
                this.updateVolumeArc();
            }
            
            // Update the now playing view if it's active
            if (this.currentRoute === 'menu/playing') {
                this.updateNowPlayingView();
            }
            
            //console.log('Media info updated:', this.mediaInfo);
        } catch (error) {
            console.error('Error fetching media info:', error);
        }
    }
    
    // Set up periodic refresh of media info
    setupMediaInfoRefresh() {
        // Refresh every 5 seconds
        setInterval(() => this.fetchMediaInfo(), 1000);
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
        
        // Use cached image if available and loaded
        const artworkUrl = this.mediaInfo.artwork;
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
            });
        }
    }
    
    // Handle media controls
    async sendMediaCommand(command) {
        try {
            const endpoint = `${this.HA_URL}/api/services/media_player/${command}`;
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: {
                    'Authorization': 'Bearer ' + this.HA_TOKEN,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ entity_id: this.ENTITY })
            });
            
            if (response.ok) {
                console.log(`Media command ${command} sent successfully`);
                // Fetch updated info after a short delay
                setTimeout(() => this.fetchMediaInfo(), 500);
            } else {
                console.error(`Error sending media command: ${response.status}`);
            }
        } catch (error) {
            console.error('Error sending media command:', error);
        }
    }
    
    // Fetch Apple TV media information from Home Assistant
    async fetchAppleTVMediaInfo() {
        console.log("Starting Apple TV media fetch");
        try {
            console.log(`Fetching Apple TV state from ${this.HA_URL}/api/states/media_player.loft_apple_tv`);
            const response = await fetch(`${this.HA_URL}/api/states/media_player.loft_apple_tv`, {
                headers: { 'Authorization': 'Bearer ' + this.HA_TOKEN }
            });
            
            if (!response.ok) {
                console.error(`Error fetching Apple TV data: ${response.status} ${response.statusText}`);
                return;
            }
            
            const data = await response.json();
            console.log("Apple TV data received:", data);
            
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
            
            console.log("Apple TV info processed x:", this.appleTVMediaInfo);
            
            // Update the Apple TV media view if it's active
            if (this.currentRoute === 'menu/showing') {
                this.updateAppleTVMediaView();
            } else {
                console.log("Not updating view - current route is", this.currentRoute);
            }
        } catch (error) {
            console.error('Error fetching Apple TV media info:', error);
        }
    }
    
    // Update the Apple TV media view with current info
    updateAppleTVMediaView() {
        console.log("Updating Apple TV media view");
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
            console.log("Periodic Apple TV refresh");
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
                this.wheelPointerAngle = itemAngle;
                this.isSelectedItem(index);
                this.handleWheelChange();
            });

            if (this.isSelectedItem(index)) {
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
        
        if (isSelected && this.selectedMenuItem !== index) {
            this.selectedMenuItem = index;
            this.navigateToView(this.menuItems[index].path);
            
            // Send click command to server
            this.sendClickCommand();
        }
        return isSelected;
    }

    // Send click command to server
    sendClickCommand() {
        try {
            const ws = new WebSocket('ws://localhost:8765/ws');
            ws.onopen = () => {
                const message = {
                    type: 'command',
                    command: 'click',
                    params: {}
                };
                ws.send(JSON.stringify(message));
                console.log('Sent click command to server');
                ws.close();
            };
            ws.onerror = (error) => {
                console.error('WebSocket error:', error);
            };
        } catch (error) {
            console.error('Error sending click command:', error);
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
                    this.volume = Math.max(0, this.volume - 5);
                    this.updateVolumeArc();
                    break;
                case "ArrowRight":
                    this.volume = Math.min(100, this.volume + 5);
                    this.updateVolumeArc();
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

        document.addEventListener('wheel', (event) => {
            if (event.deltaY < 0) {
                this.volume = Math.min(100, this.volume + 2);
            } else {
                this.volume = Math.max(0, this.volume - 2);
            }
            this.updateVolumeArc();
        });

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
        // Check for overlay at top or bottom
        if (this.wheelPointerAngle > 203) { // bottom
            if (!this.isNowPlayingOverlayActive) {
                this.isNowPlayingOverlayActive = true;
                this.navigateToView('menu/playing');
                this.fetchMediaInfo();
            }
        } else if (this.wheelPointerAngle < 155) { // top
            if (!this.isNowPlayingOverlayActive) {
                this.isNowPlayingOverlayActive = true;
                this.navigateToView('menu/showing');
                this.fetchAppleTVMediaInfo();
            }
        } else if (this.isNowPlayingOverlayActive) {
            this.isNowPlayingOverlayActive = false;
            this.navigateToView(this.menuItems[this.selectedMenuItem]?.path || 'menu');
        }

        this.updatePointer();
        this.renderMenuItems();
        this.topWheelPosition = 0;
    }

    navigateToView(path) {
        console.log('Navigating to path:', path);
        console.log('Available views:', Object.keys(this.views));
        
        // First fade out content
        const contentArea = document.getElementById('contentArea');
        if (contentArea) {
            contentArea.style.opacity = 0;
            
            // Wait for fade-out animation to complete before changing route
            setTimeout(() => {
                this.currentRoute = path;
                this.updateView();
            }, 250); // Match the transition duration in CSS
        } else {
            // No content area found, just update immediately
            this.currentRoute = path;
            this.updateView();
        }
    }

    updateView() {
        console.log('updateView called with currentRoute:', this.currentRoute);
        console.log('Available views:', Object.keys(this.views));
        console.log('Views object:', this.views);
        
        const contentArea = document.getElementById('contentArea');
        if (!contentArea) {
            console.error('Content area not found');
            return;
        }

        const view = this.views[this.currentRoute];
        if (!view) {
            console.error('View not found for route:', this.currentRoute);
            // Fallback to menu view if route not found
            this.currentRoute = 'menu';
            this.updateView();
            return;
        }

        // Update content while it's faded out
        contentArea.innerHTML = view.content;
        console.log(`Updated content area for route: ${this.currentRoute}`);
        
        // Immediately update with cached info for playing view
        if (this.currentRoute === 'menu/playing') {
            this.updateNowPlayingView();
            this.fetchMediaInfo();
        }
        // Immediately update with cached info for showing view
        else if (this.currentRoute === 'menu/showing') {
            this.updateAppleTVMediaView();
            this.fetchAppleTVMediaInfo();
        }
        
        // If navigating to security view, set up the iframe
        if (this.currentRoute === 'menu/security') {
            const securityIframe = document.getElementById('security-iframe');
            if (securityIframe) {
                // Set the iframe source to the Home Assistant camera dashboard
                securityIframe.src = `${this.HA_URL}/dashboard-cameras/home&kiosk`;
                
                // Add a loading indicator if needed
                securityIframe.onload = () => {
                    console.log('Security camera dashboard loaded');
                    securityIframe.classList.add('loaded');
                };
                
                securityIframe.onerror = (error) => {
                    console.error('Error loading security camera dashboard:', error);
                    // Maybe show an error message
                };
            }
        }
        
        this.setupContentScroll();
        
        // Fade the content back in
        setTimeout(() => {
            contentArea.style.opacity = 1;
        }, 50); // Small delay to ensure content is ready
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

// Make sendClickCommand globally accessible
document.addEventListener('DOMContentLoaded', () => {
    // Make the sendClickCommand function globally accessible
    window.sendClickCommand = () => {
        if (window.uiStore) {
            window.uiStore.sendClickCommand();
        } else {
            console.error('UIStore not initialized yet');
        }
    };
}); 
