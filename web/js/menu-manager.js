/**
 * MenuManager — owns the menu item list, rendering, and source script loading.
 *
 * Manages:
 *  - menuItems[] — the ordered list of {title, path} objects
 *  - views{} — route → {title, content, preloadId, ...} definitions
 *  - Source script loading and iframe preloading
 *  - Menu item DOM rendering (static + FLIP-animated)
 *
 * Requires:
 *  - window.Constants, window.arcs, window.LaserPositionMapper
 *  - this.onNavigate(path) callback — wired by UIStore
 *  - this.onMenuLoaded(data) callback — wired by UIStore
 */

class MenuManager {
    constructor() {
        const c = (typeof window !== 'undefined' && window.Constants) || {};
        this.radius = c.arc?.radius || 1000;
        this.angleStep = c.arc?.menuAngleStep || 5;

        // Menu items from centralized constants (static views only — sources/webpages added by router)
        this.menuItems = (c.menuItems || [
            {title: 'PLAYING', path: 'menu/playing'},
            {title: 'SCENES', path: 'menu/scenes'},
            {title: 'SYSTEM', path: 'menu/system'},
            {title: 'SHOWING', path: 'menu/showing'}
        ]).map(item => ({title: item.title, path: item.path}));

        // View definitions keyed by route path
        this.views = {
            'menu/showing': {
                title: 'SHOWING',
                content: `
                    <div id="status-page" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; color: white; text-align: center; background-color: rgba(0,0,0,0.4);">
                        <div id="apple-tv-artwork-container" style="width: 60%; aspect-ratio: 1; margin: 20px; position: relative; display: flex; justify-content: center; align-items: center; overflow: hidden; border-radius: 8px; box-shadow: 0 5px 15px rgba(0,0,0,0.3);">
                            <img id="apple-tv-artwork" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" alt="Apple TV Media" style="width: 100%; height: 100%; object-fit: contain; transition: opacity 0.6s ease;">
                        </div>
                        <div id="apple-tv-media-info" style="width: 80%; padding: 10px;">
                            <div id="apple-tv-media-title" style="font-size: 24px; font-weight: bold; margin-bottom: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">—</div>
                            <div id="apple-tv-media-details" style="font-size: 18px; margin-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">—</div>
                            <div id="apple-tv-state">Unknown</div>
                        </div>
                    </div>`
            },
            'menu/system': {
                title: 'System',
                content: `
                    <div id="system-container" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;">
                        <iframe id="system-iframe" src="softarc/system.html" style="width: 100%; height: 100%; border: none;" allowfullscreen></iframe>
                    </div>
                `
            },
            'menu/scenes': {
                title: 'Scenes',
                content: `
                    <div id="scenes-container" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;">
                    </div>
                `,
                preloadId: 'preload-scenes'
            },
            'menu/playing': {
                title: 'PLAYING',
                content: `
                    <div id="now-playing" class="media-view">
                        <div class="playing-artwork-slot media-view-artwork">
                            <div class="playing-flipper">
                                <div class="playing-face playing-front">
                                    <img class="playing-artwork" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" alt="Album Art">
                                </div>
                                <div class="playing-face playing-back" style="display:none">
                                    <img class="playing-artwork-back" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" alt="">
                                </div>
                            </div>
                        </div>
                        <div class="playing-info-slot media-view-info">
                            <div class="media-view-state"></div>
                            <div id="media-title" class="media-view-title">—</div>
                            <div id="media-artist" class="media-view-artist">—</div>
                            <div id="media-album" class="media-view-album">—</div>
                        </div>
                    </div>`
            },
        };

        this._menuLoaded = false;
        this._menuRetries = 0;
        this._lastSelectedPath = null;  // highlight state (bolded item)
        this._lastClickedPath = null;   // last item a click was fired for

        // Deferred eviction of the current route after a menu rebuild.
        // After a router restart, dynamic sources start in state "gone" and
        // are omitted from /router/menu until their service re-registers a
        // few seconds later — so a missing route right after a rebuild is
        // usually transient. Grace period before we actually evict:
        this._evictionGraceMs = c.timeouts?.menuEvictionGrace || 12000;
        this._evictionTimer = null;
        this._evictionRoute = null;

        // Callbacks wired by UIStore
        this.onNavigate = null;      // (path) => void
        this.onMenuLoaded = null;    // (data) => void — called after _fetchMenu succeeds
    }

    // ── Router menu fetch ──

    async fetchMenu() {
        try {
            const resp = await fetch(`${window.AppConfig?.routerUrl || 'http://localhost:8770'}/router/menu`);
            const data = await resp.json();
            if (!data || !data.items) return;

            // Load source view scripts on demand
            for (const item of data.items) {
                if (item.dynamic && item.preset && !window.SourcePresets?.[item.preset]) {
                    await this.loadSourceScript(item.preset);
                }
            }

            // Rebuild menu items from router response
            const newItems = [];
            for (const item of data.items) {
                const path = `menu/${item.id}`;

                // Webpage items: iframe view, rebuilt on each entry and
                // unloaded on exit by ViewManager. The id deliberately does
                // NOT start with "preload-" — that prefix is what the
                // rescue selector in ViewManager matches to keep an iframe
                // alive off-screen, and an external page left running in
                // the background leaks resources (see the fd-leak note in
                // view-manager.js).
                if (item.type === 'webpage' && item.url) {
                    const containerId = `webpage-container-${item.id}`;
                    this.views[path] = {
                        title: item.title,
                        content: `<div id="${containerId}" class="webpage-container" style="position:absolute;top:0;left:0;width:100%;height:100%;"></div>`,
                        _webpage: { iframeId: `webpage-iframe-${item.id}`, containerId, url: item.url }
                    };
                    newItems.push({ title: item.title, path });
                }
                // Dynamic sources: always register view from preset (even if menu item already exists)
                else if (item.dynamic && item.preset) {
                    if (!window.SourcePresets?.[item.preset]) {
                        await this.loadSourceScript(item.preset);
                    }
                    const preset = window.SourcePresets?.[item.preset];
                    if (preset) {
                        newItems.push({ title: item.title, path: preset.item.path, dynamic: true });
                        if (preset.view) {
                            this.views[preset.item.path] = preset.view;
                        }
                    } else {
                        newItems.push({ title: item.title, path, dynamic: true });
                    }
                } else {
                    const existing = this.menuItems.find(m => m.path === path);
                    newItems.push(existing || { title: item.title, path });
                }
            }
            this.menuItems = newItems;

            // Sync to laser position mapper
            if (window.LaserPositionMapper?.updateMenuItems) {
                window.LaserPositionMapper.updateMenuItems(this.menuItems);
            }

            // The rebuild may have dropped the view currently on screen
            // (e.g. router restarted with a source removed) — navigate away
            // and clean up, same as removeMenuItem() does.
            this._cleanupRemovedRoute();

            this._menuLoaded = true;
            this._menuRetries = 0;

            // Notify UIStore so it can restore active source
            if (this.onMenuLoaded) this.onMenuLoaded(data);

            this.renderMenuItems();
            console.log(`[MENU] Loaded ${newItems.length} items from router (active: ${data.active_source || 'none'})`);
        } catch (e) {
            this._menuLoaded = true;
            const attempt = (this._menuRetries = (this._menuRetries || 0) + 1);
            if (attempt <= 15) {
                const delay = Math.min(attempt * 2000, 10000);
                console.log(`[MENU] Router unavailable, retrying in ${delay / 1000}s (attempt ${attempt})`);
                setTimeout(() => this.fetchMenu(), delay);
            } else {
                console.log('[MENU] Router unavailable after 15 attempts, using defaults');
            }
        }
    }

    /**
     * If the current route's menu entry no longer exists (after a wholesale
     * menu rebuild), schedule an eviction: navigate to an existing view and
     * drop the stale view definition — otherwise the user is stranded on a
     * ghost view that no laser position maps to.
     *
     * The eviction is DEFERRED, not immediate: fetchMenu() fires on every
     * media-WS reconnect, and right after a beo-router restart the menu
     * momentarily lacks dynamic sources (they re-register seconds later).
     * Evicting immediately would kick the user off SPOTIFY/RADIO/JOIN during
     * every deploy/OTA/config save. Instead we start a grace timer; if a
     * later fetchMenu()/menu update shows the route back, the timer is
     * cancelled (and the fire-time re-check below also verifies against the
     * CURRENT menu, so a route restored via addMenuItem() survives too).
     * Explicit removals via removeMenuItem() still evict immediately.
     */
    _cleanupRemovedRoute() {
        const route = this._currentRoute;
        if (!route || this.menuItems.some(m => m.path === route)) {
            // Route present (or nothing on screen) — cancel any pending eviction
            this._cancelPendingEviction();
            return;
        }

        if (this._evictionTimer) {
            if (this._evictionRoute === route) return; // grace timer already running
            this._cancelPendingEviction();             // pending for a stale route
        }

        console.log(`[MENU] Current view ${route} missing after menu rebuild — evicting in ${this._evictionGraceMs / 1000}s unless it returns`);
        this._evictionRoute = route;
        this._evictionTimer = setTimeout(() => {
            this._evictionTimer = null;
            const pending = this._evictionRoute;
            this._evictionRoute = null;

            // Re-check against CURRENT state at fire time — never evict on
            // stale data. User navigated away themselves, or the route came
            // back (fetchMenu rebuild or addMenuItem broadcast) → no-op.
            if (this._currentRoute !== pending) return;
            if (this.menuItems.some(m => m.path === pending)) return;

            console.log(`[MENU] Current view ${pending} still missing after grace period — navigating away`);
            if (this.onNavigate) {
                this.onNavigate('menu/playing');
                this._currentRoute = 'menu/playing';
            }
            delete this.views[pending];
        }, this._evictionGraceMs);
    }

    _cancelPendingEviction() {
        if (!this._evictionTimer) return;
        clearTimeout(this._evictionTimer);
        this._evictionTimer = null;
        this._evictionRoute = null;
    }

    /**
     * Dynamically load a source's view script (web/sources/{preset}/view.js).
     */
    loadSourceScript(preset) {
        return new Promise(resolve => {
            const existing = document.head.querySelector(`script[data-preset="${preset}"]`);
            if (existing) existing.remove();

            const script = document.createElement('script');
            // BEO_ASSET_V is set when this copy of the UI is served from a
            // static host that caches assets for hours (the website emulator).
            // Without it, a visitor can end up running a stale view script
            // against freshly deployed core JS. Unset on a device, where
            // beo-http sends no-store anyway.
            const v = window.BEO_ASSET_V ? `?v=${window.BEO_ASSET_V}` : '';
            script.src = `sources/${preset}/view.js${v}`;
            script.dataset.preset = preset;
            script.onload = () => {
                console.log(`[MENU] Loaded source script: ${preset}`);
                const sp = window.SourcePresets?.[preset];
                if (sp?.view?.preloadId && sp.view.iframeSrc) {
                    this.preloadSourceIframe(sp.view.preloadId, sp.view.iframeSrc);
                    if (sp.item?.path) {
                        window.IframeMessenger?.registerIframe(sp.item.path, sp.view.preloadId);
                    }
                }
                resolve();
            };
            script.onerror = () => {
                console.warn(`[MENU] Source script not found: ${preset}`);
                resolve();
            };
            document.head.appendChild(script);
        });
    }

    // ── Iframe preloading ──

    preloadIframes() {
        const iframesToPreload = [
            { id: 'preload-scenes', src: 'softarc/scenes.html' }
        ];

        let preloadContainer = document.getElementById('iframe-preload-container');
        if (!preloadContainer) {
            preloadContainer = document.createElement('div');
            preloadContainer.id = 'iframe-preload-container';
            preloadContainer.style.cssText = 'position:absolute;left:-9999px;top:-9999px;width:1px;height:1px;overflow:hidden;';
            document.body.appendChild(preloadContainer);
        }

        iframesToPreload.forEach(({ id, src }) => {
            if (!document.getElementById(id)) {
                const iframe = document.createElement('iframe');
                iframe.id = id;
                iframe.src = src;
                iframe.style.cssText = 'width:1024px;height:768px;border:none;';
                preloadContainer.appendChild(iframe);
                console.log(`[PRELOAD] Loading ${src}`);
            }
        });
    }

    preloadSourceIframe(id, src) {
        if (document.getElementById(id)) return;

        let preloadContainer = document.getElementById('iframe-preload-container');
        if (!preloadContainer) {
            preloadContainer = document.createElement('div');
            preloadContainer.id = 'iframe-preload-container';
            preloadContainer.style.cssText = 'position:absolute;left:-9999px;top:-9999px;width:1px;height:1px;overflow:hidden;';
            document.body.appendChild(preloadContainer);
        }

        const iframe = document.createElement('iframe');
        iframe.id = id;
        iframe.src = src;
        iframe.style.cssText = 'width:1024px;height:768px;border:none;';
        preloadContainer.appendChild(iframe);
        console.log(`[PRELOAD] Loading source iframe: ${src}`);
    }

    reloadAllSourceIframes() {
        for (const sp of Object.values(window.SourcePresets || {})) {
            if (sp.view?.preloadId) {
                const iframe = document.getElementById(sp.view.preloadId);
                if (iframe?.contentWindow) {
                    iframe.contentWindow.postMessage({ type: 'reload-data' }, '*');
                }
            }
        }
    }

    attachPreloadedIframe(preloadId) {
        let containerId = null;
        let iframeSrc = null;

        for (const sp of Object.values(window.SourcePresets || {})) {
            if (sp.view?.preloadId === preloadId) {
                containerId = sp.view.containerId;
                iframeSrc = sp.view.iframeSrc;
                break;
            }
        }

        if (!containerId) {
            const builtins = {
                'preload-scenes': { container: 'scenes-container', src: 'softarc/scenes.html' },
                'preload-security': { container: 'security-container', src: 'softarc/security.html' },
            };
            const b = builtins[preloadId];
            if (b) { containerId = b.container; iframeSrc = b.src; }
        }

        if (!containerId) {
            console.warn(`[PRELOAD] No mapping for ${preloadId}`);
            return;
        }

        const container = document.getElementById(containerId);
        if (!container) {
            console.warn(`[PRELOAD] Container ${containerId} not found`);
            return;
        }

        let iframe = container.querySelector('iframe');
        if (iframe) {
            console.log(`[PRELOAD] Iframe already in ${containerId}`);
            return;
        }

        iframe = document.getElementById(preloadId);
        if (iframe) {
            iframe.style.cssText = 'width: 100%; height: 100%; border: none; border-radius: 8px; box-shadow: 0 5px 15px rgba(0,0,0,0.3);';
            container.appendChild(iframe);
            console.log(`[PRELOAD] Attached ${preloadId} to ${containerId}`);
        } else if (iframeSrc) {
            iframe = document.createElement('iframe');
            iframe.id = preloadId;
            iframe.src = iframeSrc;
            iframe.style.cssText = 'width: 100%; height: 100%; border: none; border-radius: 8px; box-shadow: 0 5px 15px rgba(0,0,0,0.3);';
            container.appendChild(iframe);
            console.log(`[PRELOAD] Created fresh iframe for ${containerId}`);
        }

        // Resume any ArcList that was destroyed on the last nav-away. revive()
        // is idempotent (guards against double-start), so calling it on a
        // freshly-loaded iframe is safe — it's a no-op until destroy() runs.
        try {
            const inst = iframe?.contentWindow?.arcListInstance || iframe?.contentWindow?.arcList;
            if (inst?.revive) inst.revive();
        } catch (e) { /* iframe not ready / cross-origin */ }
    }

    // ── Rendering ──

    /**
     * Angle step between menu items. Delegates to LaserPositionMapper so the
     * visual arc and the laser selection zones always use the same spacing
     * (it compresses below the default 5° when the menu grows — see
     * getMenuAngleStepFor in laser-position-mapper.js).
     */
    getAngleStep() {
        const mapper = (typeof window !== 'undefined' && window.LaserPositionMapper) || null;
        return mapper?.getMenuAngleStepFor
            ? mapper.getMenuAngleStepFor(this.menuItems.length)
            : this.angleStep;
    }

    getStartItemAngle() {
        const visibleCount = this.menuItems.length;
        const totalSpan = this.getAngleStep() * (visibleCount - 1);
        return 180 - totalSpan / 2;
    }

    _ensureHoverDelegation(menuContainer) {
        if (this._hoverDelegated) return;
        this._hoverDelegated = true;
        // Delegated mouseenter-equivalent: fires once when the pointer crosses
        // from outside a list-item (or from a different one) into this one.
        // Keeps a single listener on the container, so re-rendering items does
        // not leak per-item listeners on the detached nodes.
        menuContainer.addEventListener('mouseover', (e) => {
            const item = e.target.closest?.('.list-item');
            if (!item || !menuContainer.contains(item)) return;
            const from = e.relatedTarget?.closest?.('.list-item');
            if (item === from) return;
            const angle = parseFloat(item.dataset.angle);
            if (!Number.isNaN(angle) && this.onItemHover) this.onItemHover(angle);
        });
    }

    renderMenuItems() {
        const menuContainer = document.getElementById('menuItems');
        if (!menuContainer) return;
        this._ensureHoverDelegation(menuContainer);
        menuContainer.innerHTML = '';

        const visibleItems = this.menuItems;
        const angleStep = this.getAngleStep();
        visibleItems.forEach((item, index) => {
            const itemElement = document.createElement('div');
            itemElement.className = 'list-item';
            itemElement.dataset.path = item.path;
            itemElement.textContent = item.title;

            const itemAngle = this.getStartItemAngle() + (visibleItems.length - 1 - index) * angleStep;
            const position = arcs.getArcPoint(this.radius, 20, itemAngle);
            itemElement.dataset.angle = String(itemAngle);

            Object.assign(itemElement.style, {
                position: 'absolute',
                left: `${position.x - 100}px`,
                top: `${position.y - 25}px`,
                width: '100px',
                height: '50px',
                cursor: 'pointer'
            });

            if (item.path === this._lastSelectedPath) {
                itemElement.classList.add('selectedItem');
            }

            menuContainer.appendChild(itemElement);
        });
    }

    /**
     * Bold the menu item at selectedIndex; click when selectedPath changes.
     */
    applyMenuHighlight(selectedIndex, selectedPath) {
        const menuContainer = document.getElementById('menuItems');
        if (!menuContainer) return;

        const menuElements = menuContainer.querySelectorAll('.list-item');
        menuElements.forEach((el, i) => {
            if (i === selectedIndex) {
                el.classList.add('selectedItem');
            } else {
                el.classList.remove('selectedItem');
            }
        });

        this._lastSelectedPath = selectedPath;
        return this._shouldClick(selectedPath); // caller sends click command
    }

    /**
     * Decide whether a highlight change should fire a click. Clicks fire
     * only on a transition to a DIFFERENT item than the last one clicked —
     * not on the first highlight after boot, and not when re-entering the
     * same item after an excursion through an overlay/gap zone (path null).
     */
    _shouldClick(selectedPath) {
        const changed = !!(selectedPath && this._lastClickedPath &&
                           selectedPath !== this._lastClickedPath);
        if (selectedPath) this._lastClickedPath = selectedPath;
        return changed;
    }

    // ── Dynamic add/remove ──

    addMenuItem(item, afterPath, viewDef) {
        if (this.menuItems.some(m => m.path === item.path)) {
            console.log(`[MENU] Item ${item.path} already exists`);
            return;
        }

        const afterIndex = this.menuItems.findIndex(m => m.path === afterPath);
        const insertAt = afterIndex !== -1 ? afterIndex + 1 : this.menuItems.length;
        this.menuItems.splice(insertAt, 0, { title: item.title, path: item.path, dynamic: true });

        if (viewDef) {
            this.views[item.path] = viewDef;
        }

        if (window.LaserPositionMapper?.updateMenuItems) {
            window.LaserPositionMapper.updateMenuItems(this.menuItems);
        }

        console.log(`[MENU] Added "${item.title}" after ${afterPath} (now ${this.menuItems.length} items)`);
        this.renderMenuItemsAnimated();
    }

    removeMenuItem(path) {
        const index = this.menuItems.findIndex(m => m.path === path);
        if (index === -1) {
            console.log(`[MENU] Item ${path} not found`);
            return;
        }

        // If currently viewing the removed item, navigate to adjacent
        if (this.onNavigate && this._currentRoute === path) {
            const adjacentPath = this.menuItems[index - 1]?.path || this.menuItems[index + 1]?.path || 'menu/playing';
            this.onNavigate(adjacentPath);
        }

        this.menuItems.splice(index, 1);
        delete this.views[path];

        if (window.LaserPositionMapper?.updateMenuItems) {
            window.LaserPositionMapper.updateMenuItems(this.menuItems);
        }

        console.log(`[MENU] Removed "${path}" (now ${this.menuItems.length} items)`);
        this.renderMenuItemsAnimated();
    }

    /**
     * Re-render menu items with FLIP animation.
     * Existing items slide to their new positions, new items fade in.
     */
    renderMenuItemsAnimated() {
        const menuContainer = document.getElementById('menuItems');
        if (!menuContainer) return;
        this._ensureHoverDelegation(menuContainer);

        // --- FIRST: record old positions keyed by data-path ---
        const oldPositions = {};
        menuContainer.querySelectorAll('.list-item[data-path]').forEach(el => {
            const rect = el.getBoundingClientRect();
            oldPositions[el.dataset.path] = { left: rect.left, top: rect.top };
        });

        // --- Rebuild DOM ---
        menuContainer.innerHTML = '';
        const visibleItems = this.menuItems;
        const angleStep = this.getAngleStep();
        visibleItems.forEach((item, index) => {
            const itemElement = document.createElement('div');
            itemElement.className = 'list-item';
            itemElement.dataset.path = item.path;
            itemElement.textContent = item.title;

            const itemAngle = this.getStartItemAngle() + (visibleItems.length - 1 - index) * angleStep;
            const position = arcs.getArcPoint(this.radius, 20, itemAngle);
            itemElement.dataset.angle = String(itemAngle);

            Object.assign(itemElement.style, {
                position: 'absolute',
                left: `${position.x - 100}px`,
                top: `${position.y - 25}px`,
                width: '100px',
                height: '50px',
                cursor: 'pointer'
            });

            if (item.path === this._lastSelectedPath) {
                itemElement.classList.add('selectedItem');
            }

            menuContainer.appendChild(itemElement);
        });

        // --- LAST + INVERT + PLAY ---
        menuContainer.querySelectorAll('.list-item[data-path]').forEach(el => {
            const path = el.dataset.path;
            const newRect = el.getBoundingClientRect();

            if (oldPositions[path]) {
                const dx = oldPositions[path].left - newRect.left;
                const dy = oldPositions[path].top - newRect.top;
                if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) {
                    el.animate([
                        { transform: `translate(${dx}px, ${dy}px)` },
                        { transform: 'translate(0, 0)' }
                    ], { duration: 300, easing: 'ease-out' });
                }
            } else {
                el.animate([
                    { opacity: 0, transform: 'translateX(-20px)' },
                    { opacity: 1, transform: 'translateX(0)' }
                ], { duration: 300, delay: 150, easing: 'ease-out', fill: 'backwards' });
            }
        });
    }
}

if (typeof module !== 'undefined' && module.exports) {
    // Node.js environment (unit tests)
    module.exports = { MenuManager };
} else {
    // Browser environment
    window.MenuManager = MenuManager;
}
