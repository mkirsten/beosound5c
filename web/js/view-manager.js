/**
 * ViewManager — owns navigation, view lifecycle, and menu visibility.
 *
 * Manages:
 *  - currentRoute — which view is active
 *  - navigateToView() — route transitions with fade/overlay logic
 *  - updateView() — DOM swap, iframe rescue, preset lifecycle
 *  - Menu element visibility (show/hide the arc + pointer + items)
 *
 * Requires (wired by UIStore):
 *  - this.menuManager  — for views{}, attachPreloadedIframe, reloadAllSourceIframes
 *  - this.mediaManager — for setActivePlayingPreset, updateNowPlayingView
 */

class ViewManager {
    constructor() {
        this.currentRoute = 'menu/playing';
        this.navigationTimeout = null;
        this.menuVisible = true;
        this._previousRoute = null;

        // Set by UIStore
        this.menuManager = null;
        this.mediaManager = null;
    }

    // ── Navigation ──

    navigateToView(path) {
        if (path === this.currentRoute) return;

        if (this.navigationTimeout) {
            clearTimeout(this.navigationTimeout);
            this.navigationTimeout = null;
        }

        const from = this.currentRoute;

        // Start/stop Apple TV polling based on SHOWING view
        if (path === 'menu/showing' && from !== 'menu/showing') {
            this.mediaManager.setupAppleTVMediaInfoRefresh();
        } else if (from === 'menu/showing' && path !== 'menu/showing') {
            this.mediaManager.stopAppleTVMediaInfoRefresh();
        }

        this.currentRoute = path;

        document.dispatchEvent(new CustomEvent('bs5c:view-change', { detail: { from, to: path } }));

        this.reportViewToRouter(path);

        const isOverlayTransition = path === 'menu/playing' || path === 'menu/showing';

        if (path === 'menu/playing') {
            this.menuManager.reloadAllSourceIframes();
        }

        if (isOverlayTransition) {
            this.updateView();
            this.ensureContentVisible();
        } else {
            const contentArea = document.getElementById('contentArea');
            if (contentArea) {
                contentArea.style.opacity = 0;
                this.navigationTimeout = setTimeout(() => {
                    this.updateView();
                    this.navigationTimeout = null;
                }, 150);
            } else {
                this.updateView();
            }
        }
    }

    updateView() {
        const contentArea = document.getElementById('contentArea');
        if (!contentArea) {
            console.error('Content area not found');
            return;
        }

        const view = this.menuManager.views[this.currentRoute];
        if (!view) {
            console.error('View not found for route:', this.currentRoute);
            this.currentRoute = 'menu/playing';
            this.updateView();
            return;
        }

        // Teardown previous view's preset
        if (this._previousRoute && this._previousRoute !== this.currentRoute && window.SourcePresets) {
            for (const preset of Object.values(window.SourcePresets)) {
                if (preset.item.path === this._previousRoute && preset.onRemove) {
                    preset.onRemove();
                }
            }
        }
        this._previousRoute = this.currentRoute;

        // Tear down any iframe currently in the content area before it's
        // removed (preload- iframes get rescued below; non-preloaded ones
        // like system-iframe get GCed, but their timers/listeners keep
        // running until GC actually happens — call destroy() synchronously).
        contentArea.querySelectorAll('iframe').forEach(iframe => {
            try {
                const win = iframe.contentWindow;
                const inst = win?.arcListInstance || win?.arcList;
                if (inst?.destroy) inst.destroy();
                if (win?.systemPanel?.destroy) win.systemPanel.destroy();
            } catch (e) { /* cross-origin or unloaded iframe */ }
        });

        // Unload webpage iframes (external URLs from config, e.g. the HA
        // camera dashboard) instead of keeping them alive off-screen. An
        // external page we don't control goes on running in the background
        // — the HA camera dashboard holds live video streams, and each one
        // pins Chromium shared-memory segments. Church accumulated 983 of
        // them over 9 days and hit the renderer's 1024 soft fd limit, which
        // wedged the whole UI (frozen mid-crossfade, no media WS, main
        // thread parked in futex_wait). Blanking src unloads the document
        // now; without it the detached frame keeps streaming until GC.
        contentArea.querySelectorAll('iframe.webpage-iframe').forEach(iframe => {
            try { iframe.src = 'about:blank'; } catch (e) { /* already unloaded */ }
            iframe.remove();
        });

        // Rescue preloaded iframes before replacing content
        const preloadContainer = document.getElementById('iframe-preload-container');
        if (preloadContainer) {
            contentArea.querySelectorAll('iframe[id^="preload-"]').forEach(iframe => {
                iframe.style.cssText = 'width:1024px;height:768px;border:none;';
                preloadContainer.appendChild(iframe);
            });
        }

        contentArea.innerHTML = view.content;

        if (view.preloadId) {
            this.menuManager.attachPreloadedIframe(view.preloadId);
        }

        // Webpage views: build the iframe fresh on every entry. It was torn
        // down on the way out (see above), so there is nothing to reuse —
        // and a camera dashboard wants to be live anyway, not restored to
        // whatever it was showing hours ago.
        if (view._webpage) {
            const { iframeId, containerId, url } = view._webpage;
            const container = document.getElementById(containerId);
            if (container) {
                const iframe = document.createElement('iframe');
                iframe.id = iframeId;
                iframe.className = 'webpage-iframe';
                iframe.src = url;
                iframe.style.cssText = 'width:100%;height:100%;border:none;';
                container.appendChild(iframe);
            }
        }

        // Immediately update with cached info for playing view
        if (this.currentRoute === 'menu/playing') {
            this.mediaManager.activePlayingPreset = null;
            this.mediaManager.setActivePlayingPreset(this.mediaManager.activeSource);
            this.mediaManager.updateNowPlayingView();

            // Belt-and-suspenders: if our in-memory mediaInfo is empty
            // (e.g. broadcast was missed before this client connected, or
            // a Sonos external start raced view entry), pull the router's
            // cached state via HTTP and apply it. No-op if nothing cached.
            const mi = this.mediaManager.mediaInfo;
            const stale = !mi || !mi.title || mi.title === '—' || mi.state === 'idle' || mi.state === 'unknown';
            if (stale) {
                const url = `${window.AppConfig?.routerUrl || 'http://localhost:8770'}/router/media`;
                fetch(url).then(r => r.ok ? r.json() : null).then(data => {
                    if (data && data.title) {
                        this.mediaManager.handleMediaUpdate(data, 'view_entry_resync');
                    }
                }).catch(() => { /* router unreachable — preset already showed placeholder */ });
            }
        }
        else if (this.currentRoute === 'menu/showing') {
            this.mediaManager.updateAppleTVMediaView();
            this.mediaManager.fetchAppleTVMediaInfo();
        }
        // Fire onMount for dynamic menu presets
        if (window.SourcePresets) {
            for (const preset of Object.values(window.SourcePresets)) {
                if (preset.item.path === this.currentRoute && preset.onMount) {
                    preset.onMount();
                }
            }
        }

        // Fade content back in
        const isOverlayView = this.currentRoute === 'menu/playing' || this.currentRoute === 'menu/showing';
        if (isOverlayView) {
            contentArea.style.opacity = 1;
        } else {
            setTimeout(() => {
                contentArea.style.opacity = 1;
            }, 50);
        }
    }

    reportViewToRouter(view) {
        const url = `${window.AppConfig?.routerUrl || 'http://localhost:8770'}/router/view`;
        fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ view })
        }).catch(() => {}); // fire-and-forget
    }

    // ── Menu visibility ──

    setMenuVisible(visible) {
        if (this.menuVisible === visible) return;

        this.menuVisible = visible;
        document.dispatchEvent(new CustomEvent('bs5c:menu-visibility', { detail: { visible } }));

        const menuElements = this.getMenuElements();
        if (menuElements.length === 0) {
            console.warn('No menu elements found for visibility control');
            return;
        }

        menuElements.forEach(element => {
            element.style.transition = 'none';
            element.style.display = visible ? 'block' : 'none';
            element.style.opacity = visible ? '1' : '0';
            element.style.transform = 'translateX(0px)';
        });

        this.ensureContentVisible();
    }

    getMenuElements() {
        const menuItems = document.getElementById('menuItems');
        const mainArc = document.querySelector('#mainMenu svg');
        const anglePointer = document.getElementById('anglePointer');
        return [menuItems, mainArc, anglePointer].filter(el => el);
    }

    ensureContentVisible() {
        const contentArea = document.getElementById('contentArea');
        if (contentArea) {
            contentArea.style.transform = 'translateX(0px)';
            contentArea.style.visibility = 'visible';
            contentArea.offsetHeight; // force reflow
        }
    }

}

if (typeof module !== 'undefined' && module.exports) {
    // Node.js environment (unit tests)
    module.exports = { ViewManager };
} else {
    // Browser environment
    window.ViewManager = ViewManager;
}
