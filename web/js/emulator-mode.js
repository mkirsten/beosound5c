// Emulator Mode Manager for BeoSound 5c
// Handles receiving messages from parent emulator and provides mock data
// Uses EmulatorBridge for outgoing communication and EmulatorMockData for mock data

const EmulatorModeManager = {
    isActive: false,
    parentWindow: null,
    currentShowingIndex: 0,

    init() {
        // Check URL parameter for demo mode
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('demo') === 'true') {
            this.activate('URL parameter');
        }

        // Check config flag
        if (window.AppConfig?.demo?.enabled) {
            this.activate('config flag');
        }

        // Check if we're in an iframe (embedded in emulator.html)
        if (window.parent !== window) {
            this.parentWindow = window.parent;
            this.setupMessageListener();
        }

        console.log('[EMULATOR] Mode manager initialized');
    },

    // Listen for messages from parent emulator
    setupMessageListener() {
        window.addEventListener('message', (event) => {
            if (!event.data || !event.data.type) return;

            const { type, data } = event.data;

            switch (type) {
                case 'laser':
                    this.forwardToHardware('sendLaserEvent', data.position);
                    break;
                case 'nav':
                    this.forwardToHardware('sendNavEvent', data.direction, data.speed || 20);
                    break;
                case 'volume':
                    this.forwardToHardware('sendVolumeEvent', data.direction, data.speed || 10);
                    break;
                case 'button':
                    this.forwardToHardware('sendButtonEvent', data.button);
                    break;
                case 'camera_toggle':
                    this.handleCameraToggle();
                    break;
                case 'get_state':
                    this.sendStateToParent();
                    break;
                case 'mock_track':
                    this.handleExternalTrack(data);
                    break;
            }
        });

        // Report view changes to parent via bridge
        this.setupViewChangeReporting();

        console.log('[EMULATOR] Message listener established');
    },

    // Forward events to dummy hardware system
    forwardToHardware(method, ...args) {
        if (window.dummyHardwareManager?.server?.isRunning) {
            window.dummyHardwareManager.server[method](...args);
        }
    },

    handleCameraToggle() {
        if (window.CameraOverlayManager) {
            if (window.CameraOverlayManager.isActive) {
                window.CameraOverlayManager.hide();
            } else {
                window.CameraOverlayManager.show();
            }
        }
    },

    handleExternalTrack(data) {
        // Handle track update from parent emulator.html (Spotify playlists)
        if (!window.uiStore?.handleMediaUpdate) return;

        const trackData = {
            title: data.title,
            artist: data.artist,
            album: data.album,
            artwork_url: data.artwork,
            artwork: data.artwork,
            playback_state: 'PLAYING',
            state: 'playing',
            position_ms: 0,
            duration_ms: 240000,
            position: '0:00',
            duration: '4:00'
        };

        console.log(`[EMULATOR] Track: ${data.artist} - ${data.title}`);
        window.uiStore.handleMediaUpdate(trackData, 'emulator');
    },

    setupViewChangeReporting() {
        let lastRoute = null;
        setInterval(() => {
            if (window.uiStore && window.EmulatorBridge?.isInEmulator) {
                const currentRoute = window.uiStore.currentRoute;
                if (currentRoute !== lastRoute) {
                    lastRoute = currentRoute;
                    window.EmulatorBridge.notifyViewChanged(currentRoute);
                }
            }
        }, 100);
    },

    sendStateToParent() {
        if (window.EmulatorBridge?.isInEmulator) {
            window.EmulatorBridge.reportState({
                view: window.uiStore?.currentRoute || 'unknown',
                laserPosition: window.uiStore?.laserPosition || 93
            });
        }
    },

    activate(reason = 'unknown') {
        if (this.isActive) return;

        this.isActive = true;
        console.log(`[EMULATOR] Mode activated (reason: ${reason})`);

        if (window.uiStore?.logWebsocketMessage) {
            window.uiStore.logWebsocketMessage(`Emulator mode activated: ${reason}`);
        }

        // Setup mocks for demo mode
        this.setupShowingMock();
        this.setupSystemInfoMock();
    },

    deactivate() {
        if (!this.isActive) return;
        this.isActive = false;
        console.log('[EMULATOR] Mode deactivated');
    },

    // === Mock data proxies (delegate to EmulatorMockData) ===

    getMockCameraUrl(cameraTitle) {
        return window.EmulatorMockData?.getCameraUrl(cameraTitle) || '';
    },

    getSystemInfo() {
        return window.EmulatorMockData?.getSystemInfo() || {};
    },

    // Setup Apple TV / Showing mock
    setupShowingMock() {
        const originalFetch = window.fetch;
        window.fetch = async (url, options) => {
            if (typeof url === 'string' && url.includes('/appletv')) {
                return this.mockAppleTVResponse();
            }
            if (typeof url === 'string' && url.includes('/forward')) {
                console.log('[EMULATOR] Mock webhook:', options?.body);
                return new Response(JSON.stringify({ success: true }), {
                    status: 200,
                    headers: { 'Content-Type': 'application/json' }
                });
            }
            return originalFetch(url, options);
        };

        // Cycle showing content (if multiple shows configured)
        const shows = window.EmulatorMockData?.appleTV || [];
        if (shows.length > 1) {
            setInterval(() => {
                this.currentShowingIndex = (this.currentShowingIndex + 1) % shows.length;
            }, 20000);
        }
    },

    mockAppleTVResponse() {
        const showing = window.EmulatorMockData?.getCurrentAppleTVShow(this.currentShowingIndex) || {};
        return new Response(JSON.stringify({
            title: showing.title || 'Unknown',
            app_name: showing.app_name || 'Unknown',
            friendly_name: showing.friendly_name || 'Apple TV',
            state: showing.state || 'idle',
            artwork: showing.artwork || window.EmulatorMockData?.generateShowingArtwork(showing) || ''
        }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' }
        });
    },

    // Setup system info mock
    setupSystemInfoMock() {
        if (!window.EmulatorMockData) return;

        let uptimeSeconds = 3 * 24 * 3600 + 14 * 3600 + 22 * 60;
        setInterval(() => {
            uptimeSeconds += 1;
            const days = Math.floor(uptimeSeconds / 86400);
            const hours = Math.floor((uptimeSeconds % 86400) / 3600);
            const minutes = Math.floor((uptimeSeconds % 3600) / 60);
            window.EmulatorMockData.systemInfo.uptime = `${days} days, ${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}`;
            window.EmulatorMockData.systemInfo.cpu_temp = (44 + Math.random() * 3).toFixed(1) + '°C';
        }, 1000);
    }
};

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    EmulatorModeManager.init();
});

// Make available globally
window.EmulatorModeManager = EmulatorModeManager;
