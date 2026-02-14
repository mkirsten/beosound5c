/**
 * Menu Presets — self-contained definitions for dynamic menu items.
 *
 * Each preset owns its HTML template, loading logic, and lifecycle hooks.
 * Adding a new source type = adding one object to this registry.
 */
window.MenuPresets = {
    cd: {
        item: { title: 'CD', path: 'menu/cd' },
        after: 'menu/music',
        view: {
            title: 'CD',
            content: `
                <div id="cd-view" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; color: white; text-align: center;">
                    <!-- Loading state -->
                    <div id="cd-loading" class="cd-loading-state">
                        <div class="cd-disc"></div>
                        <div class="cd-loading-text">Reading disc<span class="cd-dots"><span>.</span><span>.</span><span>.</span></span></div>
                    </div>
                    <!-- Album state (hidden initially) -->
                    <div id="cd-album" class="cd-album-state cd-hidden">
                        <!-- Artwork area: 3D flip container -->
                        <div id="cd-artwork-area" class="cd-artwork-container">
                            <div id="cd-flipper" class="cd-flipper">
                                <img id="cd-artwork-front" class="cd-artwork cd-flip-face cd-flip-front" src="assets/cd-joyride.jpg" alt="Front Cover">
                                <img id="cd-artwork-back" class="cd-artwork cd-flip-face cd-flip-back" src="" alt="Back Cover">
                            </div>
                        </div>
                        <!-- Track list (shown via rotate, replaces artwork area) -->
                        <div id="cd-track-list" class="cd-track-list cd-hidden">
                            <div class="cd-track-list-title">—</div>
                            <div class="cd-track-list-items"></div>
                        </div>
                        <!-- Media info text -->
                        <div class="cd-media-info">
                            <div class="cd-media-title">—</div>
                            <div class="cd-media-artist">—</div>
                            <div class="cd-media-album">—</div>
                        </div>
                    </div>
                    <!-- Sub-panel overlay (airplay/database/tracks) -->
                    <div id="cd-sub-panel" class="cd-sub-panel cd-hidden"></div>
                </div>`
        },

        _timer: null,

        onAdd() {
            // No-op — loading sequence starts in onMount when user navigates to the view
        },

        /**
         * Called each time the CD view is rendered into the content area.
         * Initialises the CDView controller (icon bar) and starts the loading crossfade.
         * If cd_update metadata has already arrived, it will immediately reveal the album.
         * Otherwise the 2s timer provides a fallback crossfade (e.g. dev/demo mode).
         */
        onMount() {
            // Initialize CDView icon bar
            if (window.CDView) window.CDView.init();

            // Start fallback crossfade timer (real metadata will override via cd_update)
            if (this._timer) clearTimeout(this._timer);
            this._timer = setTimeout(() => {
                const loading = document.getElementById('cd-loading');
                const album = document.getElementById('cd-album');
                if (loading && album) {
                    loading.classList.add('cd-hidden');
                    album.classList.remove('cd-hidden');
                }
            }, 2000);
        },

        onRemove() {
            if (this._timer) {
                clearTimeout(this._timer);
                this._timer = null;
            }
            if (window.CDView) window.CDView.destroy();
        }
    }
};
