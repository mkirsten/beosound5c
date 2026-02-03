// Demo Mode Manager for BeoSound 5c
// Provides mock data when running without real hardware, cameras, or Sonos
// Also handles communication with parent emulator.html page

const EmulatorModeManager = {
    isActive: false,
    trackCycleInterval: null,
    currentTrackIndex: 0,
    parentWindow: null,

    // Mock tracks for emulator mode
    mockTracks: [
        { title: "Bohemian Rhapsody", artist: "Queen", album: "A Night at the Opera" },
        { title: "Hotel California", artist: "Eagles", album: "Hotel California" },
        { title: "Stairway to Heaven", artist: "Led Zeppelin", album: "Led Zeppelin IV" },
        { title: "Comfortably Numb", artist: "Pink Floyd", album: "The Wall" },
        { title: "Billie Jean", artist: "Michael Jackson", album: "Thriller" },
        { title: "Sweet Child O' Mine", artist: "Guns N' Roses", album: "Appetite for Destruction" },
        { title: "Smells Like Teen Spirit", artist: "Nirvana", album: "Nevermind" },
        { title: "Hey Jude", artist: "The Beatles", album: "Hey Jude" },
        { title: "Back in Black", artist: "AC/DC", album: "Back in Black" },
        { title: "Imagine", artist: "John Lennon", album: "Imagine" },
        { title: "Wonderwall", artist: "Oasis", album: "(What's the Story) Morning Glory?" },
        { title: "Purple Rain", artist: "Prince", album: "Purple Rain" }
    ],

    // Mock Apple TV/Showing data
    mockShowingData: [
        { title: "The Office", app_name: "Netflix", friendly_name: "Living Room Apple TV", state: "playing" },
        { title: "Breaking Bad", app_name: "Netflix", friendly_name: "Living Room Apple TV", state: "paused" },
        { title: "Planet Earth III", app_name: "Apple TV+", friendly_name: "Living Room Apple TV", state: "playing" },
        { title: "Ted Lasso", app_name: "Apple TV+", friendly_name: "Living Room Apple TV", state: "idle" }
    ],
    currentShowingIndex: 0,

    // Mock system info
    mockSystemInfo: {
        hostname: 'beosound5c-demo',
        ip_address: '192.168.1.100',
        uptime: '3 days, 14:22',
        cpu_temp: '45.2°C',
        memory_usage: '42%',
        disk_usage: '68%',
        wifi_signal: '-52 dBm',
        software_version: '2.1.0-demo'
    },

    init() {
        // Check URL parameter for emulator mode
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
            this.setupParentCommunication();
        }

        console.log('[DEMO] Emulator mode manager initialized');
    },

    setupParentCommunication() {
        // Listen for messages from parent emulator.html
        window.addEventListener('message', (event) => {
            if (!event.data || !event.data.type) return;

            const { type, data } = event.data;

            switch (type) {
                case 'laser':
                    this.handleExternalLaser(data);
                    break;
                case 'nav':
                    this.handleExternalNav(data);
                    break;
                case 'volume':
                    this.handleExternalVolume(data);
                    break;
                case 'button':
                    this.handleExternalButton(data);
                    break;
                case 'camera_toggle':
                    this.handleCameraToggle();
                    break;
                case 'get_state':
                    this.sendStateToParent();
                    break;
            }
        });

        // Report view changes to parent
        this.setupViewChangeReporting();

        console.log('[DEMO] Parent communication established');
    },

    handleExternalLaser(data) {
        // Forward laser event to the dummy hardware system
        if (window.dummyHardwareManager?.server?.isRunning) {
            window.dummyHardwareManager.server.sendLaserEvent(data.position);
        }
    },

    handleExternalNav(data) {
        if (window.dummyHardwareManager?.server?.isRunning) {
            window.dummyHardwareManager.server.sendNavEvent(data.direction, data.speed || 20);
        }
    },

    handleExternalVolume(data) {
        if (window.dummyHardwareManager?.server?.isRunning) {
            window.dummyHardwareManager.server.sendVolumeEvent(data.direction, data.speed || 10);
        }
    },

    handleExternalButton(data) {
        if (window.dummyHardwareManager?.server?.isRunning) {
            window.dummyHardwareManager.server.sendButtonEvent(data.button);
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

    setupViewChangeReporting() {
        // Poll for view changes and report to parent
        let lastRoute = null;
        setInterval(() => {
            if (window.uiStore && this.parentWindow) {
                const currentRoute = window.uiStore.currentRoute;
                if (currentRoute !== lastRoute) {
                    lastRoute = currentRoute;
                    this.parentWindow.postMessage({
                        type: 'view_changed',
                        path: currentRoute
                    }, '*');
                }
            }
        }, 100);
    },

    sendStateToParent() {
        if (!this.parentWindow) return;

        const state = {
            type: 'state_update',
            view: window.uiStore?.currentRoute || 'unknown',
            laserPosition: window.uiStore?.laserPosition || 93,
            angle: window.uiStore?.wheelPointerAngle || 180,
            track: this.getCurrentTrack()
        };

        this.parentWindow.postMessage(state, '*');
    },

    activate(reason = 'unknown') {
        if (this.isActive) return;

        this.isActive = true;
        console.log(`[DEMO] Emulator mode activated (reason: ${reason})`);

        if (window.uiStore?.logWebsocketMessage) {
            window.uiStore.logWebsocketMessage(`Emulator mode activated: ${reason}`);
        }

        // Start cycling through mock tracks
        this.startTrackCycle();

        // Push initial mock media update
        this.pushMockMediaUpdate();

        // Mock Apple TV / Showing view
        this.setupShowingMock();

        // Mock system info
        this.setupSystemInfoMock();

        // Notify parent if embedded
        if (this.parentWindow) {
            this.parentWindow.postMessage({
                type: 'track_changed',
                track: this.getCurrentTrack()
            }, '*');
        }
    },

    deactivate() {
        if (!this.isActive) return;

        this.isActive = false;
        this.stopTrackCycle();
        console.log('[DEMO] Emulator mode deactivated');
    },

    startTrackCycle() {
        if (this.trackCycleInterval) return;

        // Cycle through tracks every 10-15 seconds (randomized)
        const cycleTrack = () => {
            this.currentTrackIndex = (this.currentTrackIndex + 1) % this.mockTracks.length;
            this.pushMockMediaUpdate();

            // Notify parent
            if (this.parentWindow) {
                this.parentWindow.postMessage({
                    type: 'track_changed',
                    track: this.getCurrentTrack()
                }, '*');
            }

            // Schedule next cycle with random interval (10-15 seconds)
            const nextInterval = 10000 + Math.random() * 5000;
            this.trackCycleInterval = setTimeout(cycleTrack, nextInterval);
        };

        // Start first cycle
        const initialInterval = 10000 + Math.random() * 5000;
        this.trackCycleInterval = setTimeout(cycleTrack, initialInterval);
    },

    stopTrackCycle() {
        if (this.trackCycleInterval) {
            clearTimeout(this.trackCycleInterval);
            this.trackCycleInterval = null;
        }
    },

    getCurrentTrack() {
        return this.mockTracks[this.currentTrackIndex];
    },

    pushMockMediaUpdate() {
        if (!window.uiStore?.handleMediaUpdate) return;

        const track = this.getCurrentTrack();
        const mockData = {
            title: track.title,
            artist: track.artist,
            album: track.album,
            artwork_url: this.generateArtworkDataUrl(track),
            artwork: this.generateArtworkDataUrl(track),
            playback_state: 'PLAYING',
            state: 'playing',
            position_ms: Math.floor(Math.random() * 180000),
            duration_ms: 180000 + Math.floor(Math.random() * 120000),
            position: this.formatTime(Math.floor(Math.random() * 180)),
            duration: this.formatTime(180 + Math.floor(Math.random() * 120))
        };

        console.log(`[DEMO] Pushing mock track: ${track.artist} - ${track.title}`);
        window.uiStore.handleMediaUpdate(mockData, 'demo_mode');
    },

    formatTime(seconds) {
        const mins = Math.floor(seconds / 60);
        const secs = seconds % 60;
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    },

    // Setup Apple TV / Showing mock
    setupShowingMock() {
        // Intercept fetch requests to /appletv
        const originalFetch = window.fetch;
        window.fetch = async (url, options) => {
            if (typeof url === 'string' && url.includes('/appletv')) {
                return this.mockAppleTVResponse();
            }
            if (typeof url === 'string' && url.includes('/forward')) {
                // Mock webhook - just log and return success
                console.log('[DEMO] Mock webhook:', options?.body);
                return new Response(JSON.stringify({ success: true }), {
                    status: 200,
                    headers: { 'Content-Type': 'application/json' }
                });
            }
            return originalFetch(url, options);
        };

        // Cycle showing content
        setInterval(() => {
            this.currentShowingIndex = (this.currentShowingIndex + 1) % this.mockShowingData.length;
        }, 20000);
    },

    mockAppleTVResponse() {
        const showing = this.mockShowingData[this.currentShowingIndex];
        return new Response(JSON.stringify({
            title: showing.title,
            app_name: showing.app_name,
            friendly_name: showing.friendly_name,
            state: showing.state,
            artwork: this.generateShowingArtworkDataUrl(showing)
        }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' }
        });
    },

    // Setup system info mock
    setupSystemInfoMock() {
        // Update uptime periodically
        let uptimeSeconds = 3 * 24 * 3600 + 14 * 3600 + 22 * 60;
        setInterval(() => {
            uptimeSeconds += 1;
            const days = Math.floor(uptimeSeconds / 86400);
            const hours = Math.floor((uptimeSeconds % 86400) / 3600);
            const minutes = Math.floor((uptimeSeconds % 3600) / 60);
            this.mockSystemInfo.uptime = `${days} days, ${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}`;

            // Simulate slight CPU temp variation
            this.mockSystemInfo.cpu_temp = (44 + Math.random() * 3).toFixed(1) + '°C';
        }, 1000);
    },

    // Get mock system info (can be called by system.html)
    getSystemInfo() {
        return this.mockSystemInfo;
    },

    // Generate SVG artwork as data URL based on track info
    generateArtworkDataUrl(track) {
        // Generate color based on track title hash
        const hash = this.hashString(track.title + track.artist);
        const hue = hash % 360;
        const saturation = 60 + (hash % 30);
        const lightness = 35 + (hash % 20);

        const bgColor = `hsl(${hue}, ${saturation}%, ${lightness}%)`;
        const accentColor = `hsl(${(hue + 180) % 360}, ${saturation}%, ${lightness + 20}%)`;

        // Create SVG artwork with album-like design
        const svg = `
            <svg xmlns="http://www.w3.org/2000/svg" width="300" height="300" viewBox="0 0 300 300">
                <defs>
                    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
                        <stop offset="0%" style="stop-color:${bgColor}"/>
                        <stop offset="100%" style="stop-color:hsl(${(hue + 40) % 360}, ${saturation}%, ${lightness - 10}%)"/>
                    </linearGradient>
                    <radialGradient id="vinyl" cx="50%" cy="50%" r="50%">
                        <stop offset="0%" style="stop-color:#222"/>
                        <stop offset="30%" style="stop-color:#111"/>
                        <stop offset="100%" style="stop-color:#000"/>
                    </radialGradient>
                </defs>
                <rect width="300" height="300" fill="url(#bg)"/>
                <circle cx="150" cy="150" r="100" fill="url(#vinyl)" opacity="0.7"/>
                <circle cx="150" cy="150" r="80" fill="none" stroke="${accentColor}" stroke-width="0.5" opacity="0.5"/>
                <circle cx="150" cy="150" r="60" fill="none" stroke="${accentColor}" stroke-width="0.5" opacity="0.4"/>
                <circle cx="150" cy="150" r="40" fill="none" stroke="${accentColor}" stroke-width="0.5" opacity="0.3"/>
                <circle cx="150" cy="150" r="20" fill="${accentColor}" opacity="0.6"/>
                <circle cx="150" cy="150" r="5" fill="#fff" opacity="0.8"/>
                <text x="150" y="270" text-anchor="middle" fill="white" font-family="sans-serif" font-size="10" opacity="0.6">DEMO MODE</text>
            </svg>
        `.trim();

        return 'data:image/svg+xml;base64,' + btoa(svg);
    },

    // Generate artwork for Showing/Apple TV view
    generateShowingArtworkDataUrl(showing) {
        const hash = this.hashString(showing.title + showing.app_name);
        const hue = hash % 360;

        const svg = `
            <svg xmlns="http://www.w3.org/2000/svg" width="400" height="225" viewBox="0 0 400 225">
                <defs>
                    <linearGradient id="tvbg" x1="0%" y1="0%" x2="100%" y2="100%">
                        <stop offset="0%" style="stop-color:hsl(${hue}, 50%, 20%)"/>
                        <stop offset="100%" style="stop-color:hsl(${(hue + 30) % 360}, 40%, 10%)"/>
                    </linearGradient>
                </defs>
                <rect width="400" height="225" fill="url(#tvbg)"/>
                <rect x="20" y="20" width="360" height="140" fill="rgba(0,0,0,0.4)" rx="4"/>
                <text x="200" y="100" text-anchor="middle" fill="white" font-family="sans-serif" font-size="24" font-weight="bold">${this.escapeXml(showing.title)}</text>
                <text x="200" y="130" text-anchor="middle" fill="rgba(255,255,255,0.7)" font-family="sans-serif" font-size="14">${this.escapeXml(showing.app_name)}</text>
                <rect x="150" y="180" width="100" height="25" fill="rgba(255,255,255,0.1)" rx="12"/>
                <text x="200" y="197" text-anchor="middle" fill="rgba(255,255,255,0.5)" font-family="sans-serif" font-size="10">DEMO</text>
            </svg>
        `.trim();

        return 'data:image/svg+xml;base64,' + btoa(svg);
    },

    // Generate mock camera URL with SVG placeholder
    getMockCameraUrl(cameraTitle) {
        const timestamp = new Date().toLocaleTimeString();
        const hash = this.hashString(cameraTitle || 'camera');
        const variation = (hash % 20) - 10; // Small random variation

        const svg = `
            <svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" viewBox="0 0 640 480">
                <rect width="640" height="480" fill="#1a1a2e"/>
                <rect x="20" y="20" width="600" height="440" fill="#16213e" rx="10"/>

                <!-- Grid lines for camera effect -->
                <g stroke="#0f3460" stroke-width="1" opacity="0.5">
                    <line x1="0" y1="120" x2="640" y2="120"/>
                    <line x1="0" y1="240" x2="640" y2="240"/>
                    <line x1="0" y1="360" x2="640" y2="360"/>
                    <line x1="160" y1="0" x2="160" y2="480"/>
                    <line x1="320" y1="0" x2="320" y2="480"/>
                    <line x1="480" y1="0" x2="480" y2="480"/>
                </g>

                <!-- Simulated motion detection areas -->
                <rect x="${100 + variation}" y="${150 + variation}" width="80" height="120" fill="none" stroke="#00ff00" stroke-width="2" opacity="0.4"/>
                <rect x="${400 + variation}" y="${200 - variation}" width="60" height="80" fill="none" stroke="#00ff00" stroke-width="2" opacity="0.3"/>

                <!-- Camera icon -->
                <g transform="translate(270, 180)">
                    <rect x="0" y="20" width="100" height="70" fill="#e94560" rx="8"/>
                    <rect x="30" y="0" width="40" height="25" fill="#e94560" rx="5"/>
                    <circle cx="50" cy="55" r="25" fill="#1a1a2e"/>
                    <circle cx="50" cy="55" r="18" fill="#0f3460"/>
                    <circle cx="50" cy="55" r="8" fill="#e94560"/>
                </g>

                <!-- REC indicator with pulsing effect -->
                <circle cx="50" cy="50" r="8" fill="#e94560">
                    <animate attributeName="opacity" values="1;0.3;1" dur="1s" repeatCount="indefinite"/>
                </circle>
                <text x="70" y="55" fill="#e94560" font-family="monospace" font-size="16" font-weight="bold">DEMO</text>

                <!-- Camera title -->
                <text x="320" y="320" text-anchor="middle" fill="white" font-family="sans-serif" font-size="24" font-weight="bold">${this.escapeXml(cameraTitle || 'Camera')}</text>

                <!-- Timestamp -->
                <text x="320" y="360" text-anchor="middle" fill="#888" font-family="monospace" font-size="14">${this.escapeXml(timestamp)}</text>

                <!-- Emulator mode label -->
                <rect x="240" y="400" width="160" height="30" fill="#e94560" opacity="0.8" rx="5"/>
                <text x="320" y="420" text-anchor="middle" fill="white" font-family="sans-serif" font-size="12" font-weight="bold">DEMO CAMERA</text>
            </svg>
        `.trim();

        return 'data:image/svg+xml;base64,' + btoa(svg);
    },

    // Helper: Simple string hash for color generation
    hashString(str) {
        let hash = 0;
        for (let i = 0; i < str.length; i++) {
            const char = str.charCodeAt(i);
            hash = ((hash << 5) - hash) + char;
            hash = hash & hash; // Convert to 32bit integer
        }
        return Math.abs(hash);
    },

    // Helper: Escape XML special characters
    escapeXml(str) {
        return String(str).replace(/[<>&'"]/g, c => ({
            '<': '&lt;',
            '>': '&gt;',
            '&': '&amp;',
            "'": '&apos;',
            '"': '&quot;'
        }[c]));
    }
};

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    EmulatorModeManager.init();
});

// Make available globally
window.EmulatorModeManager = EmulatorModeManager;
