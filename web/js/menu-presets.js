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
                <div id="cd-view" class="media-view">
                    <!-- Loading state: spinning disc -->
                    <div id="cd-loading" class="cd-loading-state">
                        <div class="cd-disc"></div>
                        <div class="cd-loading-text">Reading disc<span class="cd-dots"><span>.</span><span>.</span><span>.</span></span></div>
                    </div>
                    <!-- Artwork area: 3D flip container (hidden until metadata) -->
                    <div id="cd-artwork-area" class="media-view-artwork cd-artwork-container cd-hidden">
                        <div id="cd-flipper" class="cd-flipper">
                            <img id="cd-artwork-front" class="cd-flip-face cd-flip-front" src="assets/cd-disc.png" alt="Front Cover">
                            <img id="cd-artwork-back" class="cd-flip-face cd-flip-back" src="" alt="Back Cover" style="display:none">
                        </div>
                    </div>
                    <!-- Track list (shown via rotate, replaces artwork area) -->
                    <div id="cd-track-list" class="cd-track-list cd-hidden">
                        <div class="cd-track-list-title">—</div>
                        <div class="cd-track-list-items"></div>
                    </div>
                    <!-- Media info text (hidden until metadata) -->
                    <div id="cd-info" class="media-view-info cd-hidden">
                        <div class="media-view-title"></div>
                        <div class="media-view-artist"></div>
                        <div class="media-view-album"></div>
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
