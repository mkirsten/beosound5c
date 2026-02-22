/**
 * Soft crossfade for text changes on the playing view.
 * Fades out, swaps text, fades back in. Cancels pending swaps on rapid updates.
 */
function crossfadeText(el, newText) {
    if (!el) return;
    if (el.textContent === newText) return;
    clearTimeout(el._crossfadeTimer);
    el.style.opacity = '0';
    el._crossfadeTimer = setTimeout(() => {
        el.textContent = newText;
        el.style.removeProperty('opacity');
    }, 200);
}
window.crossfadeText = crossfadeText;

// Default PLAYING view slot content (used when no source overrides)
const DEFAULT_ARTWORK_SLOT = `
    <div class="playing-flipper">
        <div class="playing-face playing-front">
            <img class="playing-artwork" src="" alt="Album Art">
        </div>
        <div class="playing-face playing-back" style="display:none">
            <img class="playing-artwork-back" src="" alt="">
        </div>
    </div>`;
const DEFAULT_INFO_SLOT = `
    <div id="media-title" class="media-view-title">—</div>
    <div id="media-artist" class="media-view-artist">—</div>
    <div id="media-album" class="media-view-album">—</div>
`;
const DEFAULT_PLAYING_PRESET = {
    eventType: 'media_update',
    onUpdate(container, data) {
        const titleEl = container.querySelector('.media-view-title');
        const artistEl = container.querySelector('.media-view-artist');
        const albumEl = container.querySelector('.media-view-album');
        crossfadeText(titleEl, data.title || '—');
        crossfadeText(artistEl, data.artist || '—');
        crossfadeText(albumEl, data.album || '—');
        const img = container.querySelector('.playing-artwork');
        if (img && window.ArtworkManager) {
            window.ArtworkManager.displayArtwork(img, data.artwork, 'noArtwork');
        }
        // Back artwork (show/hide back face based on availability)
        const backFace = container.querySelector('.playing-back');
        const backImg = container.querySelector('.playing-artwork-back');
        if (backFace && backImg) {
            if (data.back_artwork) {
                backImg.src = data.back_artwork;
                backFace.style.display = '';
            } else {
                backFace.style.display = 'none';
                // Un-flip if back was removed while flipped
                const flipper = container.querySelector('.playing-flipper');
                if (flipper) flipper.classList.remove('flipped');
            }
        }
    },
    onMount(container) {
        const flipper = container.querySelector('.playing-flipper');
        if (flipper) {
            flipper._clickHandler = () => {
                const back = flipper.querySelector('.playing-back');
                if (back && back.style.display !== 'none') {
                    flipper.classList.add('playing-flipper-snap');
                    flipper.classList.toggle('flipped');
                    setTimeout(() => flipper.classList.remove('playing-flipper-snap'), 200);
                }
            };
            flipper.addEventListener('click', flipper._clickHandler);
        }
    },
    onRemove(container) {
        const flipper = container.querySelector('.playing-flipper');
        if (flipper?._clickHandler) {
            flipper.removeEventListener('click', flipper._clickHandler);
        }
    }
};

class UIStore {
    constructor() {
        this.wheelPointerAngle = 180;
        this.topWheelPosition = 0;
        
        // Initialize laser position from constants (matches hardware-input.js)
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
        
        // Menu items from centralized constants (static views only — sources added by router)
        this.menuItems = (window.Constants?.menuItems || [
            {title: 'PLAYING', path: 'menu/playing'},
            {title: 'SCENES', path: 'menu/scenes'},
            {title: 'SECURITY', path: 'menu/security'},
            {title: 'SYSTEM', path: 'menu/system'},
            {title: 'SHOWING', path: 'menu/showing'}
        ]).map(item => ({title: item.title, path: item.path}));

        // Get constants from centralized config
        const c = window.Constants || {};
        this.radius = c.arc?.radius || 1000;
        this.angleStep = c.arc?.menuAngleStep || 5;
        
        // Menu visibility state (simplified)
        this.menuVisible = true;

        // Navigation transition timeout (for cancellation)
        this.navigationTimeout = null;
        
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
                    </div>
                `,
                preloadId: 'preload-scenes'
            },
            'menu/security': {
                title: 'SECURITY',
                content: `
                    <div id="security-container" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;">
                    </div>
                `,
                preloadId: 'preload-security'
            },
            'menu/playing': {
                title: 'PLAYING',
                content: `
                    <div id="now-playing" class="media-view">
                        <div class="playing-artwork-slot media-view-artwork">
                            <div class="playing-flipper">
                                <div class="playing-face playing-front">
                                    <img class="playing-artwork" src="" alt="Album Art">
                                </div>
                                <div class="playing-face playing-back" style="display:none">
                                    <img class="playing-artwork-back" src="" alt="">
                                </div>
                            </div>
                        </div>
                        <div class="playing-info-slot media-view-info">
                            <div id="media-title" class="media-view-title">—</div>
                            <div id="media-artist" class="media-view-artist">—</div>
                            <div id="media-album" class="media-view-album">—</div>
                        </div>
                    </div>`
            },
        };

        // Active source tracking (source registry)
        this.activeSource = null;          // id of active source, or null (HA fallback)
        this.activeSourcePlayer = null;    // "local" | "remote" | null — who renders audio
        this.activePlayingPreset = DEFAULT_PLAYING_PRESET;
        this._menuLoaded = false;          // true after _fetchMenu completes

        // Set initial route
        this.currentRoute = 'menu/playing';
        this.currentView = null;

        // Initialize UI
        this.initializeUI();
        this.setupEventListeners();
        this.updateView();

        // Ensure menu starts visible
        setTimeout(() => {
            this.setMenuVisible(true);
        }, 100);

        // Media info will be received via WebSocket from media server

        // Set up Apple TV media info refresh for SHOWING view
        this.setupAppleTVMediaInfoRefresh();

        // Fetch menu from router (async, non-blocking)
        this._fetchMenu();
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
    
    // Handle media update from WebSocket (Sonos player)
    handleMediaUpdate(data, reason = 'update') {
        // Defer until menu is loaded — we need to know if a source is active
        // before deciding whether to show Sonos metadata or suppress it
        if (!this._menuLoaded) return;

        // Suppress Sonos metadata when a local player source is active.
        // Local sources (CD, USB, Demo) stream to Sonos via AirPlay but provide
        // their own metadata — showing Sonos metadata would be wrong/delayed.
        // When player is "remote" (e.g. Spotify via player service) or no source
        // is active, Sonos metadata flows through.
        if (this.activeSourcePlayer === 'local') {
            return;
        }

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
    
    // Update the now playing view with current media info (Sonos/generic path)
    updateNowPlayingView() {
        // Skip when a local player source is active — its own updates are
        // routed via routeToPlayingPreset in ws-dispatcher
        if (this.activeSourcePlayer === 'local') {
            return;
        }

        // Delegate to active playing preset if available
        if (this.activePlayingPreset?.onUpdate) {
            const container = document.getElementById('now-playing');
            if (container) {
                this.activePlayingPreset.onUpdate(container, this.mediaInfo);
                return;
            }
        }

        // Fallback: direct DOM update
        const artworkEl = document.querySelector('#now-playing .playing-artwork');
        const titleEl = document.getElementById('media-title');
        const artistEl = document.getElementById('media-artist');
        const albumEl = document.getElementById('media-album');

        if (!titleEl || !artistEl || !albumEl) return;

        titleEl.textContent = this.mediaInfo.title;
        artistEl.textContent = this.mediaInfo.artist;
        albumEl.textContent = this.mediaInfo.album;

        if (artworkEl && window.ArtworkManager) {
            window.ArtworkManager.displayArtwork(artworkEl, this.mediaInfo.artwork, 'noArtwork');
        }
    }

    // Fetch Apple TV media info from backend (which proxies HA)
    async fetchAppleTVMediaInfo() {
        // Check for development mode (Mac + localhost) - use mock data
        const isMac = navigator.platform.toLowerCase().includes('mac');
        const isLocalhost = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';

        if (isMac && isLocalhost && window.EmulatorMockData) {
            const mockData = window.EmulatorMockData.getCurrentAppleTVShow();
            this.appleTVMediaInfo = {
                title: mockData.title || '—',
                friendly_name: mockData.friendly_name || '—',
                app_name: mockData.app_name || '—',
                artwork: mockData.artwork || window.EmulatorMockData.generateShowingArtwork(mockData),
                state: mockData.state || 'playing'
            };

            if (this.currentRoute === 'menu/showing') {
                this.updateAppleTVMediaView();
            }
            return;
        }

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

    // ── Router menu & source preset management ──

    async _fetchMenu() {
        try {
            const resp = await fetch(`${window.AppConfig?.routerUrl || 'http://localhost:8770'}/router/menu`);
            const data = await resp.json();
            if (!data || !data.items) return;

            // Load source view scripts on demand
            for (const item of data.items) {
                if (item.dynamic && item.preset && !window.SourcePresets?.[item.preset]) {
                    await this._loadSourceScript(item.preset);
                }
            }

            // Rebuild menu items from router response
            const newItems = [];
            for (const item of data.items) {
                const path = `menu/${item.id}`;

                // Dynamic sources: always register view from preset (even if menu item already exists)
                if (item.dynamic && item.preset && window.SourcePresets?.[item.preset]) {
                    const preset = window.SourcePresets[item.preset];
                    newItems.push({ title: item.title, path: preset.item.path, hidden: !!item.hidden });
                    if (preset.view) {
                        this.views[preset.item.path] = preset.view;
                    }
                } else {
                    const existing = this.menuItems.find(m => m.path === path);
                    newItems.push(existing || { title: item.title, path, hidden: !!item.hidden });
                }
            }
            this.menuItems = newItems;

            // Sync to laser position mapper
            if (window.LaserPositionMapper?.updateMenuItems) {
                window.LaserPositionMapper.updateMenuItems(this.menuItems.filter(m => !m.hidden));
            }

            // Restore active source and player type
            if (data.active_source) {
                this.activeSource = data.active_source;
                this.activeSourcePlayer = data.active_player || null;
                this.setActivePlayingPreset(data.active_source);
            }

            this._menuLoaded = true;
            this.renderMenuItems();
            console.log(`[MENU] Loaded ${newItems.length} items from router (active: ${data.active_source || 'none'})`);
        } catch (e) {
            this._menuLoaded = true;  // allow media updates even if router is down
            console.log('[MENU] Router unavailable, using defaults');
        }
    }

    /**
     * Dynamically load a source's view script (web/sources/{preset}/view.js).
     */
    _loadSourceScript(preset) {
        return new Promise(resolve => {
            const script = document.createElement('script');
            script.src = `sources/${preset}/view.js`;
            script.onload = () => {
                console.log(`[MENU] Loaded source script: ${preset}`);
                resolve();
            };
            script.onerror = () => {
                console.warn(`[MENU] Source script not found: ${preset}`);
                resolve();
            };
            document.head.appendChild(script);
        });
    }

    /**
     * Switch the PLAYING view to a source's preset (or default).
     */
    setActivePlayingPreset(sourceId) {
        const preset = sourceId && window.SourcePresets?.[sourceId]?.playing;
        const newPreset = preset || DEFAULT_PLAYING_PRESET;

        // Skip rebuild if preset hasn't changed (avoids artwork flash on track changes)
        if (newPreset === this.activePlayingPreset) return;

        const container = document.getElementById('now-playing');
        if (!container) {
            // PLAYING view not currently rendered — just store the preset for later
            this.activePlayingPreset = newPreset;
            return;
        }

        const artworkSlot = container.querySelector('.playing-artwork-slot');
        const infoSlot = container.querySelector('.playing-info-slot');

        // Cleanup old preset and default flipper handler
        DEFAULT_PLAYING_PRESET.onRemove(container);
        if (this.activePlayingPreset?.onRemove) {
            this.activePlayingPreset.onRemove(container);
        }

        // Activate new preset
        this.activePlayingPreset = newPreset;

        // Override slots (or restore defaults)
        if (artworkSlot) {
            artworkSlot.innerHTML = this.activePlayingPreset.artworkSlot || DEFAULT_ARTWORK_SLOT;
        }
        if (infoSlot) {
            infoSlot.innerHTML = this.activePlayingPreset.infoSlot || DEFAULT_INFO_SLOT;
        }

        // Default artwork slot includes a flipper — set up click-to-flip
        if (!this.activePlayingPreset.artworkSlot) {
            DEFAULT_PLAYING_PRESET.onMount(container);
        }

        if (this.activePlayingPreset.onMount) {
            this.activePlayingPreset.onMount(container);
        }
    }

    /**
     * Push data to the active PLAYING preset.
     */
    updatePlaying(data) {
        const container = document.getElementById('now-playing');
        if (!container || !this.activePlayingPreset?.onUpdate) return;
        this.activePlayingPreset.onUpdate(container, data);
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

        // Preload iframes for faster navigation
        this.preloadIframes();
    }

    // Preload iframe content in background for instant navigation
    preloadIframes() {
        const iframesToPreload = [
            { id: 'preload-spotify', src: 'softarc/spotify.html' },
            { id: 'preload-scenes', src: 'softarc/scenes.html' },
            { id: 'preload-security', src: 'softarc/security.html' }
        ];

        // Create a hidden container for preloaded iframes
        let preloadContainer = document.getElementById('iframe-preload-container');
        if (!preloadContainer) {
            preloadContainer = document.createElement('div');
            preloadContainer.id = 'iframe-preload-container';
            preloadContainer.style.cssText = 'position:absolute;left:-9999px;top:-9999px;width:1px;height:1px;overflow:hidden;';
            document.body.appendChild(preloadContainer);
        }

        iframesToPreload.forEach(({ id, src }) => {
            if (!document.getElementById(id)) {
                const iframe = document.createElement('iframe');
                iframe.id = id;
                iframe.src = src;
                iframe.style.cssText = 'width:1024px;height:768px;border:none;';
                preloadContainer.appendChild(iframe);
                console.log(`[PRELOAD] Loading ${src}`);
            }
        });

        // Store preloaded state
        this.iframesPreloaded = true;
    }

    // Move a preloaded iframe into the current view container
    attachPreloadedIframe(preloadId) {
        // Map preload IDs to container IDs
        const containerMap = {
            'preload-spotify': 'spotify-container',
            'preload-scenes': 'scenes-container',
            'preload-security': 'security-container'
        };

        const containerId = containerMap[preloadId];
        const container = document.getElementById(containerId);
        if (!container) {
            console.warn(`[PRELOAD] Container ${containerId} not found`);
            return;
        }

        // Check if iframe is already in this container
        let iframe = container.querySelector('iframe');
        if (iframe) {
            console.log(`[PRELOAD] Iframe already in ${containerId}`);
            return;
        }

        // Find the preloaded iframe (might be in preload container or need fresh load)
        iframe = document.getElementById(preloadId);
        if (iframe) {
            // Style the iframe for display
            iframe.style.cssText = 'width: 100%; height: 100%; border: none; border-radius: 8px; box-shadow: 0 5px 15px rgba(0,0,0,0.3);';
            // Move iframe from preload container to view container
            container.appendChild(iframe);
            console.log(`[PRELOAD] Attached ${preloadId} to ${containerId}`);
        } else {
            // Fallback: create iframe if preload failed
            const srcMap = {
                'preload-spotify': 'softarc/spotify.html',
                'preload-scenes': 'softarc/scenes.html',
                'preload-security': 'softarc/security.html'
            };
            iframe = document.createElement('iframe');
            iframe.id = preloadId;
            iframe.src = srcMap[preloadId];
            iframe.style.cssText = 'width: 100%; height: 100%; border: none; border-radius: 8px; box-shadow: 0 5px 15px rgba(0,0,0,0.3);';
            container.appendChild(iframe);
            console.log(`[PRELOAD] Created fresh iframe for ${containerId}`);
        }
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

        const visibleItems = this.menuItems.filter(m => !m.hidden);
        visibleItems.forEach((item, index) => {
            const itemElement = document.createElement('div');
            itemElement.className = 'list-item';
            itemElement.dataset.path = item.path;
            itemElement.textContent = item.title;

            const itemAngle = this.getStartItemAngle() + (visibleItems.length - 1 - index) * this.angleStep;
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
                if (window.LaserPositionMapper) {
                    this.laserPosition = Math.round(window.LaserPositionMapper.angleToLaserPosition(itemAngle));
                }
                this.handleWheelChange();
            });

            // Highlight if this item matches the last selected path
            if (item.path === this._lastSelectedPath) {
                itemElement.classList.add('selectedItem');
            }

            menuContainer.appendChild(itemElement);
        });
    }

    getStartItemAngle() {
        const visibleCount = this.menuItems.filter(m => !m.hidden).length;
        const totalSpan = this.angleStep * (visibleCount - 1);
        return 180 - totalSpan / 2;
    }

    /**
     * Bold the menu item at selectedIndex; click when selectedPath changes.
     */
    _applyMenuHighlight(selectedIndex, selectedPath) {
        const menuContainer = document.getElementById('menuItems');
        if (!menuContainer) return;

        const menuElements = menuContainer.querySelectorAll('.list-item');
        menuElements.forEach((el, i) => {
            if (i === selectedIndex) {
                el.classList.add('selectedItem');
            } else {
                el.classList.remove('selectedItem');
            }
        });

        // Click exactly when the bolded item changes — one click per highlight change
        if (selectedPath && selectedPath !== this._lastSelectedPath) {
            this.sendClickCommand();
        }
        this._lastSelectedPath = selectedPath;
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
                if (window.LaserPositionMapper) {
                    this.laserPosition = Math.round(window.LaserPositionMapper.angleToLaserPosition(angle));
                }
                this.handleWheelChange();
            }
        });

        // Volume wheel handling removed - wheel events now ONLY control laser pointer
        // Volume can be controlled via left/right arrow keys when not in now playing view

        document.getElementById('menuItems').addEventListener('click', (event) => {
            const clickedItem = event.target.closest('.list-item');
            if (!clickedItem) return;

            const children = Array.from(clickedItem.parentElement.children);
            const index = children.indexOf(clickedItem);
            const itemAngle = this.getStartItemAngle() + (children.length - 1 - index) * this.angleStep;
            this.wheelPointerAngle = itemAngle;
            if (window.LaserPositionMapper) {
                this.laserPosition = Math.round(window.LaserPositionMapper.angleToLaserPosition(itemAngle));
            }
            this.handleWheelChange();
            
            // Send click command to server
            this.sendClickCommand();
        });
    }

    handleWheelChange() {
        this.wheelPointerAngle = Math.max(150, Math.min(210, this.wheelPointerAngle));

        if (!this.laserPosition || !window.LaserPositionMapper) {
            console.error('[UI] Laser position system required but not available');
            return;
        }

        const result = window.LaserPositionMapper.resolveMenuSelection(this.laserPosition);

        // Determine effective path — overlays navigate to PLAYING/SHOWING
        let effectivePath = result.path;
        if (result.isOverlay) {
            effectivePath = result.angle >= 200 ? 'menu/playing' : 'menu/showing';
        }

        // Menu visibility
        if (result.isOverlay && this.menuVisible) {
            this.setMenuVisible(false);
        } else if (!result.isOverlay && !this.menuVisible) {
            this.setMenuVisible(true);
        }

        // Navigate when the effective path differs from currentRoute
        if (effectivePath && effectivePath !== this.currentRoute) {
            this.navigateToView(effectivePath);
        }

        // Bold + click (only for non-overlay menu items)
        this._applyMenuHighlight(result.selectedIndex, result.path);

        this.updatePointer();
        this.topWheelPosition = 0;
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
    
    

    reportViewToRouter(view) {
        const url = `${window.AppConfig?.routerUrl || 'http://localhost:8770'}/router/view`;
        fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ view })
        }).catch(() => {}); // fire-and-forget
    }

    navigateToView(path) {
        // Skip if already on the target route (avoids unnecessary DOM rebuild
        // which causes artwork flicker, e.g. when wake trigger fires while
        // already viewing the playing page)
        if (path === this.currentRoute) return;

        // Cancel any pending navigation transition
        if (this.navigationTimeout) {
            clearTimeout(this.navigationTimeout);
            this.navigationTimeout = null;
        }

        // Update route immediately to prevent repeated navigation triggers
        this.currentRoute = path;

        // Gate iframe click messages during navigation (prevents spurious clicks
        // from softarc checkForSelectionClick when preloaded iframes are attached)
        this._navGuardUntil = Date.now() + 600;

        // Report view to router so BeoRemote buttons are routed correctly
        this.reportViewToRouter(path);

        // For overlay transitions, update immediately to prevent content hiding
        const isOverlayTransition = path === 'menu/playing' || path === 'menu/showing';

        // When arriving at PLAYING, tell the spotify iframe to reload its data so
        // playlist additions/removals are picked up.
        if (path === 'menu/playing') {
            const spotifyIframe = document.getElementById('preload-spotify');
            if (spotifyIframe?.contentWindow) {
                spotifyIframe.contentWindow.postMessage({ type: 'reload-data' }, '*');
            }
        }

        if (isOverlayTransition) {
            // Overlay transitions: update immediately and ensure content stays visible
            this.updateView();
            this.ensureContentVisible(); // Force content to stay visible
        } else {
            // Regular menu navigation: update immediately (removed fade to prevent visibility issues)
            const contentArea = document.getElementById('contentArea');
            if (contentArea) {
                contentArea.style.opacity = 0;
                this.navigationTimeout = setTimeout(() => {
                    this.updateView();
                    this.navigationTimeout = null;
                }, 150); // Reduced from 250ms for snappier transitions
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

        // Teardown previous view's preset (e.g. CDView.destroy()) before replacing content
        if (this._previousRoute && this._previousRoute !== this.currentRoute && window.SourcePresets) {
            for (const preset of Object.values(window.SourcePresets)) {
                if (preset.item.path === this._previousRoute && preset.onRemove) {
                    preset.onRemove();
                }
            }
        }
        this._previousRoute = this.currentRoute;

        // Rescue preloaded iframes before replacing content (prevents reload + stale init clicks)
        // Call destroy() on ArcList instances to clean up intervals, listeners, and WebSockets
        const preloadContainer = document.getElementById('iframe-preload-container');
        if (preloadContainer) {
            contentArea.querySelectorAll('iframe[id^="preload-"]').forEach(iframe => {
                try {
                    const win = iframe.contentWindow;
                    const inst = win?.arcListInstance || win?.arcList;
                    if (inst && typeof inst.destroy === 'function') {
                        inst.destroy();
                    }
                    // Clean up SystemPanel if present (system.html)
                    if (win?.systemPanel && typeof win.systemPanel.destroy === 'function') {
                        win.systemPanel.destroy();
                    }
                } catch (e) { /* cross-origin or unloaded iframe */ }
                iframe.style.cssText = 'width:1024px;height:768px;border:none;';
                preloadContainer.appendChild(iframe);
            });
        }

        // Update content while it's faded out
        contentArea.innerHTML = view.content;

        // If view has a preloaded iframe, move it into the container
        if (view.preloadId) {
            this.attachPreloadedIframe(view.preloadId);
        }

        // Immediately update with cached info for playing view
        if (this.currentRoute === 'menu/playing') {
            // Force re-apply preset — content was just rebuilt so slots need re-injection
            this.activePlayingPreset = null;
            this.setActivePlayingPreset(this.activeSource);
            this.updateNowPlayingView();
            // Media info will be pushed automatically by media server
        }
        // Update SHOWING view with static fallback
        else if (this.currentRoute === 'menu/showing') {
            this.updateShowingView();
        }

        // For security view in non-emulator mode with HA config, override with HA dashboard
        if (this.currentRoute === 'menu/security') {
            const haUrl = window.AppConfig?.homeAssistant?.url;
            const securityDashboard = window.AppConfig?.homeAssistant?.securityDashboard;
            const isEmulator = window.EmulatorModeManager?.isActive || window.parent !== window;

            if (!isEmulator && haUrl && securityDashboard) {
                const securityContainer = document.getElementById('security-container');
                const securityIframe = securityContainer?.querySelector('iframe');
                if (securityIframe) {
                    securityIframe.src = `${haUrl}/${securityDashboard}&kiosk`;
                    console.log('Using Home Assistant security dashboard');
                }
            }
        }
        
        this.setupContentScroll();

        // Fire onMount for dynamic menu presets (e.g. CD loading sequence)
        if (window.SourcePresets) {
            for (const preset of Object.values(window.SourcePresets)) {
                if (preset.item.path === this.currentRoute && preset.onMount) {
                    preset.onMount();
                }
            }
        }

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

    /**
     * Add a menu item dynamically at runtime.
     * @param {object} item - {title, path} for the new menu item
     * @param {string} afterPath - Insert after this path (e.g. 'menu/playing')
     * @param {object} [viewDef] - Optional view definition {title, content}
     */
    addMenuItem(item, afterPath, viewDef) {
        // Don't add duplicates
        if (this.menuItems.some(m => m.path === item.path)) {
            console.log(`[MENU] Item ${item.path} already exists`);
            return;
        }

        // Find insertion point
        const afterIndex = this.menuItems.findIndex(m => m.path === afterPath);
        const insertAt = afterIndex !== -1 ? afterIndex + 1 : this.menuItems.length;
        this.menuItems.splice(insertAt, 0, { title: item.title, path: item.path });

        // Register view content
        if (viewDef) {
            this.views[item.path] = viewDef;
        }

        // Sync to laser position mapper
        if (window.LaserPositionMapper?.updateMenuItems) {
            window.LaserPositionMapper.updateMenuItems(this.menuItems.filter(m => !m.hidden));
        }

        console.log(`[MENU] Added "${item.title}" after ${afterPath} (now ${this.menuItems.length} items)`);
        this.renderMenuItemsAnimated();
    }

    /**
     * Remove a menu item dynamically at runtime.
     * @param {string} path - Path of the item to remove (e.g. 'menu/cd')
     */
    removeMenuItem(path) {
        const index = this.menuItems.findIndex(m => m.path === path);
        if (index === -1) {
            console.log(`[MENU] Item ${path} not found`);
            return;
        }

        // If currently viewing the removed item, navigate to adjacent
        if (this.currentRoute === path) {
            const adjacentPath = this.menuItems[index - 1]?.path || this.menuItems[index + 1]?.path || 'menu/playing';
            this.navigateToView(adjacentPath);
        }

        this.menuItems.splice(index, 1);
        delete this.views[path];

        // Sync to laser position mapper
        if (window.LaserPositionMapper?.updateMenuItems) {
            window.LaserPositionMapper.updateMenuItems(this.menuItems.filter(m => !m.hidden));
        }

        console.log(`[MENU] Removed "${path}" (now ${this.menuItems.length} items)`);
        this.renderMenuItemsAnimated();
    }

    /**
     * Hide or show a menu item without removing it.
     * @param {string} path - Path of the item (e.g. 'menu/playing')
     * @param {boolean} hidden - true to hide, false to show
     */
    hideMenuItem(path, hidden) {
        const item = this.menuItems.find(m => m.path === path);
        if (!item) return;
        item.hidden = hidden;

        // If currently viewing hidden item, navigate away
        if (hidden && this.currentRoute === path) {
            const visible = this.menuItems.find(m => !m.hidden && m.path !== path);
            if (visible) this.navigateToView(visible.path);
        }

        // Sync visible items to laser position mapper
        if (window.LaserPositionMapper?.updateMenuItems) {
            window.LaserPositionMapper.updateMenuItems(this.menuItems.filter(m => !m.hidden));
        }

        // Re-render the arc menu (skips hidden items)
        this.renderMenuItemsAnimated();

        console.log(`[MENU] ${hidden ? 'Hidden' : 'Shown'} "${path}"`);
    }

    /**
     * Re-render menu items with FLIP animation.
     * Existing items slide to their new positions, new items fade in.
     */
    renderMenuItemsAnimated() {
        const menuContainer = document.getElementById('menuItems');
        if (!menuContainer) return;

        // --- FIRST: record old positions keyed by data-path ---
        const oldPositions = {};
        menuContainer.querySelectorAll('.list-item[data-path]').forEach(el => {
            const rect = el.getBoundingClientRect();
            oldPositions[el.dataset.path] = { left: rect.left, top: rect.top };
        });

        // --- Rebuild DOM (skip hidden items) ---
        menuContainer.innerHTML = '';
        const visibleItems = this.menuItems.filter(m => !m.hidden);
        visibleItems.forEach((item, index) => {
            const itemElement = document.createElement('div');
            itemElement.className = 'list-item';
            itemElement.dataset.path = item.path;
            itemElement.textContent = item.title;

            const itemAngle = this.getStartItemAngle() + (visibleItems.length - 1 - index) * this.angleStep;
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
                if (window.LaserPositionMapper) {
                    this.laserPosition = Math.round(window.LaserPositionMapper.angleToLaserPosition(itemAngle));
                }
                this.handleWheelChange();
            });

            if (item.path === this._lastSelectedPath) {
                itemElement.classList.add('selectedItem');
            }

            menuContainer.appendChild(itemElement);
        });

        // --- LAST + INVERT + PLAY ---
        menuContainer.querySelectorAll('.list-item[data-path]').forEach(el => {
            const path = el.dataset.path;
            const newRect = el.getBoundingClientRect();

            if (oldPositions[path]) {
                // Existing item that moved — animate from old to new position
                const dx = oldPositions[path].left - newRect.left;
                const dy = oldPositions[path].top - newRect.top;
                if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) {
                    el.animate([
                        { transform: `translate(${dx}px, ${dy}px)` },
                        { transform: 'translate(0, 0)' }
                    ], { duration: 300, easing: 'ease-out' });
                }
            } else {
                // New item — fade in with delay
                el.animate([
                    { opacity: 0, transform: 'translateX(-20px)' },
                    { opacity: 1, transform: 'translateX(0)' }
                ], { duration: 300, delay: 150, easing: 'ease-out', fill: 'backwards' });
            }
        });
    }

    /**
     * Test helper: add a source menu item (for console testing without router)
     * Usage: uiStore.testAddSource('cd') or uiStore.testAddSource('demo')
     */
    testAddSource(sourceId) {
        const preset = window.SourcePresets?.[sourceId];
        if (!preset) {
            console.error(`SourcePresets.${sourceId} not loaded`);
            return;
        }
        this.addMenuItem(preset.item, preset.after, preset.view);
        setTimeout(() => {
            const container = document.getElementById('contentArea');
            if (preset.onAdd) preset.onAdd(container);
        }, 50);
    }

    /**
     * Test helper: remove a source menu item
     * Usage: uiStore.testRemoveSource('cd')
     */
    testRemoveSource(sourceId) {
        const preset = window.SourcePresets?.[sourceId];
        if (!preset) {
            console.error(`SourcePresets.${sourceId} not loaded`);
            return;
        }
        if (preset.onRemove) preset.onRemove();
        this.removeMenuItem(preset.item.path);
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

    // Fade out splash screen after artwork is ready
    const timeouts = window.Constants?.timeouts || {};

    const hideSplash = () => {
        const splash = document.getElementById('splash-overlay');
        if (splash && !splash.classList.contains('fade-out')) {
            splash.classList.add('fade-out');
            setTimeout(() => {
                splash.classList.add('hidden');
            }, timeouts.splashRemoveDelay || 800);
        }
    };

    // Wait for artwork to load before hiding splash
    const waitForArtwork = () => {
        const artworkEl = document.querySelector('#now-playing .playing-artwork');
        if (artworkEl && artworkEl.src && artworkEl.src !== '' && artworkEl.src !== window.location.href) {
            // Artwork src is set, wait for it to actually load
            if (artworkEl.complete && artworkEl.naturalHeight > 0) {
                hideSplash();
            } else {
                artworkEl.onload = hideSplash;
                artworkEl.onerror = hideSplash; // Hide anyway on error
            }
        } else {
            // No artwork yet, check again shortly
            setTimeout(waitForArtwork, 100);
        }
    };

    // Start checking for artwork after a brief delay, with a max timeout
    setTimeout(waitForArtwork, 300);

    // Fallback: hide splash after max wait time regardless
    setTimeout(hideSplash, 3000);

    // Relay messages from child iframes
    window.addEventListener('message', (event) => {
        if (event.data?.type === 'reload-playlists') {
            const spotifyIframe = document.getElementById('preload-spotify');
            if (spotifyIframe?.contentWindow) {
                spotifyIframe.contentWindow.postMessage({ type: 'reload-data' }, '*');
            }
        } else if (event.data?.type === 'click') {
            // Only forward iframe clicks after navigation has settled
            if (!uiStore._navGuardUntil || Date.now() > uiStore._navGuardUntil) {
                uiStore.sendClickCommand();
            }
        }
    });
});
