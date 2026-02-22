/**
 * Spotify View Controller
 *
 * Two contexts:
 *   menu/spotify  → Arc playlist/track browser
 *   menu/playing  → Now-playing artwork + metadata
 *
 * Arc browser shows playlists from playlists_with_tracks.json.
 * GO on playlist → show tracks. GO on track → play via spotify.py.
 * Nav wheel scrolls the arc, same softarc positioning as CD view.
 */
window.SpotifyView = (() => {
    const SPOTIFY_URL = window.AppConfig?.spotifyServiceUrl || 'http://localhost:8771';

    // ── State ──
    let menuActive = false;
    let nowPlaying = null;
    let playState = 'stopped';  // stopped | playing | paused
    let playlists = [];

    // Arc browser state
    let arcItems = [];
    let arcTargetIndex = 0;
    let arcCurrentIndex = 0;
    let arcAnimFrame = null;
    let arcSnapTimer = null;
    let lastScrollTime = 0;

    // View mode
    let viewMode = 'playlists';   // 'playlists' | 'tracks'
    let currentPlaylist = null;    // playlist object when in tracks mode
    let savedPlaylistIndex = 0;

    // Softarc constants (shared via ArcMath)
    const _ac = ArcMath.getConstants();
    const SCROLL_SPEED = _ac.scrollSpeed;
    const SCROLL_STEP = _ac.scrollStep;
    const SNAP_DELAY = _ac.snapDelay;
    const MIDDLE_INDEX = _ac.middleIndex;
    const BASE_ITEM_SIZE = _ac.baseItemSize;
    const MAX_RADIUS = _ac.maxRadius;
    const HORIZONTAL_MULTIPLIER = _ac.horizontalMultiplier;
    const BASE_X_OFFSET = _ac.baseXOffset;

    function resetState() {
        if (arcSnapTimer) clearTimeout(arcSnapTimer);
        if (arcAnimFrame) cancelAnimationFrame(arcAnimFrame);
        menuActive = false;
        arcItems = [];
        arcTargetIndex = 0;
        arcCurrentIndex = 0;
        arcAnimFrame = null;
        arcSnapTimer = null;
        lastScrollTime = 0;
        viewMode = 'playlists';
        currentPlaylist = null;
        savedPlaylistIndex = 0;
    }

    // ── Lifecycle ──

    function init() {
        if (!document.getElementById('spotify-view')) return;
        resetState();
        menuActive = true;
        loadPlaylists();
        console.log('[Spotify] View initialized');
    }

    function destroy() {
        resetState();
    }

    async function loadPlaylists() {
        // Try fetching from the service first, fallback to local JSON
        try {
            const resp = await fetch(`${SPOTIFY_URL}/playlists`);
            if (resp.ok) {
                playlists = await resp.json();
            } else {
                throw new Error(`HTTP ${resp.status}`);
            }
        } catch (e) {
            console.warn('[Spotify] Service unavailable, loading from JSON:', e.message);
            try {
                const resp = await fetch('json/playlists_with_tracks.json');
                if (resp.ok) playlists = await resp.json();
            } catch (e2) {
                console.warn('[Spotify] No playlists available');
                playlists = [];
            }
        }

        if (menuActive) {
            buildPlaylistArc();
            renderArc();
            startAnimation();
        }
    }

    // ── Arc Item Building ──

    function buildPlaylistArc() {
        viewMode = 'playlists';
        currentPlaylist = null;
        arcItems = playlists.map((pl, i) => ({
            id: pl.id,
            label: pl.name,
            image: pl.image,
            type: 'playlist',
            index: i,
            data: pl
        }));
        arcCurrentIndex = savedPlaylistIndex;
        arcTargetIndex = savedPlaylistIndex;
    }

    function buildTrackArc(playlist) {
        viewMode = 'tracks';
        currentPlaylist = playlist;
        savedPlaylistIndex = Math.round(arcTargetIndex);

        const tracks = playlist.tracks || [];
        arcItems = tracks.map((t, i) => ({
            id: t.id || `track-${i}`,
            label: t.name + (t.artist ? `\n${t.artist}` : ''),
            image: t.image || playlist.image,
            type: 'track',
            index: i,
            uri: t.uri || '',
            data: t
        }));

        // Add "Back" item at the end
        arcItems.push({
            id: '_back',
            label: 'Back',
            image: null,
            type: 'action',
            index: arcItems.length,
            icon: 'arrow-left'
        });

        arcCurrentIndex = 0;
        arcTargetIndex = 0;
    }

    // ── Arc Rendering (softarc positioning) ──

    function renderArc() {
        const container = document.getElementById('spotify-arc-container');
        if (!container) return;

        container.innerHTML = '';
        if (arcItems.length === 0) {
            container.innerHTML = '<div class="spotify-empty">No playlists found.<br>Run setup_spotify.py to connect.</div>';
            return;
        }

        for (let i = 0; i < arcItems.length; i++) {
            const item = arcItems[i];
            const el = document.createElement('div');
            el.className = 'spotify-arc-item';
            el.dataset.index = i;

            if (item.image) {
                const img = document.createElement('img');
                img.className = 'spotify-arc-image';
                img.src = item.image;
                img.loading = 'lazy';
                el.appendChild(img);
            } else if (item.icon) {
                const icon = document.createElement('i');
                icon.className = `ph ph-${item.icon} spotify-arc-icon`;
                el.appendChild(icon);
            } else {
                const placeholder = document.createElement('div');
                placeholder.className = 'spotify-arc-placeholder';
                placeholder.textContent = item.label?.[0] || '?';
                el.appendChild(placeholder);
            }

            const label = document.createElement('div');
            label.className = 'spotify-arc-label';
            // Handle multiline labels (track name + artist)
            const lines = item.label.split('\n');
            label.innerHTML = lines[0] + (lines[1] ? `<span class="spotify-arc-sublabel">${lines[1]}</span>` : '');
            el.appendChild(label);

            container.appendChild(el);
        }

        updateArcPositions();
    }

    function updateArcPositions() {
        const container = document.getElementById('spotify-arc-container');
        if (!container) return;

        const items = container.querySelectorAll('.spotify-arc-item');
        const screenH = container.offsetHeight || 768;
        const centerY = screenH / 2;

        items.forEach((el, i) => {
            const offset = i - arcCurrentIndex;
            const absOffset = Math.abs(offset);
            const isCenter = absOffset < 0.5;

            // Skip items too far away
            if (absOffset > MIDDLE_INDEX + 1) {
                el.style.display = 'none';
                return;
            }
            el.style.display = '';

            // Position calculation (matching softarc)
            const itemSpacing = BASE_ITEM_SIZE * 0.75;
            const y = centerY + offset * itemSpacing;

            // Arc curve (quadratic)
            const normalizedOffset = offset / MIDDLE_INDEX;
            const arcX = BASE_X_OFFSET + MAX_RADIUS * (1 - normalizedOffset * normalizedOffset) * HORIZONTAL_MULTIPLIER;

            // Scale and opacity
            const scale = Math.max(0.4, 1 - absOffset * 0.12);
            const opacity = Math.max(0.2, 1 - absOffset * 0.2);

            el.style.transform = `translate(${arcX}px, ${y - centerY}px) scale(${scale})`;
            el.style.opacity = opacity;
            el.classList.toggle('spotify-arc-selected', isCenter);
        });
    }

    // ── Animation ──

    function startAnimation() {
        if (arcAnimFrame) return;
        function tick() {
            const diff = arcTargetIndex - arcCurrentIndex;
            if (Math.abs(diff) > 0.01) {
                arcCurrentIndex += diff * SCROLL_SPEED;
                updateArcPositions();
            }
            arcAnimFrame = requestAnimationFrame(tick);
        }
        arcAnimFrame = requestAnimationFrame(tick);
    }

    function stopAnimation() {
        if (arcAnimFrame) {
            cancelAnimationFrame(arcAnimFrame);
            arcAnimFrame = null;
        }
    }

    function snapToNearest() {
        arcTargetIndex = Math.round(arcTargetIndex);
        arcTargetIndex = Math.max(0, Math.min(arcItems.length - 1, arcTargetIndex));
    }

    // ── Navigation Events ──

    function handleNavEvent(data) {
        if (!menuActive || arcItems.length === 0) return false;

        const dir = data.direction === 'clock' ? 1 : -1;
        const speed = Math.min(data.speed || 10, 30);
        const step = SCROLL_STEP * (1 + speed / 30);

        arcTargetIndex += dir * step;
        arcTargetIndex = Math.max(-0.4, Math.min(arcItems.length - 0.6, arcTargetIndex));

        lastScrollTime = Date.now();
        if (arcSnapTimer) clearTimeout(arcSnapTimer);
        arcSnapTimer = setTimeout(snapToNearest, SNAP_DELAY);

        return true;
    }

    function handleButton(button) {
        if (!menuActive) return false;

        if (button === 'go') {
            const idx = Math.round(arcTargetIndex);
            if (idx < 0 || idx >= arcItems.length) return true;
            const item = arcItems[idx];

            if (item.type === 'playlist') {
                buildTrackArc(item.data);
                renderArc();
            } else if (item.type === 'track') {
                playTrack(item);
            } else if (item.type === 'action' && item.id === '_back') {
                goBackToPlaylists();
            }
            return true;
        }

        if (button === 'left') {
            if (viewMode === 'tracks') {
                goBackToPlaylists();
                return true;
            }
            return false;
        }

        if (button === 'right') {
            if (viewMode === 'playlists') {
                const idx = Math.round(arcTargetIndex);
                if (idx >= 0 && idx < arcItems.length && arcItems[idx].type === 'playlist') {
                    buildTrackArc(arcItems[idx].data);
                    renderArc();
                    return true;
                }
            }
            return false;
        }

        return false;
    }

    function goBackToPlaylists() {
        buildPlaylistArc();
        renderArc();
    }

    // ── Playback ──

    async function playTrack(item) {
        const cmd = currentPlaylist
            ? { command: 'play_playlist', playlist_id: currentPlaylist.id, track_index: item.index }
            : { command: 'play_track', uri: item.uri };

        try {
            await fetch(`${SPOTIFY_URL}/command`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(cmd)
            });
        } catch (e) {
            console.warn('[Spotify] Command failed:', e);
        }
    }

    async function sendCommand(cmd) {
        try {
            await fetch(`${SPOTIFY_URL}/command`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: cmd })
            });
        } catch (e) {
            console.warn('[Spotify] Command failed:', e);
        }
    }

    // ── Metadata Updates ──

    function updateMetadata(data) {
        playState = data.state || 'stopped';
        if (data.now_playing) {
            nowPlaying = data.now_playing;
        }
    }

    // ── Public API ──

    return {
        init,
        destroy,
        handleNavEvent,
        handleButton,
        updateMetadata,
        sendCommand,
        get isActive() { return menuActive || (window.uiStore?.activeSource === 'spotify'); },
        get nowPlaying() { return nowPlaying; },
        get playState() { return playState; }
    };
})();

// ── Spotify Source Preset ──
window.SourcePresets = window.SourcePresets || {};
window.SourcePresets.spotify = {
    // No controller — nav/button events route to the softarc iframe via IframeMessenger
    item: { title: 'SPOTIFY', path: 'menu/spotify' },
    after: 'menu/playing',
    view: {
        title: 'SPOTIFY',
        content: `
            <div id="spotify-container" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%;">
            </div>`,
        preloadId: 'preload-spotify'
    },

    onAdd() {},

    onMount() {
        // The softarc iframe handles its own init via DOMContentLoaded
    },

    onRemove() {
        if (window.SpotifyView) window.SpotifyView.destroy();
    },

    // PLAYING sub-preset: use media_update from beo-player-sonos (handles artwork perfectly)
    // When Sonos is the output, beo-player-sonos polls and broadcasts artwork/metadata.
    // For librespot fallback, spotify.py sends media_update in the same format.
    playing: {
        eventType: 'media_update'
    }
};
