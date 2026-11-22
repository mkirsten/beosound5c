/**
 * MediaManager — owns all "what's playing" state.
 *
 * Manages:
 *  - mediaInfo (now-playing metadata from router)
 *  - appleTVMediaInfo (SHOWING view, polled from backend)
 *  - activePlayingPreset (which renderer draws the PLAYING view)
 *  - activeSource / activeSourcePlayer
 *
 * Dispatches:
 *  - bs5c:media-update  { data, reason }   — after every media update
 *  - bs5c:media-text-updated               — after crossfade text swap (via crossfadeText)
 */

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
        document.dispatchEvent(new CustomEvent('bs5c:media-text-updated'));
    }, 200);
}
window.crossfadeText = crossfadeText;

/** Shared "is this media state playing" check (TRANSITIONING = Sonos between tracks). */
function isPlayingState(state) {
    return state === 'playing' || state === 'TRANSITIONING';
}
window.isPlayingState = isPlayingState;

// End of the ▶ resume flash — drop the class so the next edge can re-trigger it.
document.addEventListener('animationend', (e) => {
    if (e.animationName === 'bs5c-resume-flash') {
        document.body.classList.remove('playback-resumed');
    }
});

const DEFAULT_PLAYING_PRESET = {
    // All sources push metadata via the unified router media path.
    // media_update events reach this preset via handleMediaUpdate() → updateNowPlayingView().
    onUpdate(container, data) {
        const titleEl = container.querySelector('.media-view-title');
        const artistEl = container.querySelector('.media-view-artist');
        const albumEl = container.querySelector('.media-view-album');
        if (titleEl && titleEl.textContent !== (data.title || '—')) {
            window.ElapsedTime?.reset();
        }
        crossfadeText(titleEl, data.title || '—');
        crossfadeText(artistEl, data.artist || '—');
        crossfadeText(albumEl, data.album || '—');
        const img = container.querySelector('.playing-artwork');
        if (img && window.ArtworkManager) {
            // During idle/boot we haven't received real media yet — use the
            // fully transparent placeholder so the UI doesn't flash a
            // "no artwork" graphic that looks like a broken state. Once media
            // is actually playing/paused but the service didn't supply art, we
            // fall back to the silent vinyl-circle glyph.
            const hasMedia = data.state === 'playing' || data.state === 'paused';
            const placeholderType = hasMedia ? 'noArtwork' : 'blank';
            window.ArtworkManager.displayArtwork(img, data.artwork, placeholderType);
        }
        // Back artwork (show/hide back face based on availability)
        const backFace = container.querySelector('.playing-back');
        const backImg = container.querySelector('.playing-artwork-back');
        if (backFace && backImg) {
            if (data.back_artwork) {
                backImg.src = data.back_artwork;
                backFace.style.display = '';
            } else if (!backFace.querySelector('.cd-back-tracklist')) {
                // No back artwork and no source-populated content — hide
                backFace.style.display = 'none';
                // Un-flip if back was removed while flipped
                const flipper = container.querySelector('.playing-flipper');
                if (flipper) flipper.classList.remove('flipped');
            }
        }
    },
    onMount(container) {
        window.ElapsedTime?.start();
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
        window.ElapsedTime?.stop();
        const flipper = container.querySelector('.playing-flipper');
        if (flipper?._clickHandler) {
            flipper.removeEventListener('click', flipper._clickHandler);
        }
    }
};

class MediaManager {
    constructor() {
        this.mediaInfo = {
            title: '—',
            artist: '—',
            album: '—',
            artwork: '',
            canvas_url: '',
            track_id: '',
            state: 'idle'
        };

        this.appleTVMediaInfo = {
            title: '—',
            friendly_name: '—',
            app_name: '—',
            artwork: '',
            state: 'unknown'
        };

        this.activeSource = null;          // id of active source, or null
        this.activeSourcePlayer = null;    // "local" | "remote" | null
        this.activePlayingPreset = DEFAULT_PLAYING_PRESET;

        this._appleTVRefreshInterval = null;
    }

    // ── Now-playing (router media WS) ──

    handleMediaUpdate(data, reason = 'update') {
        console.log(`[MEDIA-WS] ${reason}: ${data.title} - ${data.artist}`);

        // canvas_url: on track_change, clear unless payload provides one
        // (new track = new canvas). On update, keep existing if not in payload
        // (canvas arrives async for the same track).
        const keepCanvas = reason !== 'track_change' && !('canvas_url' in data);
        const keepMusicVideo = reason !== 'track_change' && !('music_video_url' in data);
        // track_id: stamped by the router from the player's _track_uri
        // hint — used by canvas-panel.js to verify the canvas it's
        // about to show actually belongs to the currently playing
        // track. On track_change always replace; on update preserve
        // existing if payload doesn't include one (e.g. canvas_inject
        // re-broadcasts mutate canvas_url but keep the same track_id).
        const keepTrackId = reason !== 'track_change' && !('track_id' in data);
        const prevState = this.mediaInfo.state;
        this.mediaInfo = {
            title: data.title || '—',
            artist: data.artist || '—',
            album: data.album || '—',
            artwork: data.artwork || '',
            back_artwork: data.back_artwork || '',
            canvas_url: keepCanvas ? (this.mediaInfo.canvas_url || '') : (data.canvas_url || ''),
            music_video_url: keepMusicVideo ? (this.mediaInfo.music_video_url || '') : (data.music_video_url || ''),
            track_id: keepTrackId ? (this.mediaInfo.track_id || '') : (data.track_id || ''),
            state: data.state || 'unknown',
            position: data.position || '0:00',
            duration: data.duration || '0:00'
        };

        this._syncPlaybackStateClasses(prevState, this.mediaInfo);

        document.dispatchEvent(new CustomEvent('bs5c:media-update', {
            detail: { data: this.mediaInfo, reason }
        }));

        this.updateNowPlayingView();
    }

    /**
     * On-screen playback state (see styles.css "Playback state"):
     *   body.playback-paused  — a track is loaded but not playing. Draws the
     *                           ❚❚ glyph above the title and dims the artwork.
     *   body.playback-resumed — set on the not-playing → playing edge, flashes
     *                           ▶ once; the CSS animation's end removes it.
     * Also fires bs5c:playback-started on that edge (immersive-mode.js).
     */
    _syncPlaybackStateClasses(prevState, mi) {
        const hasTrack = !!(mi.title && mi.title !== '—');
        const paused = hasTrack && (mi.state === 'paused' || mi.state === 'stopped');
        document.body.classList.toggle('playback-paused', paused);

        const wasPlaying = isPlayingState(prevState);
        const nowPlaying = isPlayingState(mi.state);
        if (!wasPlaying && nowPlaying) {
            // Only flash when a state element is on screen; otherwise the
            // class would linger and flash late on the next PLAYING mount.
            if (document.querySelector('.media-view-state, .immersive-info-state')) {
                document.body.classList.remove('playback-resumed');
                document.body.offsetHeight;  // restart the animation if mid-flash
                document.body.classList.add('playback-resumed');
            }
            document.dispatchEvent(new CustomEvent('bs5c:playback-started'));
        }
    }

    updateNowPlayingView() {
        const container = document.getElementById('now-playing');
        if (container && this.activePlayingPreset?.onUpdate) {
            this.activePlayingPreset.onUpdate(container, this.mediaInfo);
        }
    }

    /**
     * Switch the PLAYING view to a source's preset (or default).
     */
    setActivePlayingPreset(sourceId) {
        const preset = sourceId && window.SourcePresets?.[sourceId]?.playing;
        const newPreset = preset || DEFAULT_PLAYING_PRESET;
        if (newPreset === this.activePlayingPreset) return;

        const container = document.getElementById('now-playing');
        if (!container) {
            this.activePlayingPreset = newPreset;
            return;
        }

        if (this.activePlayingPreset?.onRemove) this.activePlayingPreset.onRemove(container);
        this.activePlayingPreset = newPreset;
        if (this.activePlayingPreset.onMount) this.activePlayingPreset.onMount(container);
        this.updateNowPlayingView();
    }

    // ── Apple TV / SHOWING view ──

    async fetchAppleTVMediaInfo() {
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
            this.updateAppleTVMediaView();
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
            this.updateAppleTVMediaView();
        } catch (error) {
            console.error('Error fetching Apple TV info:', error);
        }
    }

    updateAppleTVMediaView() {
        const artworkEl = document.getElementById('apple-tv-artwork');
        const titleEl = document.getElementById('apple-tv-media-title');
        const detailsEl = document.getElementById('apple-tv-media-details');
        const stateEl = document.getElementById('apple-tv-state');

        if (titleEl) titleEl.textContent = this.appleTVMediaInfo.title;
        if (detailsEl) detailsEl.textContent = this.appleTVMediaInfo.app_name;
        if (stateEl) stateEl.textContent = this.appleTVMediaInfo.state;

        if (artworkEl && window.ArtworkManager) {
            window.ArtworkManager.displayArtwork(artworkEl, this.appleTVMediaInfo.artwork, 'showing');
        }
    }

    setupAppleTVMediaInfoRefresh() {
        this.fetchAppleTVMediaInfo();
        // Always clear first — re-entering SHOWING must not stack intervals.
        this.stopAppleTVMediaInfoRefresh();
        this._appleTVRefreshInterval = setInterval(() => {
            // Skip fetches while the tab is hidden — the Chromium kiosk
            // rarely backgrounds, but on the dev host this saves pointless
            // network traffic.
            if (document.visibilityState === 'hidden') return;
            this.fetchAppleTVMediaInfo();
        }, 5000);

        // Defense-in-depth: guarantee cleanup on page hide/unload so a
        // rogue interval can never outlive the document.
        if (!this._appleTVUnloadBound) {
            this._appleTVUnloadBound = () => this.stopAppleTVMediaInfoRefresh();
            window.addEventListener('pagehide', this._appleTVUnloadBound);
        }
    }

    stopAppleTVMediaInfoRefresh() {
        if (this._appleTVRefreshInterval) {
            clearInterval(this._appleTVRefreshInterval);
            this._appleTVRefreshInterval = null;
        }
    }
}

window.MediaManager = MediaManager;
window.DEFAULT_PLAYING_PRESET = DEFAULT_PLAYING_PRESET;
