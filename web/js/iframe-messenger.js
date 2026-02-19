/**
 * Centralized iframe messaging for BeoSound 5c
 *
 * Handles all postMessage communication between the main UI
 * and embedded iframes (spotify, scenes, system pages).
 */

const IframeMessenger = {
    /** Dynamic iframe registrations (overrides static Constants.iframes) */
    _dynamicIframes: {},

    /**
     * Register a dynamic iframe for a route
     * @param {string} route - Route path (e.g. 'menu/usb')
     * @param {string} iframeId - DOM element ID of the iframe
     */
    registerIframe(route, iframeId) {
        this._dynamicIframes[route] = iframeId;
    },

    /**
     * Unregister a dynamic iframe for a route
     * @param {string} route - Route path to unregister
     */
    unregisterIframe(route) {
        delete this._dynamicIframes[route];
    },

    /**
     * Get the iframe ID for the current route
     * @param {string} route - Current route path
     * @returns {string|null} Iframe element ID or null
     */
    getIframeIdForRoute(route) {
        // Dynamic registrations override static
        if (this._dynamicIframes[route]) return this._dynamicIframes[route];
        const iframes = window.Constants?.iframes || {
            'menu/spotify': 'preload-spotify',
            'menu/scenes': 'scenes-iframe',
            'menu/system': 'system-iframe'
        };
        return iframes[route] || null;
    },

    /**
     * Get the iframe element for the current route
     * @param {string} route - Current route path
     * @returns {HTMLIFrameElement|null} Iframe element or null
     */
    getIframeForRoute(route) {
        const iframeId = this.getIframeIdForRoute(route);
        if (!iframeId) return null;
        return document.getElementById(iframeId);
    },

    /**
     * Check if the current route has an associated iframe
     * @param {string} route - Current route path
     * @returns {boolean} True if route has iframe
     */
    routeHasIframe(route) {
        return this.getIframeIdForRoute(route) !== null;
    },

    /**
     * Send a message to the iframe for the current route
     * @param {string} route - Current route path
     * @param {string} type - Message type ('nav', 'button', 'keyboard')
     * @param {object} data - Message data
     * @returns {boolean} True if message was sent
     */
    sendToRoute(route, type, data) {
        const iframe = this.getIframeForRoute(route);

        if (!iframe) {
            console.log(`No iframe for route: ${route}`);
            return false;
        }

        if (!iframe.contentWindow) {
            console.log(`Iframe contentWindow not available for route: ${route}`);
            return false;
        }

        const message = { type, ...data };

        try {
            iframe.contentWindow.postMessage(message, '*');
            console.log(`Sent ${type} message to ${this.getIframeIdForRoute(route)}:`, message);
            return true;
        } catch (error) {
            console.error(`Error sending message to iframe:`, error);
            return false;
        }
    },

    /**
     * Send a navigation wheel event to iframe
     * @param {string} route - Current route path
     * @param {object} navData - Navigation data with direction and speed
     * @returns {boolean} True if message was sent
     */
    sendNavEvent(route, navData) {
        return this.sendToRoute(route, 'nav', { data: navData });
    },

    /**
     * Send a button event to iframe
     * @param {string} route - Current route path
     * @param {string} button - Button name ('left', 'right', 'go')
     * @returns {boolean} True if message was sent
     */
    sendButtonEvent(route, button) {
        return this.sendToRoute(route, 'button', { button });
    },

    /**
     * Send a keyboard event to iframe
     * @param {string} route - Current route path
     * @param {KeyboardEvent} event - Original keyboard event
     * @returns {boolean} True if message was sent
     */
    sendKeyboardEvent(route, event) {
        return this.sendToRoute(route, 'keyboard', {
            key: event.key,
            code: event.code,
            ctrlKey: event.ctrlKey,
            shiftKey: event.shiftKey,
            altKey: event.altKey,
            metaKey: event.metaKey
        });
    },

    /**
     * List of routes that handle their own navigation via iframes
     * @returns {string[]} Array of route paths
     */
    getLocalHandledRoutes() {
        const staticRoutes = Object.keys(window.Constants?.iframes || {
            'menu/spotify': 'preload-spotify',
            'menu/scenes': 'scenes-iframe',
            'menu/system': 'system-iframe'
        });
        const dynamicRoutes = Object.keys(this._dynamicIframes);
        return [...new Set([...staticRoutes, ...dynamicRoutes])];
    }
};

// Make available globally
window.IframeMessenger = IframeMessenger;
