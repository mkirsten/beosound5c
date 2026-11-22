/**
 * Tests for web/js/menu-manager.js
 *
 * Covers:
 *  - _shouldClick(): phantom-click prevention — clicks fire only on a
 *    transition to a DIFFERENT item than the last one clicked, never on the
 *    first highlight after boot or when re-entering the same item after an
 *    excursion through an overlay/gap zone.
 *  - _cleanupRemovedRoute(): a wholesale menu rebuild (fetchMenu) that drops
 *    the currently-viewed item must evict the user — but only after a grace
 *    period, because right after a beo-router restart dynamic sources are
 *    momentarily missing from /router/menu until their service re-registers.
 *    A route that reappears within the grace window cancels the eviction.
 *  - getAngleStep(): visual arc spacing delegates to LaserPositionMapper so
 *    rendering and laser selection zones always agree.
 *
 * Run with: node --test tests/unit/js/test_menu_manager.js
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');

// menu-manager.js expects a browser environment; provide the minimal window
// surface it touches in the code paths under test (constructor + spacing).
const mapper = require('../../../web/js/laser-position-mapper.js');
global.window = { LaserPositionMapper: mapper };

const { MenuManager } = require('../../../web/js/menu-manager.js');


describe('MenuManager._shouldClick (phantom click prevention)', () => {
    it('first highlight after boot does not click', () => {
        const m = new MenuManager();
        assert.equal(m._shouldClick('menu/playing'), false);
    });

    it('moving to a different item clicks', () => {
        const m = new MenuManager();
        m._shouldClick('menu/playing');
        assert.equal(m._shouldClick('menu/system'), true);
    });

    it('staying on the same item does not click again', () => {
        const m = new MenuManager();
        m._shouldClick('menu/playing');
        assert.equal(m._shouldClick('menu/playing'), false);
    });

    it('re-entering the same item after a gap/overlay excursion does not click', () => {
        const m = new MenuManager();
        m._shouldClick('menu/playing');
        // Laser passes through a gap or overlay zone → path is null
        assert.equal(m._shouldClick(null), false);
        assert.equal(m._shouldClick(null), false);
        // ...and comes back to the same item
        assert.equal(m._shouldClick('menu/playing'), false);
    });

    it('moving to a different item via a gap still clicks', () => {
        const m = new MenuManager();
        m._shouldClick('menu/playing');
        m._shouldClick(null);
        assert.equal(m._shouldClick('menu/system'), true);
    });

    it('boot sweep through overlay then onto an item fires zero clicks', () => {
        const m = new MenuManager();
        assert.equal(m._shouldClick(null), false);   // boot in overlay zone
        assert.equal(m._shouldClick('menu/scenes'), false);  // first item entered
        assert.equal(m._shouldClick('menu/scenes'), false);  // held
    });
});


describe('MenuManager._cleanupRemovedRoute (deferred eviction after menu rebuild)', () => {
    // Build a manager sitting on menu/spotify whose menu rebuild dropped the
    // spotify entry (default menuItems don't include it) — simulates a
    // beo-router restart where the source hasn't re-registered yet.
    function onGhostRoute() {
        const m = new MenuManager();
        const navigations = [];
        m.onNavigate = (path) => { navigations.push(path); };
        m.views['menu/spotify'] = { title: 'SPOTIFY' };
        m._currentRoute = 'menu/spotify';
        return { m, navigations };
    }

    it('does not evict immediately — starts a grace timer instead', (t) => {
        t.mock.timers.enable({ apis: ['setTimeout'] });
        const { m, navigations } = onGhostRoute();
        m._cleanupRemovedRoute();
        assert.deepEqual(navigations, []);
        assert.ok(m.views['menu/spotify'], 'view must survive the grace window');
        assert.equal(m._currentRoute, 'menu/spotify');
        assert.ok(m._evictionTimer, 'grace timer should be pending');
    });

    it('evicts when the route is still missing after the grace period', (t) => {
        t.mock.timers.enable({ apis: ['setTimeout'] });
        const { m, navigations } = onGhostRoute();
        m._cleanupRemovedRoute();
        t.mock.timers.tick(m._evictionGraceMs);
        assert.deepEqual(navigations, ['menu/playing']);
        assert.equal(m.views['menu/spotify'], undefined);
        assert.equal(m._currentRoute, 'menu/playing');
    });

    it('cancels the eviction when a later rebuild shows the route again', (t) => {
        t.mock.timers.enable({ apis: ['setTimeout'] });
        const { m, navigations } = onGhostRoute();
        m._cleanupRemovedRoute();
        // Source service re-registers → next fetchMenu rebuild includes it
        m.menuItems.push({ title: 'SPOTIFY', path: 'menu/spotify', dynamic: true });
        m._cleanupRemovedRoute();
        assert.equal(m._evictionTimer, null, 'timer should be cancelled');
        t.mock.timers.tick(m._evictionGraceMs * 2);
        assert.deepEqual(navigations, []);
        assert.ok(m.views['menu/spotify']);
        assert.equal(m._currentRoute, 'menu/spotify');
    });

    it('re-checks at fire time — route restored via addMenuItem broadcast survives', (t) => {
        t.mock.timers.enable({ apis: ['setTimeout'] });
        const { m, navigations } = onGhostRoute();
        m._cleanupRemovedRoute();
        // Route comes back through the menu_item add path (no fetchMenu),
        // so no explicit cancellation happened before the timer fires.
        m.menuItems.push({ title: 'SPOTIFY', path: 'menu/spotify', dynamic: true });
        t.mock.timers.tick(m._evictionGraceMs);
        assert.deepEqual(navigations, []);
        assert.ok(m.views['menu/spotify']);
    });

    it('re-checks at fire time — no eviction if the user navigated away themselves', (t) => {
        t.mock.timers.enable({ apis: ['setTimeout'] });
        const { m, navigations } = onGhostRoute();
        m._cleanupRemovedRoute();
        m._currentRoute = 'menu/system'; // user moved on during the grace window
        t.mock.timers.tick(m._evictionGraceMs);
        assert.deepEqual(navigations, []);
    });

    it('repeated rebuilds with the route still missing keep one timer and evict once', (t) => {
        t.mock.timers.enable({ apis: ['setTimeout'] });
        const { m, navigations } = onGhostRoute();
        m._cleanupRemovedRoute();
        const firstTimer = m._evictionTimer;
        t.mock.timers.tick(Math.floor(m._evictionGraceMs / 2));
        m._cleanupRemovedRoute(); // WS reconnect fires fetchMenu again mid-grace
        assert.equal(m._evictionTimer, firstTimer, 'existing grace timer must be kept');
        t.mock.timers.tick(m._evictionGraceMs);
        assert.deepEqual(navigations, ['menu/playing']);
    });

    it('does nothing when the current route still exists', (t) => {
        t.mock.timers.enable({ apis: ['setTimeout'] });
        const m = new MenuManager();
        let navigatedTo = null;
        m.onNavigate = (path) => { navigatedTo = path; };
        m._currentRoute = 'menu/playing';
        m._cleanupRemovedRoute();
        assert.equal(m._evictionTimer, null);
        t.mock.timers.tick(m._evictionGraceMs);
        assert.equal(navigatedTo, null);
        assert.ok(m.views['menu/playing']);
    });

    it('does nothing when no route has been visited yet', () => {
        const m = new MenuManager();
        let navigatedTo = null;
        m.onNavigate = (path) => { navigatedTo = path; };
        m._cleanupRemovedRoute();
        assert.equal(m._evictionTimer, null);
        assert.equal(navigatedTo, null);
    });
});


describe('MenuManager.getAngleStep (shared spacing with laser mapper)', () => {
    it('uses the default step for small menus', () => {
        const m = new MenuManager();  // 4 default items
        assert.equal(m.getAngleStep(), 5);
    });

    it('matches LaserPositionMapper spacing for large menus', () => {
        const m = new MenuManager();
        m.menuItems = Array.from({ length: 10 }, (_, i) => ({
            title: `I${i}`, path: `menu/i${i}`
        }));
        assert.equal(m.getAngleStep(), mapper.getMenuAngleStepFor(10));
        assert.ok(m.getAngleStep() < 5, 'expected compressed spacing at 10 items');
    });

    it('getStartItemAngle agrees with the mapper when items are synced', () => {
        const m = new MenuManager();
        m.menuItems = Array.from({ length: 9 }, (_, i) => ({
            title: `I${i}`, path: `menu/i${i}`
        }));
        mapper.updateMenuItems(m.menuItems);
        assert.ok(
            Math.abs(m.getStartItemAngle() - mapper.getMenuStartAngle()) < 1e-9,
            `MenuManager ${m.getStartItemAngle()} != mapper ${mapper.getMenuStartAngle()}`
        );
    });
});


describe('MenuManager.fetchMenu (titles come from the router)', () => {
    async function rebuildWith(items) {
        const m = new MenuManager();
        // No DOM here: skip rendering, and fail loudly instead of retrying.
        m.renderMenuItems = () => {};
        m._cleanupRemovedRoute = () => {};
        const saved = global.fetch;
        global.fetch = async () => ({ json: async () => ({ items }) });
        try {
            await m.fetchMenu();
            assert.equal(m._menuRetries, 0, 'fetchMenu fell into its retry path');
        } finally {
            global.fetch = saved;
        }
        return m;
    }

    it('a built-in item takes the title configured in the menu', async () => {
        const m = new MenuManager();
        const builtin = m.menuItems.find(i => !i.dynamic);
        const id = builtin.path.replace(/^menu\//, '');
        const rebuilt = await rebuildWith([{ id, title: 'RENAMED' }]);
        const item = rebuilt.menuItems.find(i => i.path === builtin.path);
        assert.equal(item.title, 'RENAMED');
    });

    it('extra fields of the existing entry are kept', async () => {
        const m = new MenuManager();
        const builtin = m.menuItems.find(i => !i.dynamic);
        const id = builtin.path.replace(/^menu\//, '');
        const rebuilt = await rebuildWith([{ id, title: 'RENAMED' }]);
        const item = rebuilt.menuItems.find(i => i.path === builtin.path);
        assert.deepEqual(item, { ...builtin, title: 'RENAMED' });
    });
});
