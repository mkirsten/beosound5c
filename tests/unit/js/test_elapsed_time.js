/**
 * Tests for the PLAYING elapsed-time clock (web/js/elapsed.js).
 *
 * The clock is two clocks: the player is asked where it is every few
 * seconds, and the wall clock carries the number between those answers. The
 * interpolation is where this can go quietly wrong — a paused track whose
 * digits keep climbing, or a count that runs past the end of the song —
 * and neither would throw, so both are pinned here.
 *
 * Run with: node --test tests/unit/js/test_elapsed_time.js
 */

const { describe, it, beforeEach } = require('node:test');
const assert = require('node:assert/strict');

let now = 0;
global.performance = { now: () => now };

// Two rows, because the clock writes to both the normal panel and the
// immersive overlay — the canvas panel mirrors the latter over a video, so
// a clock that only updated one of them would vanish the moment a music
// video started.
function fakeRow() {
    const n = { textContent: '', _classes: new Set() };
    n.classList = {
        add: (c) => n._classes.add(c),
        remove: (c) => n._classes.delete(c),
        contains: (c) => n._classes.has(c),
        toggle: (c, on) => on ? n._classes.add(c) : n._classes.delete(c),
    };
    return n;
}

const node = fakeRow();          // .media-view-time
const overlayRow = fakeRow();    // .immersive-info-time

global.document = {
    readyState: 'complete',
    getElementById: () => node,
    querySelector: (sel) => sel === '.immersive-info-time' ? overlayRow : null,
};
let mirrorRefreshes = 0;
global.window = { CanvasPanel: { refreshText: () => { mirrorRefreshes++; } } };

const elapsed = require('../../../web/js/elapsed.js');

beforeEach(() => {
    now = 0;
    mirrorRefreshes = 0;
    for (const n of [node, overlayRow]) { n.textContent = ''; n._classes.clear(); }
});

describe('formatting', () => {
    it('is minutes and padded seconds', () => {
        assert.equal(elapsed.fmt(0), '0:00');
        assert.equal(elapsed.fmt(9_000), '0:09');
        assert.equal(elapsed.fmt(61_000), '1:01');
        assert.equal(elapsed.fmt(406_306), '6:46');
    });

    it('never shows a negative time', () => {
        assert.equal(elapsed.fmt(-5_000), '0:00');
    });
});

describe('between polls', () => {
    it('advances with the wall clock while playing', () => {
        elapsed._set({ positionMs: 60_000, durationMs: 200_000, atWallClock: 0, playing: true });
        now = 7_000;
        elapsed._tick();
        assert.equal(node.textContent, '1:07 / 3:20');
    });

    it('stands still while paused', () => {
        elapsed._set({ positionMs: 60_000, durationMs: 200_000, atWallClock: 0, playing: false });
        now = 30_000;
        elapsed._tick();
        assert.equal(node.textContent, '1:00 / 3:20',
                     'a paused track kept counting');
    });

    it('never runs past the end of the track', () => {
        // The last poll before a track change can be seconds stale.
        elapsed._set({ positionMs: 195_000, durationMs: 200_000, atWallClock: 0, playing: true });
        now = 60_000;
        elapsed._tick();
        assert.equal(node.textContent, '3:20 / 3:20');
    });
});

describe('when there is nothing to show', () => {
    it('clears and hides the row', () => {
        elapsed._set({ positionMs: 1_000, durationMs: 200_000, atWallClock: 0, playing: true });
        elapsed._tick();
        assert.ok(node.classList.contains('has-time'));

        // A player that cannot answer, or a stream with no duration.
        elapsed._set(null);
        elapsed._tick();
        assert.equal(node.textContent, '');
        assert.ok(!node.classList.contains('has-time'),
                  'the row stayed visible with nothing in it');
    });
});

describe('every place the text appears', () => {
    it('writes the overlay row as well as the panel', () => {
        // The canvas panel mirrors the overlay, so a clock that updated
        // only the panel would be invisible over a canvas or a video —
        // which is most of what this view shows.
        elapsed._set({ positionMs: 30_000, durationMs: 90_000, atWallClock: 0, playing: true });
        elapsed._tick();
        assert.equal(node.textContent, '0:30 / 1:30');
        assert.equal(overlayRow.textContent, '0:30 / 1:30');
        assert.ok(overlayRow.classList.contains('has-time'));
    });

    it('re-syncs the mirror only when the text actually changed', () => {
        elapsed._set({ positionMs: 30_000, durationMs: 90_000, atWallClock: 0, playing: false });
        elapsed._tick();
        const after = mirrorRefreshes;
        assert.ok(after > 0, 'the mirror was never told');
        elapsed._tick();                       // paused: same second, same text
        assert.equal(mirrorRefreshes, after,
                     'the mirror was re-laid out for an unchanged string');
    });
});
