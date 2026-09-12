// Demo backend for BeoSound 5c
//
// The UI normally talks to a dozen local services (beo-router on 8770, the
// player on 8766, one source service per music service, …).  Served from a
// plain static server there is nothing behind those ports, so the arc menu
// falls back to four items and every view sits in its empty state — which is
// what anyone running `python3 -m http.server` in web/ used to see, and what
// the site emulator has to work around.
//
// This file stands in for those services when demo mode is on: it answers the
// handful of requests that decide what the device *looks like* (the menu, the
// now-playing metadata, per-source browse data) with bundled demo content, and
// leaves everything else to fail exactly as it does on a device with a service
// down — so the demo exercises the real UI code paths, not a parallel mock UI.
//
// Loaded by index.html and by the softarc iframes (each frame has its own
// `fetch`, so each installs its own interception).  Inert unless demo mode is
// on — see AppConfig.demo.enabled in config.js.

(function () {
    // Works out demo mode without help: some pages that talk to beo-input
    // (config.html, system.html) don't load config.js at all, so relying on
    // AppConfig would leave them talking to a device that isn't there.
    function demoModeActive() {
        if (window.AppConfig?.demo?.enabled) return true;
        const param = new URLSearchParams(location.search).get('demo');
        if (param === 'true') return true;
        if (param === 'false') return false;
        // A device serves the UI over plain http on port 80. https reports an
        // empty port too, so the protocol has to be part of the test.
        const servedByDevice = location.protocol === 'http:'
            && (location.port === '' || location.port === '80');
        if (!servedByDevice && location.protocol !== 'file:') return true;
        try {
            return !!(window.parent !== window && window.parent.AppConfig?.demo?.enabled);
        } catch (e) {
            return false;   // cross-origin parent
        }
    }

    if (!demoModeActive()) return;
    if (window.__demoBackendInstalled) return;
    window.__demoBackendInstalled = true;
    if (window.AppConfig?.demo) window.AppConfig.demo.enabled = true;

    const DEMO_JSON = 'json/demo/';   // relative to web/

    // Path prefix back to web/ — index.html is at the root, softarc pages one
    // level down. Used so the same file works in both frames.
    const ROOT = location.pathname.includes('/softarc/') ? '../' : '';

    // ── Menu ──
    // Mirrors what beo-router builds from config.json: static views plus one
    // dynamic entry per registered source. Sources a demo can't fake (CD needs
    // a disc, USB needs a stick, JOIN needs speakers on the network) are left
    // out rather than shown broken.
    const MENU = {
        items: [
            { id: 'playing', title: 'PLAYING' },
            { id: 'spotify', title: 'SPOTIFY', preset: 'spotify', dynamic: true },
            { id: 'apple_music', title: 'APPLE MUSIC', preset: 'apple_music', dynamic: true },
            { id: 'tidal', title: 'TIDAL', preset: 'tidal', dynamic: true },
            { id: 'plex', title: 'PLEX', preset: 'plex', dynamic: true },
            { id: 'jellyfin', title: 'JELLYFIN', preset: 'jellyfin', dynamic: true },
            { id: 'radio', title: 'RADIO', preset: 'radio', dynamic: true },
            { id: 'home', title: 'HOME', type: 'webpage', url: 'softarc/home.html' },
            { id: 'scenes', title: 'SCENES' },
            { id: 'security', title: 'SECURITY', type: 'webpage',
              url: 'softarc/security.html' },
            { id: 'system', title: 'SYSTEM' },
        ],
        active_source: 'spotify',
        active_player: 'sonos',
    };

    // ── Now playing ──
    // Public-domain-ish placeholder metadata with generated artwork: no
    // third-party image hosts, nothing to rot, works offline.
    const TRACKS = [
        { title: 'Nocturne in E-flat major', artist: 'Chopin', album: 'Nocturnes', hue: 210 },
        { title: 'Clair de Lune', artist: 'Debussy', album: 'Suite bergamasque', hue: 265 },
        { title: 'Gymnopédie No. 1', artist: 'Satie', album: 'Trois Gymnopédies', hue: 25 },
        { title: 'The Blue Danube', artist: 'Strauss II', album: 'Waltzes', hue: 195 },
        { title: 'Prelude in C major', artist: 'Bach', album: 'Well-Tempered Clavier', hue: 145 },
    ];

    function artwork(track, size = 640) {
        // viewBox: without it the artwork does not scale when the UI draws it
        // larger than its natural size (the centred arc tile does exactly that).
        const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}"
             viewBox="0 0 ${size} ${size}">
            <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0" stop-color="hsl(${track.hue},45%,32%)"/>
                <stop offset="1" stop-color="hsl(${(track.hue + 40) % 360},50%,12%)"/>
            </linearGradient></defs>
            <rect width="${size}" height="${size}" fill="url(#g)"/>
            <text x="50%" y="46%" text-anchor="middle" fill="#fff" font-family="Helvetica Neue,sans-serif"
                  font-size="${Math.round(size / 16)}" letter-spacing="2">${esc(track.artist.toUpperCase())}</text>
            <text x="50%" y="56%" text-anchor="middle" fill="rgba(255,255,255,.72)"
                  font-family="Helvetica Neue,sans-serif" font-size="${Math.round(size / 22)}">${esc(track.album)}</text>
        </svg>`;
        return 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svg)));
    }

    function esc(s) {
        return String(s).replace(/[<>&'"]/g, c =>
            ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', "'": '&apos;', '"': '&quot;' }[c]));
    }

    let trackIndex = 0;
    let positionMs = 42_000;
    const DURATION_MS = 214_000;

    function mediaPayload() {
        if (externalMedia) {
            // Shape the shell's track like a player media update.
            return {
                title: externalMedia.title,
                artist: externalMedia.artist,
                album: externalMedia.album,
                artwork: externalMedia.artwork,
                artwork_url: externalMedia.artwork,
                playback_state: 'playing',
                state: 'playing',
                source: 'spotify',
            };
        }
        const t = TRACKS[trackIndex % TRACKS.length];
        const fmt = ms => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}`;
        return {
            title: t.title,
            artist: t.artist,
            album: t.album,
            artwork: artwork(t),
            artwork_url: artwork(t),
            playback_state: 'playing',
            state: 'playing',
            source: 'spotify',
            position_ms: positionMs,
            duration_ms: DURATION_MS,
            position: fmt(positionMs),
            duration: fmt(DURATION_MS),
            volume: 32,
        };
    }

    // ── Per-source browse data ──
    // Each music source runs its own service on its own port and the views
    // browse it over HTTP. Mapping port → demo file means the views take their
    // normal code path (fetch the service, render the arc list) with bundled
    // content standing in for the owner's real library.
    const SOURCE_PORTS = {
        '8771': 'spotify',
        '8773': 'usb',
        '8774': 'apple_music',
        '8776': 'news',
        '8777': 'tidal',
        '8778': 'plex',
        '8781': 'jellyfin',
        '8779': 'radio',
    };

    // Endpoint → demo file, per source. Shapes match what each service
    // returns, so the views parse them with their real mapping code:
    // flat playlist arrays for the music services, a {path,parent,name,items}
    // envelope for the browse-style ones (radio, usb).
    const SOURCE_DATA = {
        spotify:     { '/playlists': 'spotify_playlists.json' },
        apple_music: { '/playlists': 'apple_music_playlists.json' },
        tidal:       { '/playlists': 'tidal_playlists.json' },
        plex:        { '/playlists': 'plex_playlists.json' },
        jellyfin:    { '/playlists': 'jellyfin_playlists.json' },
        radio:       { '/browse': 'radio_browse.json', '/favourites': 'radio_favourites.json' },
        news:        { '/articles': 'news_articles.json' },
        usb:         { '/browse': 'usb_browse.json' },
    };

    // radio_browse.json is keyed by browse path; the others are served as-is.
    const KEYED_BY_PATH = new Set(['radio_browse.json']);

    // Per-device user data files some pages read directly.
    const PLAYLIST_FILES = {
        'spotify_playlists.json': 'spotify_playlists.json',
        'apple_music_playlists.json': 'apple_music_playlists.json',
        'tidal_playlists.json': 'tidal_playlists.json',
        'plex_playlists.json': 'plex_playlists.json',
        'jellyfin_playlists.json': 'jellyfin_playlists.json',
        'digit_playlists.json': 'digit_playlists.json',
        'scenes.json': 'scenes.json',
    };

    function jsonResponse(body, status = 200) {
        return new Response(JSON.stringify(body), {
            status,
            headers: { 'Content-Type': 'application/json' },
        });
    }

    // ── Fetch interception ──
    const realFetch = window.fetch.bind(window);

    window.fetch = function (input, init) {
        const url = typeof input === 'string' ? input : (input && input.url) || '';

        // Router menu — the one request that decides the whole arc.
        if (url.includes('/router/menu')) return Promise.resolve(jsonResponse(MENU));

        if (url.includes('/router/media') || url.includes('/player/media')) {
            return Promise.resolve(jsonResponse(mediaPayload()));
        }

        if (url.includes('/router/tone')) {
            return Promise.resolve(jsonResponse({
                supported: true, bass: 0, treble: 0, loudness: false,
            }));
        }

        if (url.includes('/router/status')) {
            return Promise.resolve(jsonResponse({
                active_source: 'spotify', active_source_name: 'Spotify',
                active_player: 'sonos', output_device: 'BeoLab 5',
                volume: 32, transport_mode: 'webhook',
            }));
        }

        // HOME view readouts. Plausible house readings so the view can be
        // exercised without a Home Assistant to talk to.
        if (url.includes('/ha/panel')) {
            return Promise.resolve(jsonResponse([
                { entity_id: 'sensor.outdoor_temperature', label: 'Outdoor', state: '4.2', unit: '°C', icon: '', available: true },
                { entity_id: 'sensor.indoor_temperature', label: 'Living room', state: '21.5', unit: '°C', icon: '', available: true },
                { entity_id: 'sensor.battery_level', label: 'Battery', state: '68', unit: '%', icon: 'battery-charging', available: true },
                { entity_id: 'sensor.solar_now', label: 'Solar', state: '1.4', unit: 'kW', icon: 'sun-dim', available: true },
                { entity_id: 'binary_sensor.front_door', label: 'Front door', state: 'closed', unit: '', icon: 'lock', available: true },
                { entity_id: 'person.demo', label: 'Someone', state: 'home', unit: '', icon: '', available: true },
                { entity_id: 'sensor.missing_example', label: 'Unconfigured sensor', state: 'unavailable', unit: '', icon: '', available: false },
            ]));
        }

        if (url.includes('/player/status') || url.includes('/player/state')) {
            return Promise.resolve(jsonResponse({
                state: 'playing', player: 'sonos', volume: 32,
                capabilities: ['sharelink', 'url_stream'],
            }));
        }

        // beo-input endpoints the SYSTEM and CONFIG pages call same-origin
        // (they go through beo-http's proxy on a device; here there is no
        // proxy and no beo-input, so answer them directly).
        const path = url.split('?')[0].replace(/^https?:\/\/[^/]+/, '');
        if (path === '/info' || path.endsWith('/info')) {
            return Promise.resolve(jsonResponse({
                ip_address: '192.168.1.100',
                hostname: 'beosound5c-demo',
                telemetry: true,
            }));
        }
        if (path === '/health') {
            return Promise.resolve(jsonResponse({
                status: 'ok', service: 'beo-input', screen: 'on', hid_connected: true,
            }));
        }
        if (path === '/people') {
            return Promise.resolve(jsonResponse([]));
        }
        if (path === '/bt/remotes') {
            return Promise.resolve(jsonResponse([
                { name: 'BeoRemote One', mac: 'AA:BB:CC:DD:EE:FF', connected: true },
            ]));
        }
        if (path === '/update/check') {
            return Promise.resolve(jsonResponse({
                current: 'demo', update_available: false, update_in_progress: false,
            }));
        }
        if (path.startsWith('/discover/')) {
            return Promise.resolve(jsonResponse([
                { ip: '192.168.1.50', name: 'Living Room' },
                { ip: '192.168.1.51', name: 'Kitchen' },
            ]));
        }
        if (path === '/config') {
            // Saving is not a thing in a demo — acknowledge and change nothing.
            return Promise.resolve(jsonResponse({
                status: 'ok', message: 'Demo mode — configuration was not saved',
            }));
        }

        // Demo playlist/scene data in place of per-device user files.
        for (const [name, demoName] of Object.entries(PLAYLIST_FILES)) {
            if (url.includes(name) && !url.includes('/demo/')) {
                return realFetch(ROOT + DEMO_JSON + demoName, init);
            }
        }

        // Source services: stand in for whichever one the view is browsing.
        const portMatch = url.match(/localhost:(87\d\d)(\/[^?]*)/);
        if (portMatch) {
            const source = SOURCE_PORTS[portMatch[1]];
            const path = portMatch[2];
            const method = (init?.method || 'GET').toUpperCase();

            if (method !== 'GET') {
                // Play/pause/next and friends: acknowledge so the UI doesn't
                // show an error, then let the media ticker carry the state.
                if (path === '/command') trackIndex++;
                return Promise.resolve(jsonResponse({ status: 'ok' }));
            }
            const file = source && SOURCE_DATA[source]?.[path];
            if (file) {
                return realFetch(ROOT + DEMO_JSON + file, init).then(async r => {
                    if (!r.ok) return jsonResponse([]);
                    if (!KEYED_BY_PATH.has(file)) return r;
                    // Browse endpoints take a ?path= and return that level.
                    const levels = await r.json();
                    const want = new URL(url, location.href).searchParams.get('path') || '';
                    return jsonResponse(levels[want] || levels[''] || { items: [] });
                });
            }
            if (path === '/setup-status') {
                return Promise.resolve(jsonResponse({
                    setup_status: {
                        player_type: 'sonos',
                        web_api_ready: true,
                        spotify_connect_ready: true,
                    },
                }));
            }
            if (path === '/status') {
                return Promise.resolve(jsonResponse({
                    state: 'playing', source: source || 'spotify', ...mediaPayload(),
                }));
            }
            if (path === '/queue') return Promise.resolve(jsonResponse({ queue: [] }));

            // Any other read on a source service (favourites, canvas, tokens…):
            // answer empty rather than let it hit a port with nothing behind
            // it. The views handle "nothing there" fine; a refused connection
            // just fills the console and stalls the setup page's probes.
            if (source && method === 'GET') return Promise.resolve(jsonResponse([]));
        }

        // Anything else that would hit a local service: let it fail the way a
        // device with that service stopped does, so the UI shows its real
        // fallback state instead of a mocked-up one.
        return realFetch(input, init);
    };

    // ── Playback simulation ──
    // Advances the position and rolls over to the next track, pushing updates
    // through the same uiStore entry point the media WebSocket uses.
    function pushMedia() {
        window.uiStore?.handleMediaUpdate?.(mediaPayload(), 'demo');
    }

    setInterval(() => {
        positionMs += 1000;
        if (positionMs >= DURATION_MS) {
            positionMs = 0;
            trackIndex++;
            if (!parentDrivesMedia) pushMedia();
        }
    }, 1000);

    // Only drive playback when nothing else is. Inside the website emulator the
    // shell pushes real tracks via postMessage, and both writing to the same
    // place is a race — this file pushed 100ms after the shell and won, so the
    // device showed a placeholder instead of the album the shell had queued.
    //
    // Being embedded at all is the test. Sniffing for a global in the parent
    // does not work: the shell declares its controller with `const`, and
    // top-level `const` is not a property of `window`, so the check silently
    // read undefined and this file kept clobbering the shell.
    const parentDrivesMedia = window.parent !== window;

    // Remember what the shell last pushed, so a later read of /router/media
    // (view mounts do this) answers with that rather than our placeholder.
    let externalMedia = null;
    if (parentDrivesMedia) {
        window.addEventListener('message', (event) => {
            const d = event.data;
            if (d && d.type === 'mock_track' && d.data) externalMedia = d.data;
        });
    }

    if (!parentDrivesMedia) {
        document.addEventListener('DOMContentLoaded', () => setTimeout(pushMedia, 600));
    } else {
        console.log('[DEMO] Parent frame drives playback — not pushing our own media');
    }

    window.DemoBackend = { MENU, TRACKS, mediaPayload, artwork };
    console.log('[DEMO] Backend active — menu, media and browse data are mocked');
})();
