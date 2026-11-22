/**
 * Tests for fitting the interface to the screen (web/js/ui-scale.js).
 *
 * The interface is a fixed 1024x768 composition, so anywhere that is not
 * 4:3 gets black bars. Two things have to hold for that to look deliberate
 * rather than broken:
 *
 *   1. The fit is uniform and centred, and a viewport within a hair of the
 *      design renders 1:1 instead of resampling every glyph to recover a
 *      row of pixels nobody can see.
 *
 *   2. Full-screen media is exempt. It hangs on the stage, which is a
 *      sibling of body rather than a child of it — body is what carries
 *      the scale transform, and anything inside it is measured against the
 *      letterboxed design box and clipped at its edges. A stage that ends
 *      up inside body is the whole bug, silently: it still renders, just
 *      with bars down the sides of the canvas on a 16:9 screen.
 *
 * Run with: node --test tests/unit/js/test_ui_stage.js
 */

const { describe, it, beforeEach } = require('node:test');
const assert = require('node:assert/strict');

// ── Minimal DOM stub ──────────────────────────────────────────────────────

let props, docEl, body, created;

function makeEl(tag) {
    const el = {
        tagName: tag.toUpperCase(),
        id: '',
        children: [],
        parentNode: null,
        get isConnected() { return !!this.parentNode; },
        style: { setProperty: (k, v) => { props[k] = String(v); } },
        appendChild(child) {
            this.children.push(child);
            child.parentNode = this;
            return child;
        },
    };
    created.push(el);
    return el;
}

function loadFresh(width, height) {
    props = {};
    created = [];
    docEl = makeEl('html');
    body = makeEl('body');
    docEl.appendChild(body);
    global.window = { innerWidth: width, innerHeight: height, addEventListener() {} };
    global.document = { documentElement: docEl, body, createElement: makeEl };
    delete require.cache[require.resolve('../../../web/js/ui-scale.js')];
    require('../../../web/js/ui-scale.js');
    return global.window.UiScale;
}

// ── Fitting ───────────────────────────────────────────────────────────────

describe('fit', () => {
    it('letterboxes a 16:9 receiver, centred', () => {
        loadFresh(960, 540);
        // 960/1024 = 0.9375 wide, 540/768 = 0.703125 tall. The shorter one
        // wins, which leaves 120px of bar down each side.
        assert.equal(props['--ui-scale'], '0.703125');
        assert.equal(props['--ui-offset-x'], '120px');
        assert.equal(props['--ui-offset-y'], '0px');
    });

    it('renders 1:1 when the viewport is a hair under the design', () => {
        // The device's kiosk reports 1023x767 inside a 1024x768 panel.
        loadFresh(1023, 767);
        assert.equal(props['--ui-scale'], '1');
    });

    it('still scales up a viewport larger than the design', () => {
        loadFresh(2048, 1536);
        assert.equal(props['--ui-scale'], '2');
    });
});

// ── The stage ─────────────────────────────────────────────────────────────

describe('stage', () => {
    it('hangs beside body, never inside it', () => {
        const ui = loadFresh(960, 540);
        const stage = ui.stage();
        assert.equal(stage.parentNode, docEl);
        assert.notEqual(stage.parentNode, body);
        assert.equal(body.children.length, 0);
    });

    it('is one element, however many layers ask for it', () => {
        const ui = loadFresh(960, 540);
        assert.equal(ui.stage(), ui.stage());
        assert.equal(docEl.children.filter(e => e.id === 'ui-stage').length, 1);
    });

    it('comes back after the document is rebuilt under it', () => {
        const ui = loadFresh(960, 540);
        const first = ui.stage();
        first.parentNode = null;                  // detached
        const second = ui.stage();
        assert.notEqual(second, first);
        assert.equal(second.parentNode, docEl);
    });
});
