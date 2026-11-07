// Fit the interface to whatever screen it is being shown on.
//
// The UI is a fixed composition, authored at 1024x768: the arc geometry, the
// overlays and every inline SVG are written in those coordinates, and nothing
// under web/ reads the window size. On the device's own panel that is exactly
// the viewport, so the scale works out to 1 and this changes nothing at all.
//
// It matters everywhere else. A Google Cast receiver hands the page 960x540,
// which is 16:9 and so a third shorter in proportion than the design. Because
// html, body and .container all set overflow:hidden, the surplus is clipped
// rather than scrolled: the bottom of the arc simply is not there, with no
// scrollbar and no overflow for a script to notice. Measured on a BRAVIA, the
// document reported zero overflow in both axes while a sixth of the interface
// was missing.
//
// So letterbox rather than crop. Scale uniformly to fit and centre what is
// left over, which keeps the 4:3 composition intact — stretching an arc that
// was drawn around a circle would look worse than black bars.
(function () {
    'use strict';

    var DESIGN_W = 1024;
    var DESIGN_H = 768;

    // Near enough is better than exact. The kiosk on the device reports a
    // 1023x767 viewport inside its 1024x768 panel — a pixel of window
    // chrome — and fitting the design into that would resample the whole
    // interface by a tenth of a percent, softening every glyph to recover
    // one row of pixels nobody can see. Below this, render 1:1 and let the
    // edge clip, which is what the device did before any of this existed.
    var SNAP = 0.99;

    function apply() {
        var scale = Math.min(window.innerWidth / DESIGN_W,
                             window.innerHeight / DESIGN_H);
        // Only snaps upward, and only from just below: a viewport larger
        // than the design still scales up to fill it.
        if (scale > SNAP && scale < 1) scale = 1;
        var style = document.documentElement.style;
        style.setProperty('--ui-scale', scale);
        // Whole pixels: a half-pixel offset resamples just as surely as a
        // fractional scale does.
        style.setProperty('--ui-offset-x',
            Math.round((window.innerWidth - DESIGN_W * scale) / 2) + 'px');
        style.setProperty('--ui-offset-y',
            Math.round((window.innerHeight - DESIGN_H * scale) / 2) + 'px');
    }

    // ── The stage: everything that should NOT be letterboxed ──────────
    //
    // Letterboxing is right for the composition and wrong for full-screen
    // media. A canvas, a music video or a visualizer is meant to fill the
    // glass, and on a 16:9 receiver the bars put a third of the screen
    // beyond its reach.
    //
    // Such a layer cannot simply be a child of <body>, because body is
    // what carries the transform. That makes body the containing block for
    // every fixed box inside it, so position:fixed resolves against the
    // 1024x768 design box rather than the screen, and body's overflow:
    // hidden then clips anything reaching past it.
    //
    // The stage sits beside body instead, so a fixed layer mounted on it
    // is measured against the real viewport and 100% means the screen.
    // It holds no layout of its own: it is out of flow and zero-sized,
    // purely somewhere to hang those layers.
    var stageEl = null;

    function stage() {
        if (!stageEl || !stageEl.isConnected) {
            stageEl = document.createElement('div');
            stageEl.id = 'ui-stage';
            document.documentElement.appendChild(stageEl);
        }
        return stageEl;
    }

    // Consumers fall back to document.body when this file is absent, where
    // body is untransformed and 100% means the screen anyway.
    window.UiScale = { stage: stage };

    apply();
    window.addEventListener('resize', apply);
    // A Cast receiver can resize its viewport after the first paint.
    window.addEventListener('orientationchange', apply);
})();
