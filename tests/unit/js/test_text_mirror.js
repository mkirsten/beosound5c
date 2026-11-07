/**
 * Tests for the now-playing text copied onto a backdrop
 * (web/js/text-mirror.js).
 *
 * The text that is really on screen lives inside the scaled composition.
 * A full-bleed backdrop does not: it hangs on the stage, outside the
 * scale, so its box is in screen pixels while the source's computed sizes
 * are in design pixels. The copy has to cross that boundary.
 *
 * Measured on a 16:9 receiver before it did, with the scale at 0.703:
 * copying the source's viewport rect straight across put the lines 19px
 * left and 119px high of what they were copying, at a font size 42% too
 * large. Both are invisible in a test that only checks the strings, and
 * invisible on the device, where the scale is 1 and every ratio here is
 * one — which is exactly why they are worth pinning down.
 *
 * Run with: node --test tests/unit/js/test_text_mirror.js
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');

// ── Minimal DOM stub ──────────────────────────────────────────────────────

function el(rect, offsetWidth, children = []) {
    return {
        children,
        firstElementChild: null,
        innerHTML: '',
        style: { cssText: '' },
        offsetWidth,
        getBoundingClientRect: () => rect,
        appendChild(child) { this.firstElementChild = child; return child; },
    };
}

let source = null;
global.window = { addEventListener() {} };
global.document = {
    querySelector: (sel) => (sel === '.immersive-info' ? source : null),
    createElement: () => el({ left: 0, top: 0, width: 0, height: 0 }, 0),
    addEventListener() {},
};
global.getComputedStyle = () => ({});

require('../../../web/js/text-mirror.js');
const { render } = global.window.TextMirror;

/** The inline style render() wrote, as a lookup. */
function styleOf(host) {
    const out = {};
    for (const decl of host.firstElementChild.style.cssText.split(';')) {
        const i = decl.indexOf(':');
        if (i > 0) out[decl.slice(0, i).trim()] = decl.slice(i + 1).trim();
    }
    return out;
}

// The composition on a 16:9 receiver: 1024x768 fitted into 960x540, so
// 0.703125 and 120px of bar down each side. The .immersive-info block is
// 280px wide at design (675, 522).
const SCALE = 0.703125;
const SRC_RECT = { left: 120 + 675 * SCALE, top: 522 * SCALE, width: 280 * SCALE, height: 138 * SCALE };

describe('render', () => {
    it('places the copy over the line it is copying, on a full-bleed host', () => {
        source = el(SRC_RECT, 280);
        // The stage: a screen-sized box, outside the scale.
        const host = el({ left: 0, top: 0, width: 960, height: 540 }, 960);
        render(host);
        const s = styleOf(host);
        // Screen coordinates, because the host is in screen coordinates.
        assert.equal(s.left, '594.609375px');
        assert.equal(s.top, '367.03125px');
        // Laid out at design size and scaled, rather than written out at a
        // size the composition around it is not using.
        assert.equal(s.width, '280px');
        assert.equal(s.transform, 'scale(0.703125)');
    });

    it('places it in design coordinates on a host inside the scale', () => {
        source = el(SRC_RECT, 280);
        // The body: the design box, scaled and offset with the source.
        const host = el({ left: 120, top: 0, width: 1024 * SCALE, height: 768 * SCALE }, 1024);
        render(host);
        const s = styleOf(host);
        // Same place on screen, said in the host's own units — and no
        // scaling, because host and source are already scaled together.
        assert.equal(s.left, '675px');
        assert.equal(s.top, '522px');
        assert.equal(s.transform, 'scale(1)');
    });

    it('is a no-op transform at 1:1, as on the device', () => {
        source = el({ left: 675, top: 522, width: 280, height: 138 }, 280);
        const host = el({ left: 0, top: 0, width: 1023, height: 767 }, 1023);
        render(host);
        const s = styleOf(host);
        assert.equal(s.left, '675px');
        assert.equal(s.top, '522px');
        assert.equal(s.transform, 'scale(1)');
    });

    it('does not divide by an element that has no width yet', () => {
        source = el({ left: 0, top: 0, width: 0, height: 0 }, 0);
        const host = el({ left: 0, top: 0, width: 0, height: 0 }, 0);
        render(host);
        const s = styleOf(host);
        assert.equal(s.transform, 'scale(1)');
        assert.equal(s.left, '0px');
    });
});
