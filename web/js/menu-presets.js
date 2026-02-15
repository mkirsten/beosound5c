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
                    <div id="cd-album" class="cd-album-state">
                        <!-- Artwork area: 3D flip container -->
                        <div id="cd-artwork-area" class="cd-artwork-container">
                            <div id="cd-flipper" class="cd-flipper">
                                <img id="cd-artwork-front" class="cd-artwork cd-flip-face cd-flip-front" src="assets/cd-disc.png" alt="Front Cover">
                                <img id="cd-artwork-back" class="cd-artwork cd-flip-face cd-flip-back" src="" alt="Back Cover" style="display:none">
                            </div>
                        </div>
                        <!-- Track list (shown via rotate, replaces artwork area) -->
                        <div id="cd-track-list" class="cd-track-list cd-hidden">
                            <div class="cd-track-list-title">—</div>
                            <div class="cd-track-list-items"></div>
                        </div>
                        <!-- Media info text -->
                        <div class="cd-media-info">
                            <div class="cd-media-title">Loading</div>
                            <div class="cd-media-artist"></div>
                            <div class="cd-media-album"></div>
                        </div>
                    </div>
                    <!-- Sub-panel overlay (airplay/database/tracks) -->
                    <div id="cd-sub-panel" class="cd-sub-panel cd-hidden"></div>
                </div>`
        },

        onAdd() {
            // No-op — loading sequence starts in onMount when user navigates to the view
        },

        /**
         * Called each time the CD view is rendered into the content area.
         * Initialises the CDView controller (icon bar).
         */
        onMount() {
            if (window.CDView) window.CDView.init();
        },

        onRemove() {
            if (window.CDView) window.CDView.destroy();
        }
    }
};
