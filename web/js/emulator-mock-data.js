// Emulator Mock Data for BeoSound 5c
// Consolidates all mock/demo data in one place

const EmulatorMockData = {
    // === Camera images for security overlay ===
    cameras: {
        'Front door': 'https://images.unsplash.com/photo-1558036117-15d82a90b9b1?w=640&h=480&fit=crop',
        'Door': 'https://images.unsplash.com/photo-1558036117-15d82a90b9b1?w=640&h=480&fit=crop',
        'Gate': 'images/gate-camera.jpg',
        'default': 'https://images.unsplash.com/photo-1600596542815-ffad4c1539a9?w=640&h=480&fit=crop'
    },

    // === Apple TV / Showing view data ===
    appleTV: [
        {
            title: "Stranger Things",
            app_name: "Netflix",
            friendly_name: "Church TV",
            state: "playing",
            artwork: "https://image.tmdb.org/t/p/w500/49WJfeN0moxb9IPfGn8AIqMGskD.jpg"
        }
    ],

    // === System info ===
    systemInfo: {
        hostname: 'beosound5c-demo',
        ip_address: '192.168.1.100',
        uptime: '3 days, 14:22',
        cpu_temp: '45.2°C',
        memory_usage: '42%',
        disk_usage: '68%',
        wifi_signal: '-52 dBm',
        software_version: '2.1.0-demo'
    },

    // === Helper methods ===

    getCameraUrl(cameraTitle) {
        return this.cameras[cameraTitle] || this.cameras['default'];
    },

    getCurrentAppleTVShow(index = 0) {
        return this.appleTV[index % this.appleTV.length];
    },

    getSystemInfo() {
        return { ...this.systemInfo };
    },

    // Generate showing artwork placeholder if no real image
    generateShowingArtwork(showing) {
        if (showing.artwork) return showing.artwork;

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

    // Helper: Simple string hash
    hashString(str) {
        let hash = 0;
        for (let i = 0; i < str.length; i++) {
            const char = str.charCodeAt(i);
            hash = ((hash << 5) - hash) + char;
            hash = hash & hash;
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

// Make available globally
window.EmulatorMockData = EmulatorMockData;
