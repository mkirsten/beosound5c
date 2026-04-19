/**
 * UIStore — thin coordinator that wires together MediaManager, MenuManager,
 * and ViewManager.  Owns input handling (laser, wheel, keyboard) and the
 * arc pointer.  Exposes backward-compatible window.uiStore API by delegating
 * to the individual managers.
 *
 * Load order (index.html):
 *   media-manager.js → menu-manager.js → view-manager.js → ui-store.js
 */

class UIStore {
    constructor() {
        // ── Create managers ──
        this.media = new MediaManager();
        this.menu = new MenuManager();
        this.view = new ViewManager();

        // ── Wire cross-references ──
        this.view.menuManager = this.menu;
        this.view.mediaManager = this.media;

        this.menu.onNavigate = (path) => this.view.navigateToView(path);
        this.menu.onMenuLoaded = (data) => {
            if (data.active_source) {
                this.media.activeSource = data.active_source;
                this.media.activeSourcePlayer = data.active_player || null;
                this.media.setActivePlayingPreset(data.active_source);
            }
        };
        this.menu.onItemHover = (angle) => {
            this.wheelPointerAngle = angle;
            if (window.LaserPositionMapper) {
                this.laserPosition = Math.round(window.LaserPositionMapper.angleToLaserPosition(angle));
            }
            this.handleWheelChange();
        };

        // Keep menu manager informed of current route for removeMenuItem
        const origNav = this.view.navigateToView.bind(this.view);
        this.view.navigateToView = (path) => {
            origNav(path);
            this.menu._currentRoute = this.view.currentRoute;
        };

        // ── Input / pointer state ──
        this.wheelPointerAngle = 180;
        this.topWheelPosition = 0;
        this.laserPosition = window.Constants?.laser?.defaultPosition || 93;

        // ── Debug ──
        this.debugEnabled = true;
        this.debugVisible = false;
        this.wsMessages = [];
        this.maxWsMessages = 50;

        // ── Initialize ──
        this._initializeUI();
        this._setupEventListeners();
        this.view.updateView();

        setTimeout(() => {
            this.view.setMenuVisible(true);
        }, 100);

        // Apple TV refresh starts on-demand when navigating to SHOWING view

        // Fetch menu from router (async, non-blocking)
        this.menu.fetchMenu();
    }

    // ── Backward-compatible property access ──
    // External code (ws-dispatcher, hardware-input, immersive-mode, etc.)
    // accesses these via window.uiStore.X — delegate to the right manager.

    get mediaInfo() { return this.media.mediaInfo; }
    set mediaInfo(v) { this.media.mediaInfo = v; }

    get activeSource() { return this.media.activeSource; }
    set activeSource(v) { this.media.activeSource = v; }

    get activeSourcePlayer() { return this.media.activeSourcePlayer; }
    set activeSourcePlayer(v) { this.media.activeSourcePlayer = v; }

    get activePlayingPreset() { return this.media.activePlayingPreset; }

    get menuItems() { return this.menu.menuItems; }

    get currentRoute() { return this.view.currentRoute; }
    set currentRoute(v) { this.view.currentRoute = v; }

    get menuVisible() { return this.view.menuVisible; }

    get views() { return this.menu.views; }

    get _menuLoaded() { return this.menu._menuLoaded; }

    // ── Delegated methods ──

    handleMediaUpdate(data, reason) { this.media.handleMediaUpdate(data, reason); }
    updateNowPlayingView() { this.media.updateNowPlayingView(); }
    setActivePlayingPreset(sourceId) { this.media.setActivePlayingPreset(sourceId); }

    navigateToView(path) { this.view.navigateToView(path); this.menu._currentRoute = this.view.currentRoute; }
    setMenuVisible(visible) { this.view.setMenuVisible(visible); }

    addMenuItem(item, afterPath, viewDef) { this.menu.addMenuItem(item, afterPath, viewDef); }
    removeMenuItem(path) { this.menu._currentRoute = this.view.currentRoute; this.menu.removeMenuItem(path); }

    _loadSourceScript(preset) { return this.menu.loadSourceScript(preset); }
    _reloadAllSourceIframes() { this.menu.reloadAllSourceIframes(); }

    // ── Debug logging ──

    logWebsocketMessage(message) {
        this.wsMessages.unshift({
            time: new Date().toLocaleTimeString(),
            message
        });
        if (this.wsMessages.length > this.maxWsMessages) {
            this.wsMessages.length = this.maxWsMessages;
        }
    }

    // ── UI initialization ──

    _initializeUI() {
        const mainArc = document.getElementById('mainArc');
        mainArc.setAttribute('d', arcs.drawArc(arcs.cx, arcs.cy, this.menu.radius, 158, 202));

        this.menu.renderMenuItems();
        this.updatePointer();
        this.menu.preloadIframes();
    }

    // ── Pointer ──

    updatePointer() {
        const pointerDot = document.getElementById('pointerDot');
        const pointerLine = document.getElementById('pointerLine');
        const mainMenu = document.getElementById('mainMenu');

        const point = arcs.getArcPoint(this.menu.radius, 0, this.wheelPointerAngle);
        const transform = `rotate(${this.wheelPointerAngle - 90}deg)`;

        [pointerDot, pointerLine].forEach(element => {
            element.setAttribute('cx', point.x);
            element.setAttribute('cy', point.y);
            element.style.transformOrigin = `${point.x}px ${point.y}px`;
            element.style.transform = transform;
        });

        if (mainMenu) {
            if (this.wheelPointerAngle > 203 || this.wheelPointerAngle < 155) {
                mainMenu.classList.add('slide-out');
            } else {
                mainMenu.classList.remove('slide-out');
            }
        }
    }

    // ── Input handling ──

    handleWheelChange() {
        this.wheelPointerAngle = Math.max(150, Math.min(210, this.wheelPointerAngle));

        if (!this.laserPosition || !window.LaserPositionMapper) {
            console.error('[UI] Laser position system required but not available');
            return;
        }

        const result = window.LaserPositionMapper.resolveMenuSelection(this.laserPosition);

        // Determine effective path — overlays navigate to PLAYING/SHOWING.
        // If SHOWING is not in the menu, both ends land on PLAYING.
        let effectivePath = result.path;
        if (result.isOverlay) {
            const hasShowing = this.menu.menuItems.some(m => m.path === 'menu/showing');
            effectivePath = (result.angle >= 200 || !hasShowing) ? 'menu/playing' : 'menu/showing';
        }

        // Menu visibility
        if (result.isOverlay && this.view.menuVisible) {
            this.view.setMenuVisible(false);
        } else if (!result.isOverlay && !this.view.menuVisible) {
            this.view.setMenuVisible(true);
        }

        // Navigate when the effective path differs
        if (effectivePath && effectivePath !== this.view.currentRoute) {
            this.view.navigateToView(effectivePath);
            this.menu._currentRoute = this.view.currentRoute;
        }

        // Bold + click (only for non-overlay menu items)
        if (this.menu.applyMenuHighlight(result.selectedIndex, result.path)) {
            this.sendClickCommand();
        }

        this.updatePointer();
        this.topWheelPosition = 0;

        document.dispatchEvent(new CustomEvent('bs5c:wheel-change'));
    }

    setLaserPosition(position) {
        this.laserPosition = position;
    }

    sendClickCommand() {
        const ws = window.hardwareWebSocket;
        if (ws && ws.readyState === WebSocket.OPEN) {
            try {
                ws.send(JSON.stringify({ type: 'command', command: 'click', params: {} }));
            } catch (error) {
                // Silently fail - connection may have closed between check and send
            }
        }
    }

    forwardButtonToActiveIframe(button) {
        if (window.IframeMessenger) {
            window.IframeMessenger.sendButtonEvent(this.view.currentRoute, button);
        }
    }

    forwardKeyboardToActiveIframe(event) {
        if (window.IframeMessenger) {
            window.IframeMessenger.sendKeyboardEvent(this.view.currentRoute, event);
        }
    }

    _setupEventListeners() {
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
                    if (this.view.currentRoute === 'menu/playing') {
                        // Webhook handled by dummy hardware system
                    } else {
                        this.forwardButtonToActiveIframe('left');
                        this.forwardKeyboardToActiveIframe(event);
                    }
                    break;
                case "ArrowRight":
                    if (this.view.currentRoute === 'menu/playing') {
                        // Webhook handled by dummy hardware system
                    } else {
                        this.forwardButtonToActiveIframe('right');
                        this.forwardKeyboardToActiveIframe(event);
                    }
                    break;
                case "Enter":
                    if (this.view.currentRoute !== 'menu/playing') {
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

        document.getElementById('menuItems').addEventListener('click', (event) => {
            const clickedItem = event.target.closest('.list-item');
            if (!clickedItem) return;

            const children = Array.from(clickedItem.parentElement.children);
            const index = children.indexOf(clickedItem);
            const itemAngle = this.menu.getStartItemAngle() + (children.length - 1 - index) * this.menu.angleStep;
            this.wheelPointerAngle = itemAngle;
            if (window.LaserPositionMapper) {
                this.laserPosition = Math.round(window.LaserPositionMapper.angleToLaserPosition(itemAngle));
            }
            this.handleWheelChange();

            this.sendClickCommand();
        });
    }

    // ── Test helpers ──

    testAddSource(sourceId) {
        const preset = window.SourcePresets?.[sourceId];
        if (!preset) {
            console.error(`SourcePresets.${sourceId} not loaded`);
            return;
        }
        this.menu.addMenuItem(preset.item, preset.after, preset.view);
        setTimeout(() => {
            const container = document.getElementById('contentArea');
            if (preset.onAdd) preset.onAdd(container);
        }, 50);
    }

    testRemoveSource(sourceId) {
        const preset = window.SourcePresets?.[sourceId];
        if (!preset) {
            console.error(`SourcePresets.${sourceId} not loaded`);
            return;
        }
        if (preset.onRemove) preset.onRemove();
        this.menu._currentRoute = this.view.currentRoute;
        this.menu.removeMenuItem(preset.item.path);
    }
}

// ── Bootstrap ──

document.addEventListener('DOMContentLoaded', () => {
    const uiStore = new UIStore();
    window.uiStore = uiStore;

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

    const waitForArtwork = () => {
        const artworkEl = document.querySelector('#now-playing .playing-artwork');
        if (artworkEl && artworkEl.src && artworkEl.src !== '' && artworkEl.src !== window.location.href) {
            if (artworkEl.complete && artworkEl.naturalHeight > 0) {
                hideSplash();
            } else {
                artworkEl.onload = hideSplash;
                artworkEl.onerror = hideSplash;
            }
        } else {
            setTimeout(waitForArtwork, 100);
        }
    };

    setTimeout(waitForArtwork, 300);
    setTimeout(hideSplash, 3000);

    // Relay messages from child iframes
    window.addEventListener('message', (event) => {
        if (event.data?.type === 'reload-playlists') {
            uiStore.menu.reloadAllSourceIframes();
        } else if (event.data?.type === 'click') {
            // Only honor clicks from an iframe currently attached to the
            // active view. Preloaded / detached iframes in the offscreen
            // preload container still have live message listeners and may
            // emit clicks from stale input — ignore those to avoid racing
            // through menu items during/after navigation.
            const contentArea = document.getElementById('contentArea');
            if (!contentArea) return;
            const fromActive = Array.from(contentArea.querySelectorAll('iframe'))
                .some(f => f.contentWindow === event.source);
            if (fromActive) uiStore.sendClickCommand();
        }
    });
});
