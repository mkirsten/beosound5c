/**
 * Webpage menu items can opt in to receiving the wheel and buttons by
 * setting window.beoHandlesInput = true (docs/webpage-input.md).
 *
 * Run with: node --test tests/unit/js/test_webpage_handles_input.js
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');

global.window = {};
const { IframeMessenger } = require('../../../web/js/iframe-messenger.js');

function docWith(contentWindow) {
    return {
        querySelector: sel => (sel === '#contentArea .webpage-iframe' && contentWindow !== undefined)
            ? { get contentWindow() { return contentWindow(); } }
            : null,
    };
}

function pageWindow(flag) {
    const sent = [];
    const win = { beoHandlesInput: flag, postMessage: (m, o) => sent.push([m, o]) };
    return { win, sent };
}

describe('IframeMessenger.sendToWebpage', () => {
    it('sends nav to a page that opted in, in the built-in message shape', () => {
        const { win, sent } = pageWindow(true);
        const ok = IframeMessenger.sendToWebpage('nav', { data: { direction: 'clock' } }, docWith(() => win));
        assert.equal(ok, true);
        assert.deepEqual(sent, [[{ type: 'nav', data: { direction: 'clock' } }, '*']]);
    });

    it('sends buttons to a page that opted in', () => {
        const { win, sent } = pageWindow(true);
        assert.equal(IframeMessenger.sendToWebpage('button', { button: 'go' }, docWith(() => win)), true);
        assert.deepEqual(sent[0][0], { type: 'button', button: 'go' });
    });

    it('leaves a page without the flag alone (default scroll / webhook)', () => {
        const { win, sent } = pageWindow(undefined);
        assert.equal(IframeMessenger.sendToWebpage('nav', { data: {} }, docWith(() => win)), false);
        assert.deepEqual(sent, []);
    });

    it('only a literal true counts', () => {
        const { win } = pageWindow('yes');
        assert.equal(IframeMessenger.sendToWebpage('nav', { data: {} }, docWith(() => win)), false);
    });

    it('a cross-origin page (access throws) falls back to default', () => {
        const doc = docWith(() => { throw new Error('SecurityError'); });
        assert.equal(IframeMessenger.sendToWebpage('nav', { data: {} }, doc), false);
    });

    it('no webpage iframe on screen: nothing sent', () => {
        assert.equal(IframeMessenger.sendToWebpage('nav', { data: {} }, docWith(undefined)), false);
    });
});
