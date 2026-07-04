/**
 * Spotify Canvas + Music Video Panel
 *
 * Displays looping video backgrounds during immersive mode.
 * Priority: music video (YouTube via Invidious) > Spotify Canvas > artwork.
 *
 * Cycle:
 *   music video present → 8s artwork → 25s video → repeat
 *   canvas only         → 15s artwork → one canvas loop (min 10s) → repeat
 *
 * Text metadata is handled by immersive-mode.js — this module only
 * manages the video layer and progress bar.
 */
(function() {
    'use strict';

    // ── Configuration ──
    var ARTWORK_SHOW_MS = 15000;       // artwork dwell when canvas only
    var ARTWORK_WITH_VIDEO_MS = 8000;  // artwork dwell when music video available
    var CANVAS_SHOW_MS = 10000;        // min canvas dwell (extends to loop duration)
    var VIDEO_SHOW_MS = 25000;         // fixed music video dwell (videos don't loop)
    var FADE_MS = 800;

    // ── State ──
    var active = false;
    var canvasUrl = '';       // Spotify Canvas URL for current track
    var musicVideoUrl = '';   // YouTube music video URL for current track
    var currentUrl = '';      // URL actually loaded in <video>
    var currentMode = null;   // 'video' | 'canvas' — what's loaded
    var currentTrackId = '';
    var videoReady = false;
    var container = null;
    var video = null;
    var textMirror = null;
    var progressBar = null;
    var cycleTimer = null;
    var cycling = false;
    var fadeRAF = null;

    // ── Helpers ──

    function isPaused() {
        var s = window.uiStore && window.uiStore.mediaInfo && window.uiStore.mediaInfo.state;
        return s === 'paused' || s === 'idle' || s === 'stopped';
    }

    function trackMatches() {
        if (!currentTrackId) return true;
        var live = window.uiStore && window.uiStore.mediaInfo && window.uiStore.mediaInfo.track_id;
        return live === currentTrackId;
    }

    // The best available URL and its mode, given current state
    function preferredUrl()  { return musicVideoUrl || canvasUrl; }
    function preferredMode() { return musicVideoUrl ? 'video' : 'canvas'; }
    function artworkShowMs() { return musicVideoUrl ? ARTWORK_WITH_VIDEO_MS : ARTWORK_SHOW_MS; }

    // ── DOM setup ──

    function ensureDOM() {
        if (container) return;

        container = document.createElement('div');
        container.className = 'canvas-panel';
        container.innerHTML =
            '<video class="canvas-video" autoplay muted playsinline></video>' +
            '<div class="canvas-text-mirror"></div>' +
            '<div class="canvas-progress"><div class="canvas-progress-fill"></div></div>';

        video = container.querySelector('.canvas-video');
        textMirror = container.querySelector('.canvas-text-mirror');
        progressBar = container.querySelector('.canvas-progress-fill');

        video.addEventListener('loadedmetadata', function() {
            applyVideoFit();
        });
        video.addEventListener('canplaythrough', function() {
            videoReady = true;
            applyVideoFit();  // re-apply: dimensions may have been 0 at loadedmetadata
            if (active) fadeIn();
            tryStartCycle();
        });
        video.addEventListener('timeupdate', updateProgress);
        video.addEventListener('error', function() {
            // Stop the retry cycle outright — leaving videoReady=false
            // with cycling on re-arms a 2s retry forever while nothing
            // re-attempts the load.
            videoReady = false;
            stopCycle();
        });

        document.body.appendChild(container);
    }

    // ── Show / Hide ──

    function animateOpacity(from, to, done) {
        if (fadeRAF) cancelAnimationFrame(fadeRAF);
        var start = null;
        function step(ts) {
            if (!start) start = ts;
            var p = Math.min((ts - start) / FADE_MS, 1);
            var eased = 1 - Math.pow(1 - p, 3);
            container.style.opacity = from + (to - from) * eased;
            if (p < 1) {
                fadeRAF = requestAnimationFrame(step);
            } else {
                fadeRAF = null;
                if (done) done();
            }
        }
        fadeRAF = requestAnimationFrame(step);
    }

    function fadeIn() {
        if (!container || !videoReady) return;
        container.style.pointerEvents = 'auto';
        video.play().catch(function() {});
        animateOpacity(parseFloat(container.style.opacity) || 0, 1);
    }

    function fadeOut() {
        if (!container) return;
        animateOpacity(parseFloat(container.style.opacity) || 1, 0, function() {
            container.style.pointerEvents = 'none';
            if (!active && video) video.pause();
        });
    }

    function syncTextMirror() {
        var src = document.querySelector('.immersive-info');
        if (!src || !textMirror) return;
        var rect = src.getBoundingClientRect();
        textMirror.style.cssText =
            'position:absolute;left:' + rect.left + 'px;top:' + rect.top + 'px;' +
            'width:' + rect.width + 'px;color:white;pointer-events:none;z-index:2;';
        textMirror.innerHTML = src.innerHTML;
        var srcKids = src.children;
        var mirKids = textMirror.children;
        for (var i = 0; i < srcKids.length && i < mirKids.length; i++) {
            var cs = getComputedStyle(srcKids[i]);
            mirKids[i].style.cssText =
                'font-size:' + cs.fontSize + ';font-weight:' + cs.fontWeight +
                ';margin-bottom:' + cs.marginBottom + ';opacity:' + cs.opacity +
                ';white-space:nowrap;overflow:hidden;text-overflow:ellipsis;' +
                'width:' + cs.width + ';';
            mirKids[i].textContent = srcKids[i].textContent;
        }
    }

    function show() {
        if (active) return;
        if (!currentUrl || !videoReady) return;
        if (isPaused()) return;
        if (!trackMatches()) return;
        active = true;
        syncTextMirror();
        fadeIn();
        document.dispatchEvent(new CustomEvent('bs5c:canvas-visibility', { detail: { visible: true } }));
    }

    function hide() {
        if (!active) return;
        active = false;
        fadeOut();
        document.dispatchEvent(new CustomEvent('bs5c:canvas-visibility', { detail: { visible: false } }));
    }

    // ── Auto-cycle ──

    function tryStartCycle() {
        if (cycling) return;
        if (!currentUrl || !videoReady) return;
        if (isPaused()) return;
        if (!trackMatches()) return;
        if (!window.ImmersiveMode || !window.ImmersiveMode.active) return;
        cycling = true;
        scheduleCanvas();
    }

    function stopCycle() {
        cycling = false;
        clearTimeout(cycleTimer);
        cycleTimer = null;
        if (active) hide();
        // Pause unconditionally — with autoplay+loop, a canvas stopped
        // during the artwork-dwell phase (before it was ever shown) keeps
        // decoding hidden video indefinitely otherwise.
        if (video && !video.paused) video.pause();
    }

    // Wait for artwork to finish, then attempt to show video.
    // If the video isn't ready yet (still loading), retry every 2s.
    function scheduleCanvas() {
        clearTimeout(cycleTimer);
        if (!cycling) return;
        cycleTimer = setTimeout(tryShowVideo, artworkShowMs());
    }

    function tryShowVideo() {
        if (!cycling || !currentUrl) return;
        if (!window.ImmersiveMode || !window.ImmersiveMode.active) { stopCycle(); return; }
        if (!videoReady) {
            // Still buffering — retry in 2s without resetting the artwork timer
            cycleTimer = setTimeout(tryShowVideo, 2000);
            return;
        }
        // On resume (second+ cycle): skip a few seconds ahead so the viewer
        // doesn't see the same moment they just left. The video was paused at
        // its last position; advance past that rather than rewinding to zero.
        if (currentMode === 'video' && video && video.currentTime > 2
                && video.duration && video.currentTime + 8 < video.duration) {
            video.currentTime += 5;
        }
        show();
        scheduleArtwork();
    }

    function scheduleArtwork() {
        clearTimeout(cycleTimer);
        if (!cycling) return;
        var duration;
        if (currentMode === 'video') {
            // Music videos are long and don't loop — use a fixed window
            duration = VIDEO_SHOW_MS;
        } else {
            // Canvas: extend to cover at least one full loop
            duration = CANVAS_SHOW_MS;
            if (video && video.duration && video.duration > 0) {
                var loopMs = video.duration * 1000;
                if (loopMs > duration) duration = loopMs;
            }
        }
        cycleTimer = setTimeout(function() {
            if (!cycling) return;
            if (!window.ImmersiveMode || !window.ImmersiveMode.active) { stopCycle(); return; }
            hide();
            // If a better video arrived while we were showing, switch now
            var p = preferredUrl();
            if (p && p !== currentUrl) loadVideo(p);
            scheduleCanvas();
        }, duration);
    }

    // ── Video fit ──

    // Apply object-position and zoom based on video mode and intrinsic dimensions.
    // Called from loadedmetadata (dimensions known) and on mode change (reset).
    function applyVideoFit() {
        if (!video) return;
        if (currentMode === 'canvas') {
            // Portrait 9:16 canvas: top-biased crop so subjects stay in frame
            video.style.objectPosition = 'center 20%';
            video.style.transform = '';
        } else {
            // Music video: centre crop; zoom in to fill vertically.
            // YouTube delivers cinematic (2.39:1, 2.35:1, 1.85:1) content inside a
            // 16:9 container — bars are baked into the pixels, so object-fit:cover
            // alone can't remove them. Compute zoom adaptively: assume the
            // worst-case content AR is 2.40:1 and derive the scale needed so
            // that content fills the panel with bars pushed beyond overflow:hidden.
            //   ar in 1.65–1.95 → 16:9-ish encode, bars likely baked in
            //   ar ≥ 1.95       → native ultra-wide (no bars), cover handles it
            //   ar < 1.65       → portrait/4:3, no zoom
            video.style.objectPosition = 'center center';
            var ar = (video.videoWidth && video.videoHeight)
                     ? video.videoWidth / video.videoHeight : 16 / 9;
            var zoom = 1.0;
            if (ar >= 1.65 && ar < 1.95) {
                // zoom = 2.40 / ar pushes 2.39:1 bars just outside overflow:hidden
                // e.g. ar=1.78 → zoom≈1.35; ar=1.85 → zoom≈1.30; ar=1.65 → zoom≈1.45
                zoom = 2.40 / ar;
            }
            video.style.transform = zoom > 1.01 ? ('scale(' + zoom.toFixed(2) + ')') : '';
        }
    }

    // ── Video loading ──

    function loadVideo(url) {
        if (!url) return;
        ensureDOM();
        var mode = (url === musicVideoUrl && musicVideoUrl) ? 'video' : 'canvas';
        if (url === currentUrl && mode === currentMode) return;  // already loaded
        currentUrl = url;
        currentMode = mode;
        currentTrackId = (window.uiStore && window.uiStore.mediaInfo
                          && window.uiStore.mediaInfo.track_id) || '';
        // Canvas videos loop; music videos don't (they're 3-5 min)
        video.loop = (mode === 'canvas');
        videoReady = false;
        // Reset fit while dimensions are unknown; applyVideoFit() finalises on loadedmetadata
        video.style.objectPosition = 'center center';
        video.style.transform = '';
        if (typeof Hls !== 'undefined' && Hls.isSupported() && url.indexOf('.m3u8') !== -1) {
            if (video._hls) { video._hls.destroy(); }
            var hls = new Hls({ enableWorker: false });
            video._hls = hls;
            hls.loadSource(url);
            hls.attachMedia(video);
        } else {
            if (video._hls) { video._hls.destroy(); video._hls = null; }
            video.src = url;
            video.load();
        }
    }

    function loadPreferred() {
        var url = preferredUrl();
        if (url) loadVideo(url);
    }

    function clearVideo() {
        canvasUrl = '';
        musicVideoUrl = '';
        currentUrl = '';
        currentMode = null;
        currentTrackId = '';
        videoReady = false;
        stopCycle();
        if (video) {
            // Tear down any HLS instance too — its loaders/buffers/
            // listeners otherwise linger until the next loadVideo() on a
            // shell that runs for weeks.
            if (video._hls) {
                try { video._hls.destroy(); } catch (e) { /* already dead */ }
                video._hls = null;
            }
            video.removeAttribute('src');
            video.load();
        }
    }

    // ── Progress bar ──

    function updateProgress() {
        if (!progressBar || !video || !video.duration) return;
        var pct = (video.currentTime / video.duration) * 100;
        progressBar.style.width = pct + '%';
    }

    // ── Event listeners ──

    function init() {
        var uiStore = window.uiStore;
        if (!uiStore) { setTimeout(init, 200); return; }

        ensureDOM();

        // Immediate stop when user presses prev/next — don't wait for the
        // server round-trip before the track_change event arrives.
        document.addEventListener('bs5c:skip', function() {
            stopCycle();
        });

        document.addEventListener('bs5c:media-update', function(e) {
            var reason = e.detail.reason;
            var data = e.detail.data || {};
            var cUrl = data.canvas_url || '';
            var vUrl = data.music_video_url || '';

            // Pause / stop → yank back to artwork immediately
            if (data.state && data.state !== 'playing') {
                stopCycle();
            }

            if (reason === 'track_change') {
                // New track — reset everything
                stopCycle();
                clearVideo();
                canvasUrl = cUrl;
                musicVideoUrl = vUrl;
                loadPreferred();
                tryStartCycle();
            } else {
                // Stale-track guard: if the track_id changed (e.g. external_control
                // arriving before track_change), stop the old video immediately.
                if (currentTrackId) {
                    var liveId = window.uiStore && window.uiStore.mediaInfo && window.uiStore.mediaInfo.track_id;
                    if (liveId && liveId !== currentTrackId) {
                        stopCycle();
                    }
                }

                // Same track — canvas or music video arrived (background injection)
                var changed = false;
                if ('canvas_url' in data && cUrl !== canvasUrl) {
                    canvasUrl = cUrl;
                    changed = true;
                }
                if ('music_video_url' in data && vUrl !== musicVideoUrl) {
                    musicVideoUrl = vUrl;
                    changed = true;
                }
                if (changed) {
                    var p = preferredUrl();
                    if (!p) {
                        // Both URLs cleared (e.g. switched to radio). Fully
                        // reset so the cycle can't restart with stale state
                        // and flash a black video panel between artworks.
                        clearVideo();
                    } else if (p !== currentUrl) {
                        // Better video available — load it; cycle picks it up
                        loadVideo(p);
                        tryStartCycle();
                    }
                }
            }
        });

        // Update text mirror when metadata changes during video
        document.addEventListener('bs5c:media-text-updated', function() {
            if (active) syncTextMirror();
        });

        // Sync dot classes when canvas/video URLs arrive asynchronously
        document.addEventListener('bs5c:media-update', function() {
            if (active) syncTextMirror();
        });

        // Menu open → pause cycle; menu close → resume
        document.addEventListener('bs5c:menu-visibility', function(e) {
            if (e.detail.visible) {
                stopCycle();
            } else {
                setTimeout(function() { tryStartCycle(); }, 800);
            }
        });

        // View change — stop when leaving now playing
        document.addEventListener('bs5c:view-change', function(e) {
            if (e.detail.to !== 'menu/playing') {
                stopCycle();
            }
            if (e.detail.to === 'menu/playing') {
                var info = uiStore.mediaInfo;
                if (info) {
                    if (info.canvas_url)       canvasUrl = info.canvas_url;
                    if (info.music_video_url)  musicVideoUrl = info.music_video_url;
                    loadPreferred();
                }
            }
        });

        // Seed from cached media state on init
        var info = uiStore.mediaInfo;
        if (info) {
            if (info.canvas_url)       canvasUrl = info.canvas_url;
            if (info.music_video_url)  musicVideoUrl = info.music_video_url;
            loadPreferred();
        }
    }

    // Expose for debugging / external coordination
    window.CanvasPanel = {
        show: show, hide: hide,
        get active()         { return active; },
        get cycling()        { return cycling; },
        get hasCanvas()      { return !!(canvasUrl || musicVideoUrl) && videoReady; },
        get hasMusicVideo()  { return !!musicVideoUrl && videoReady; },
        get currentMode()    { return currentMode; },
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() { setTimeout(init, 300); });
    } else {
        setTimeout(init, 300);
    }
})();
