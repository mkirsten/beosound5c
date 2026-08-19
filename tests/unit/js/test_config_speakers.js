/**
 * §3 regression guard for the JOIN→SPEAKERS rename in the softarc config
 * editor (web/softarc/config.html).
 *
 * The editor rebuilds the whole `menu` block from CFG_SOURCE_DEFS on save, so
 * if the def key or the view mapping regresses to 'JOIN', a device carrying
 * the new `"SPEAKERS": "join"` config would render multiroom disabled and lose
 * the entry on save. There is no jsdom in the dev env for a full round-trip,
 * so this asserts the source-level invariants that make the round-trip correct.
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(
    path.join(__dirname, '../../../web/softarc/config.html'), 'utf8');

describe('config editor SPEAKERS rename (§3)', () => {
    it('CFG_SOURCE_VIEWS maps SPEAKERS → join and keeps legacy JOIN', () => {
        const block = html.match(/const CFG_SOURCE_VIEWS = \{([\s\S]*?)\};/)[1];
        assert.match(block, /'SPEAKERS':\s*'join'/, 'SPEAKERS must map to join');
        assert.match(block, /'JOIN':\s*'join'/, 'legacy JOIN must still map');
    });

    it('CFG_SOURCE_DEFS uses the SPEAKERS key, not JOIN', () => {
        assert.match(html, /key:\s*'SPEAKERS'/, 'def key must be SPEAKERS');
        assert.doesNotMatch(html, /key:\s*'JOIN'/, 'stale JOIN def key must be gone');
    });

    it('the enabled check accepts a legacy JOIN menu entry', () => {
        // enabled = ... || (src.key === 'SPEAKERS' && 'JOIN' in menu)
        assert.match(html, /'SPEAKERS'\s*&&\s*'JOIN' in menu/,
            'must load configs written before the rename');
    });
});
