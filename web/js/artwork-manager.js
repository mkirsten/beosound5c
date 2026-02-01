/**
 * Centralized artwork management for BeoSound 5c
 *
 * Handles artwork caching, preloading, and display transitions.
 * Used by both the Now Playing and SHOWING (Apple TV) views.
 */

const ArtworkManager = {
    // In-memory cache for loaded images
    cache: {},

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
                this.cache[url] = img;
                resolve(img);
            };
            img.onerror = () => {
                reject(new Error('Failed to load image'));
            };
            img.src = url;
        });
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

        // Hardcoded fallback placeholders in case Constants isn't loaded
        const defaultPlaceholders = {
            noArtwork: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Crect width='200' height='200' fill='%23333'/%3E%3Ctext x='100' y='100' font-family='Arial' font-size='14' fill='%23999' text-anchor='middle' dominant-baseline='middle'%3ENo Artwork%3C/text%3E%3C/svg%3E",
            artworkUnavailable: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Crect width='200' height='200' fill='%23333'/%3E%3Ctext x='100' y='100' font-family='Arial' font-size='14' fill='%23999' text-anchor='middle' dominant-baseline='middle'%3EArtwork Unavailable%3C/text%3E%3C/svg%3E",
            showing: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Crect width='200' height='200' fill='%23222'/%3E%3Ctext x='100' y='100' font-family='Arial' font-size='16' fill='%23666' text-anchor='middle' dominant-baseline='middle'%3ESHOWING%3C/text%3E%3C/svg%3E"
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

        // Preload and cache for future use
        this.preloadImage(artworkUrl)
            .then(img => {
                if (img) {
                    fadeIn(img.src);
                }
            })
            .catch(error => {
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
