/**
 * Tests for ArcList's in-place update path (web/softarc/script-v2.js).
 *
 * render() reuses the elements it already has when it can, which is what
 * keeps scrolling cheap: the wheel moves items along the arc dozens of times
 * a second and rebuilding the DOM each time would show.
 *
 * That decision used to be made on the item ids alone. Any list whose rows
 * keep their ids while their contents change therefore kept rendering the
 * text it was first given, with no way to tell from the outside: the data
 * reloaded, the item objects updated, and the screen did not move. It bit
 * the HOME view, whose Home Assistant readings refresh every 15 seconds
 * against unchanged entity ids, and the CAST view, where the receiver being
 * shown never lit up.
 *
 * The fix compares what is actually drawn and falls through to the full
 * render when it differs. Both halves matter, so both are pinned here:
 * unchanged content must still take the fast path.
 *
 * Run with: node --test tests/unit/js/test_arclist_content_refresh.js
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');

const { ArcList } = require('../../../web/softarc/script-v2.js');

// ── Minimal stubs ─────────────────────────────────────────────────────────
// _updateExistingElements() touches the container, the visible items and the
// elements' dataset/classList/style. Building a real DOM would be far more
// machinery than the decision under test needs.

class FakeEl {
    constructor(item) {
        this.dataset = {};
        if (item) {
            this.dataset.itemId = item.id;
            this.dataset.itemSig = signatureOf(item);
        }
        this.style = {
            setProperty(name, value) { this[name] = value; },
        };
        this._classes = new Set();
        this.classList = {
            add: (...c) => c.forEach(x => this._classes.add(x)),
            remove: (...c) => c.forEach(x => this._classes.delete(x)),
            contains: (c) => this._classes.has(c),
        };
        this._name = { classList: this.classList };
    }
    querySelector() { return this._name; }
}

function signatureOf(item) {
    return Object.create(ArcList.prototype)._itemSignature(item);
}

function makeList(items, domItems = items) {
    const list = Object.create(ArcList.prototype);
    list.currentIndex = 0;
    list.getVisibleItems = () => items.map((it, index) => ({ ...it, index, x: 0, y: 0, scale: 1 }));
    const els = domItems.map(it => new FakeEl(it));
    list.container = { querySelectorAll: () => els };
    list._els = els;
    return list;
}

const BASE = [
    { id: 'sensor.outdoor', name: 'Outside   4 °C', icon: 'thermometer-simple', color: '#fff' },
    { id: 'sensor.battery', name: 'Battery   81 %', icon: 'gauge', color: '#fff' },
];

function changed(field, value) {
    const copy = BASE.map(i => ({ ...i }));
    copy[0][field] = value;
    return copy;
}

describe('ArcList in-place update', () => {
    it('reuses elements when nothing visible has changed', () => {
        const list = makeList(BASE);
        assert.equal(list._updateExistingElements(), true);
        // And it really did update them, rather than returning early.
        assert.equal(list._els[0].style.transform, 'translate(0px, 0px) scale(1)');
    });

    it('falls through when a value in the label changes', () => {
        // The HOME case: same entity, new reading.
        const list = makeList(changed('name', 'Outside   5 °C'), BASE);
        assert.equal(list._updateExistingElements(), false);
    });

    it('falls through when the icon changes', () => {
        // The CAST case: the receiver now showing swaps its icon.
        const list = makeList(changed('icon', 'screencast'), BASE);
        assert.equal(list._updateExistingElements(), false);
    });

    it('falls through when the colour changes', () => {
        const list = makeList(changed('color', '#6699ff'), BASE);
        assert.equal(list._updateExistingElements(), false);
    });

    it('falls through when artwork changes', () => {
        const list = makeList(changed('image', 'http://example.invalid/a.jpg'), BASE);
        assert.equal(list._updateExistingElements(), false);
    });

    it('still falls through on a different id', () => {
        const list = makeList(changed('id', 'sensor.something_else'), BASE);
        assert.equal(list._updateExistingElements(), false);
    });

    it('still falls through when the count changes', () => {
        const list = makeList(BASE, [BASE[0]]);
        assert.equal(list._updateExistingElements(), false);
    });
});

describe('ArcList item signature', () => {
    it('separates fields so a value cannot migrate between them', () => {
        const sig = Object.create(ArcList.prototype);
        assert.notEqual(
            sig._itemSignature({ name: 'ab', icon: 'c' }),
            sig._itemSignature({ name: 'a', icon: 'bc' }));
    });

    it('tolerates a missing item', () => {
        assert.equal(Object.create(ArcList.prototype)._itemSignature(undefined), '');
    });
});
