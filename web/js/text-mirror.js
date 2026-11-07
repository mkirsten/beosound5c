/**
 * Copies the now-playing text on top of a full-screen backdrop.
 *
 * The text that is actually visible on PLAYING lives in .immersive-info,
 * nested inside #viewport. #viewport carries a transform — the GPU hint
 * from hardware-input.js — which makes it a stacking context, so nothing
 * inside it can be raised above a backdrop painted on the body. The canvas
 * panel and the visualizer are both painted there.
 *
 * So the text is copied rather than raised: same strings, same computed
 * sizes, positioned over the original, inside the backdrop's own stacking
 * context. Both backdrops needed this; it lives here so there is one copy
 * of it rather than one each.
 *
 * A mirror is registered once and re-synced whenever what it is copying
 * changes — a track change, or the elapsed clock ticking.
 */
(function () {
    'use strict';

    const mirrors = new Set();

    /**
     * `host` is a full-viewport, positioned box belonging to the backdrop.
     * The copy goes inside it, at the original's coordinates.
     *
     * The geometry is set on that child and never on the host, because the
     * host's stacking is the backdrop's business: writing a z-index here
     * put a body-level layer on z-index 2 and buried the text under the
     * very backdrop it was supposed to sit on.
     */
    function render(host) {
        const src = document.querySelector('.immersive-info');
        if (!src || !host) return;
        let into = host.firstElementChild;
        if (!into) {
            into = document.createElement('div');
            host.appendChild(into);
        }
        const rect = src.getBoundingClientRect();
        into.style.cssText =
            'position:absolute;left:' + rect.left + 'px;top:' + rect.top + 'px;' +
            'width:' + rect.width + 'px;';
        into.innerHTML = src.innerHTML;
        const srcKids = src.children;
        const mirKids = into.children;
        for (let i = 0; i < srcKids.length && i < mirKids.length; i++) {
            const cs = getComputedStyle(srcKids[i]);
            mirKids[i].style.cssText =
                'font-size:' + cs.fontSize + ';font-weight:' + cs.fontWeight +
                ';margin-bottom:' + cs.marginBottom + ';opacity:' + cs.opacity +
                ';height:' + cs.height + ';line-height:' + cs.lineHeight +
                ';letter-spacing:' + cs.letterSpacing +
                ';visibility:' + cs.visibility +
                ';white-space:nowrap;overflow:hidden;text-overflow:ellipsis;' +
                'width:' + cs.width + ';';
            mirKids[i].textContent = srcKids[i].textContent;
        }
    }

    function register(el) { if (el) mirrors.add(el); }
    function unregister(el) { mirrors.delete(el); }

    function syncAll() {
        for (const el of mirrors) {
            // A mirror whose backdrop has gone is left alone rather than
            // unregistered behind the owner's back; it costs one check.
            if (el.isConnected) render(el);
        }
    }

    // A new track rewrites everything being copied. So does the elapsed
    // clock, once a second, which announces itself rather than reaching
    // into each mirror — see js/elapsed.js.
    document.addEventListener('bs5c:media-update', syncAll);
    document.addEventListener('bs5c:now-playing-text', syncAll);

    window.TextMirror = { render, register, unregister, syncAll };
})();
