// Mock services for system.html — intercepts fetch and WebSocket in demo mode.
// Only loaded on Mac + localhost (see conditional <script> in system.html).
// Production devices (Raspberry Pi) never load this file.
(function () {
    // ── Mock data ──

    const services = {
        'beo-http': 'Running', 'beo-ui': 'Running', 'beo-input': 'Running',
        'beo-bluetooth': 'Running', 'beo-masterlink': 'Running',
        'beo-player-sonos': 'Running', 'beo-source-cd': 'Running',
        'beo-source-spotify': 'Running',
        'beo-source-usb': 'Inactive', 'beo-source-news': 'Running',
    };

    const system = {
        hostname: 'beosound5c-church', ip_address: '192.168.0.163',
        uptime: '3d 14h 22m', cpu_temp: '48.2°C', memory: '1.2G / 3.8G (32%)',
        git_tag: 'v0.9.2-dev',
    };

    const config = { DEVICE_NAME: 'Church' };

    const router = {
        active_source: 'spotify', active_source_name: 'Spotify',
        active_player: 'sonos', output_device: 'BeoLab 5',
        volume: 35, transport_mode: 'webhook',
        sources: {
            spotify: { name: 'Spotify', state: 'playing' },
            cd: { name: 'CD', state: 'stopped' },
            radio: { name: 'Radio', state: 'stopped' },
        },
    };

    const player = {
        player: 'sonos', name: 'Sonos', speaker_name: 'Church Living',
        speaker_ip: '192.168.0.190', state: 'playing', volume: 35,
        ws_clients: 1, artwork_cache_size: 42, is_grouped: false,
        current_track: { title: 'Gymnopédie No. 1', artist: 'Erik Satie', album: 'Gymnopédies' },
    };

    const people = [
        { friendly_name: 'Demo User', state: 'home', entity_picture: null },
        { friendly_name: 'Guest', state: 'away', entity_picture: null },
        { friendly_name: 'Family', state: 'home', entity_picture: null },
    ];

    const btRemotes = [
        { mac: '48:D0:CF:BD:34:BA', name: 'BEORC', connected: true, rssi: -52, battery: 85 },
        { mac: '48:D0:CF:BD:CE:35', name: 'BEORC', connected: false, rssi: null, battery: 60 },
    ];

    const spotify = {
        display_name: 'Markus Kirsten', has_credentials: true, needs_reauth: false,
        state: 'playing', playlist_count: 24, fetching: false,
        last_refresh: new Date().toISOString(), last_refresh_duration: 4.2,
        digit_playlists: {
            '0': 'Liked Songs', '1': 'Discover Weekly', '2': 'Classical',
            '3': 'Jazz', '4': 'Lo-Fi', '5': 'Dinner',
            '6': 'Party', '7': 'Sleep', '8': 'Focus', '9': 'Workout',
        },
    };

    const cd = {
        drive_connected: true, disc_inserted: true,
        metadata: { artist: 'Nils Frahm', title: 'All Melody', musicbrainz: true, artwork: null },
        playback: { state: 'stopped', current_track: 3, total_tracks: 12 },
    };

    const masterlink = { usb_connected: true, volume: 35, mute: false, speakers_on: true };

    // Drift CPU temp and memory slightly each call so sparklines animate
    let baseTemp = 48.2;
    function drift(base, range) { return base + (Math.random() - 0.5) * range; }

    function statusResponse() {
        baseTemp = Math.max(40, Math.min(60, baseTemp + (Math.random() - 0.5) * 3));
        const memPct = Math.max(20, Math.min(50, 32 + (Math.random() - 0.5) * 8));
        return {
            system: { ...system, cpu_temp: baseTemp.toFixed(1) + '°C', memory: `1.2G / 3.8G (${memPct.toFixed(0)}%)` },
            config, services, git_tag: system.git_tag,
        };
    }

    // ── URL → response map ──

    function mockResponse(url) {
        if (url.includes(':8767/webhook'))       return statusResponse();
        if (url.includes(':8770/router/status'))  return router;
        if (url.includes(':8766/player/status'))  return player;
        if (url.includes(':8767/bt/remotes'))     return btRemotes;
        if (url.includes(':8768/mixer/status'))   return masterlink;
        if (url.includes(':8771/status'))          return spotify;
        if (url.includes(':8769/status'))          return cd;
        if (url.includes(':8767/people'))          return people;
        return null;
    }

    // ── Intercept fetch ──

    const _realFetch = window.fetch;
    window.fetch = function (url, options) {
        const data = mockResponse(typeof url === 'string' ? url : url.url);
        if (data !== null) {
            return Promise.resolve(new Response(JSON.stringify(data), {
                status: 200,
                headers: { 'Content-Type': 'application/json' },
            }));
        }
        return _realFetch.apply(this, arguments);
    };

    // ── Intercept WebSocket (input service on :8765) ──

    const _RealWebSocket = window.WebSocket;
    window.WebSocket = function (url, protocols) {
        if (url.includes(':8765')) {
            const ws = {
                readyState: 1,
                OPEN: 1,
                send() {},
                close() { ws.readyState = 3; if (ws.onclose) ws.onclose(); },
            };
            setTimeout(() => {
                if (ws.onopen) ws.onopen();
                if (ws.onmessage) {
                    ws.onmessage({ data: JSON.stringify({ type: 'system_info', git_tag: system.git_tag }) });
                }
            }, 50);
            return ws;
        }
        return protocols ? new _RealWebSocket(url, protocols) : new _RealWebSocket(url);
    };
    Object.assign(window.WebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });

    console.log('[SYSTEM] Mock services active (demo mode)');
})();
