/**
 * Tests for the lifecycle of "webpage" menu items — config-driven iframes
 * pointing at external URLs (church.json maps SECURITY to the Home Assistant
 * camera dashboard).
 *
 * Why this is guarded: these iframes used to be kept alive off-screen by the
 * same "rescue" path that preserves our own preloaded source iframes. An
 * external page we don't control keeps running there — the HA camera
 * dashboard holds live video streams, and every stream pins Chromium
 * shared-memory segments, one fd each. Church accumulated 983 of them over 9
 * days and hit the renderer's soft RLIMIT_NOFILE, which does not crash the
 * renderer but deadlocks it: display frozen mid-crossfade, media WS gone,
 * main thread parked in futex_wait, and systemd still reporting beo-ui
 * active. Recovery needed a manual restart.
 *
 * Two invariants keep that from coming back:
 *   1. ViewManager unloads webpage iframes on the way out of the view.
 *   2. MenuManager does not give them a "preload-" id, which is what the
 *      rescue selector matches.
 *
 * Run with: node --test tests/unit/js/test_webpage_iframe_lifecycle.js
 */

const { describe, it, beforeEach } = require('node:test');
const assert = require('node:assert/strict');

// ── Minimal DOM stub ──────────────────────────────────────────────────────
// updateView() only ever uses getElementById/createElement plus three fixed
// querySelectorAll selectors, so a real DOM would be far more machinery than
// the behaviour under test needs.

class FakeEl {
    constructor(tag) {
        this.tagName = tag.toUpperCase();
        this.id = '';
        this.className = '';
        this.src = '';
        this.style = { cssText: '' };
        this.children = [];
        this.parent = null;
        this._html = '';
    }

    appendChild(child) {
        if (child.parent) child.parent._detach(child);
        child.parent = this;
        this.children.push(child);
        return child;
    }

    _detach(child) {
        this.children = this.children.filter(c => c !== child);
    }

    remove() {
        if (this.parent) this.parent._detach(this);
        this.parent = null;
    }

    get innerHTML() { return this._html; }

    set innerHTML(html) {
        // Assigning innerHTML detaches every descendant, as in a browser.
        for (const c of this.children) c.parent = null;
        this.children = [];
        this._html = html;
        // Views under test render one container div carrying an id.
        const m = /id="([^"]+)"/.exec(html || '');
        if (m) {
            const div = new FakeEl('div');
            div.id = m[1];
            this.appendChild(div);
        }
    }

    descendants() {
        return this.children.flatMap(c => [c, ...c.descendants()]);
    }

    querySelectorAll(selector) {
        const iframes = this.descendants().filter(e => e.tagName === 'IFRAME');
        switch (selector) {
            case 'iframe':
                return iframes;
            case 'iframe.webpage-iframe':
                return iframes.filter(e => e.className.split(/\s+/).includes('webpage-iframe'));
            case 'iframe[id^="preload-"]':
                return iframes.filter(e => e.id.startsWith('preload-'));
            default:
                throw new Error(`DOM stub has no support for selector: ${selector}`);
        }
    }
}

const CAMERA_URL = 'http://homeassistant.local:8123/dashboard-cameras/home?kiosk';

const WEBPAGE_VIEW = {
    title: 'SECURITY',
    content: '<div id="webpage-container-security" class="webpage-container"></div>',
    _webpage: {
        iframeId: 'webpage-iframe-security',
        containerId: 'webpage-container-security',
        url: CAMERA_URL
    }
};

const PLAIN_VIEW = { title: 'SPEAKERS', content: '<div id="speakers-root"></div>' };

let body, contentArea, preloadContainer;

function installDom({ withPreloadContainer = true } = {}) {
    body = new FakeEl('body');
    contentArea = new FakeEl('div');
    contentArea.id = 'contentArea';
    body.appendChild(contentArea);

    preloadContainer = null;
    if (withPreloadContainer) {
        preloadContainer = new FakeEl('div');
        preloadContainer.id = 'iframe-preload-container';
        body.appendChild(preloadContainer);
    }

    global.document = {
        getElementById(id) {
            return [body, ...body.descendants()].find(e => e.id === id) || null;
        },
        createElement(tag) { return new FakeEl(tag); }
    };
    global.window = {};
}

function makeViewManager() {
    const { ViewManager } = require('../../../web/js/view-manager.js');
    const vm = new ViewManager();
    vm.menuManager = {
        views: { 'menu/security': WEBPAGE_VIEW, 'menu/speakers': PLAIN_VIEW },
        attachPreloadedIframe() {},
        reloadAllSourceIframes() {}
    };
    vm.mediaManager = {};
    // Start somewhere neutral so the first updateView() is an entry, not a
    // re-render of menu/playing (which would pull in mediaManager).
    vm.currentRoute = 'menu/speakers';
    return vm;
}

function currentWebpageIframes() {
    return contentArea.descendants()
        .filter(e => e.tagName === 'IFRAME' && e.className.includes('webpage-iframe'));
}

describe('ViewManager webpage iframe lifecycle', () => {
    beforeEach(() => installDom());

    it('creates the iframe with the configured URL on entry', () => {
        const vm = makeViewManager();
        vm.currentRoute = 'menu/security';
        vm.updateView();

        const frames = currentWebpageIframes();
        assert.equal(frames.length, 1);
        assert.equal(frames[0].src, CAMERA_URL);
        assert.equal(frames[0].id, 'webpage-iframe-security');
    });

    it('unloads and detaches the iframe when navigating away', () => {
        const vm = makeViewManager();
        vm.currentRoute = 'menu/security';
        vm.updateView();
        const frame = currentWebpageIframes()[0];

        vm.currentRoute = 'menu/speakers';
        vm.updateView();

        assert.equal(currentWebpageIframes().length, 0, 'iframe must leave the content area');
        assert.equal(frame.parent, null, 'iframe must be detached from the DOM');
        assert.equal(frame.src, 'about:blank',
            'src must be blanked so the external page unloads now, not at GC time');
    });

    it('never rescues the webpage iframe, even if it carries a preload- id', () => {
        // Defence in depth: MenuManager is supposed to keep "preload-" off
        // these ids (guarded below), but the teardown must not *depend* on
        // that — it runs before the rescue pass precisely so a stray id
        // can't put a live camera stream back into the preload container.
        const vm = makeViewManager();
        vm.menuManager.views['menu/security'] = {
            ...WEBPAGE_VIEW,
            _webpage: { ...WEBPAGE_VIEW._webpage, iframeId: 'preload-webpage-security' }
        };

        vm.currentRoute = 'menu/security';
        vm.updateView();

        vm.currentRoute = 'menu/speakers';
        vm.updateView();

        const stowed = preloadContainer.descendants().filter(e => e.tagName === 'IFRAME');
        assert.deepEqual(stowed, [],
            'a background camera stream is exactly what leaked the fds — it must not be kept alive');
    });

    it('builds a fresh iframe on re-entry rather than restoring the old one', () => {
        const vm = makeViewManager();
        vm.currentRoute = 'menu/security';
        vm.updateView();
        const first = currentWebpageIframes()[0];

        vm.currentRoute = 'menu/speakers';
        vm.updateView();
        vm.currentRoute = 'menu/security';
        vm.updateView();

        const second = currentWebpageIframes()[0];
        assert.notEqual(second, first, 'must be a new element, not the stale one');
        assert.equal(second.src, CAMERA_URL, 'fresh load, not about:blank carried over');
    });

    it('every iframe from repeated entry/exit cycles gets unloaded', () => {
        // Detaching alone is not enough: a detached frame goes on running
        // (and holding its shm segments) until GC gets to it, which on a
        // 9-day-uptime kiosk is indistinguishable from never. Each cycle's
        // frame must have been explicitly unloaded.
        const vm = makeViewManager();
        const seen = [];

        for (let i = 0; i < 25; i++) {
            vm.currentRoute = 'menu/security';
            vm.updateView();
            seen.push(currentWebpageIframes()[0]);
            vm.currentRoute = 'menu/speakers';
            vm.updateView();
        }

        assert.equal(seen.length, 25);
        assert.equal(new Set(seen).size, 25, 'each entry builds its own iframe');
        for (const frame of seen) {
            assert.equal(frame.src, 'about:blank', 'every cycle must unload its iframe');
            assert.equal(frame.parent, null);
        }
        assert.deepEqual(body.descendants().filter(e => e.tagName === 'IFRAME'), []);
    });

    it('still rescues our own preloaded source iframes', () => {
        const vm = makeViewManager();
        // A preloaded source iframe sitting in the content area, as
        // attachPreloadedIframe() would have left it.
        const preloaded = new FakeEl('iframe');
        preloaded.id = 'preload-scenes';
        contentArea.appendChild(preloaded);

        vm.currentRoute = 'menu/speakers';
        vm.updateView();

        assert.equal(preloaded.parent, preloadContainer,
            'preload- iframes are ours and must keep their rescue path');
        assert.notEqual(preloaded.src, 'about:blank');
    });
});

describe('MenuManager webpage view registration', () => {
    beforeEach(() => installDom());

    it('does not give webpage iframes a preload- id', async () => {
        const mapper = require('../../../web/js/laser-position-mapper.js');
        global.window = { LaserPositionMapper: mapper };
        global.fetch = async () => ({
            json: async () => ({
                items: [{ id: 'security', title: 'SECURITY', type: 'webpage', url: CAMERA_URL }]
            })
        });

        const { MenuManager } = require('../../../web/js/menu-manager.js');
        const m = new MenuManager();
        m.renderMenuItems = () => {};   // DOM rendering is not under test
        await m.fetchMenu();

        const view = m.views['menu/security'];
        assert.ok(view, 'webpage item must register a view');
        assert.equal(view._webpage.url, CAMERA_URL);
        assert.ok(!view._webpage.iframeId.startsWith('preload-'),
            'a preload- id would put this iframe back on the rescue path that leaked fds');
    });
});
