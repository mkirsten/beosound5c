/**
 * Centralized artwork management for BeoSound 5c
 *
 * Handles artwork caching, preloading, and display transitions.
 * Used by both the Now Playing and SHOWING (Apple TV) views.
 */

const ArtworkManager = {
    // In-memory cache for loaded images (bounded LRU)
    cache: {},
    _cacheOrder: [],   // URLs in insertion order (oldest first)
    _maxCacheSize: 50, // keep at most 50 images in memory

    /**
     * Preload and cache an image
     * @param {string} url - Image URL to preload
     * @returns {Promise<HTMLImageElement|null>} Loaded image or null
     */
    preloadImage(url) {
        return new Promise((resolve, reject) => {
            if (!url) return resolve(null);

            // Return cached image if available
            if (this.cache[url] && this.cache[url].complete) {
                return resolve(this.cache[url]);
            }

            const img = new window.Image();
            img.onload = () => {
                this._addToCache(url, img);
                resolve(img);
            };
            img.onerror = () => {
                reject(new Error('Failed to load image'));
            };
            img.src = url;
        });
    },

    _addToCache(url, img) {
        if (this.cache[url]) {
            // Move to end (most recently used)
            this._cacheOrder = this._cacheOrder.filter(u => u !== url);
        }
        this.cache[url] = img;
        this._cacheOrder.push(url);
        // Evict oldest entries beyond the limit
        while (this._cacheOrder.length > this._maxCacheSize) {
            const evicted = this._cacheOrder.shift();
            delete this.cache[evicted];
        }
    },

    /**
     * Display artwork with fade transition
     * Handles data URLs, cached images, and preloading
     *
     * @param {HTMLImageElement} imgElement - Target img element
     * @param {string} artworkUrl - URL of artwork to display
     * @param {string} placeholderType - Type of placeholder: 'noArtwork', 'artworkUnavailable', 'showing'
     */
    displayArtwork(imgElement, artworkUrl, placeholderType = 'noArtwork') {
        if (!imgElement) return;

        const fadeInDelay = window.Constants?.timeouts?.artworkFadeIn || 100;
        const fadeInComplete = window.Constants?.timeouts?.artworkFadeInComplete || 20;

        // Hardcoded fallback placeholders in case Constants isn't loaded.
        // Must stay in sync with web/js/constants.js `placeholders`.
        const SILENT = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'><rect width='200' height='200' fill='%231a1a1a'/><circle cx='100' cy='100' r='62' stroke='%23333' stroke-width='1.5' fill='none'/><circle cx='100' cy='100' r='24' stroke='%23333' stroke-width='1' fill='none'/><circle cx='100' cy='100' r='4' fill='%23333'/></svg>";
        const defaultPlaceholders = {
            blank: "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7",
            noArtwork: SILENT,
            artworkUnavailable: SILENT,
            showing: SILENT
        };
        const placeholders = window.Constants?.placeholders || defaultPlaceholders;

        // Helper to fade in new artwork
        const fadeIn = (src) => {
            if (imgElement.src === src) return; // Already showing this image

            imgElement.style.opacity = 0;
            setTimeout(() => {
                imgElement.src = src;
                setTimeout(() => {
                    imgElement.style.opacity = 1;
                }, fadeInComplete);
            }, fadeInDelay);
        };

        // Remember the most recent request for this element — sync paths
        // update it too, so any older in-flight async load bails out.
        imgElement._artworkToken = artworkUrl;

        // No artwork URL - show placeholder
        if (!artworkUrl) {
            const placeholder = placeholders[placeholderType] || placeholders.noArtwork;
            imgElement.src = placeholder;
            imgElement.style.opacity = 1;
            return;
        }

        // Data URL (from direct Sonos API) - set immediately with fade
        if (artworkUrl.startsWith('data:')) {
            fadeIn(artworkUrl);
            return;
        }

        // Check cache first
        if (this.cache[artworkUrl] && this.cache[artworkUrl].complete) {
            fadeIn(this.cache[artworkUrl].src);
            return;
        }

        // Preload and cache for future use.  The token guard means a slow
        // load that resolves AFTER a newer track's artwork was applied
        // can't overwrite it (fast skips: track A's late resolve/reject
        // must not clobber track B's art).
        this.preloadImage(artworkUrl)
            .then(img => {
                if (img && imgElement._artworkToken === artworkUrl) {
                    fadeIn(img.src);
                }
            })
            .catch(error => {
                if (imgElement._artworkToken !== artworkUrl) return;
                console.error('Error loading artwork:', error.message);
                if (error.message.includes('0 bytes')) {
                    console.warn('Home Assistant media player proxy returned 0 bytes - this is a known issue with Sonos artwork URLs');
                }
                // Show error placeholder
                const placeholder = placeholders.artworkUnavailable || placeholders.noArtwork;
                imgElement.src = placeholder;
                imgElement.style.opacity = 1;
            });
    },

    /**
     * Clear the artwork cache
     * Useful for memory management on long-running sessions
     */
    clearCache() {
        this.cache = {};
        this._cacheOrder = [];
    },

    /**
     * Get cache size for debugging
     * @returns {number} Number of cached images
     */
    getCacheSize() {
        return Object.keys(this.cache).length;
    }
};

// Make available globally
window.ArtworkManager = ArtworkManager;
