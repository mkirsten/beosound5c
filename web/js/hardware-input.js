// Hardware Input Handler
// Processes physical input events: laser pointer, navigation wheel,
// volume wheel, and button presses. Also manages cursor visibility,
// pointer rendering, and volume arc overlay.

// Configuration - uses Constants for timeout values
const config = {
    showMouseCursor: false,   // Hide mouse cursor on hardware device
    wsUrl: AppConfig.websocket.input,  // Loaded from centralized config
    skipFactor: 1,          // Process 1 out of every N events (higher = more skipping)
    disableTransitions: true, // Disable CSS transitions on the pointer for responsiveness
    bypassRAF: true,        // Bypass requestAnimationFrame for immediate updates
    showDebugOverlay: true, // Show the debug overlay to help diagnose issues

    // Timeouts from centralized Constants (with fallbacks)
    get volumeProcessingDelay() {
        return window.Constants?.timeouts?.volumeProcessing || 50;
    },
    get cursorHideDelay() {
        return window.Constants?.timeouts?.cursorHide || 2000;
    }
};

// Global variables for laser event optimization
// Default position from Constants (fallback to 93)
const defaultLaserPosition = window.Constants?.laser?.defaultPosition || 93;
let lastLaserEvent = { position: defaultLaserPosition };
let cursorHideTimeout = null;

// Performance tracking
let lastUpdateTime = 0;
let frameTimeAvg = 0;
let eventsProcessed = 0;

// Pointer state
let lastKnownPointerAngle = 180; // Default middle position

// ── Cursor Visibility ──

function showCursor() {
    const cursorStyle = document.getElementById('cursor-style');
    if (cursorStyle) {
        cursorStyle.textContent = `
            body, div, svg, path, ellipse { cursor: auto !important; }
            #viewport { cursor: auto !important; }
            .list-item { cursor: pointer !important; }
            .flow-item { cursor: pointer !important; }
        `;
    }
}

function hideCursor() {
    const cursorStyle = document.getElementById('cursor-style');
    if (cursorStyle) {
        cursorStyle.textContent = '* { cursor: none !important; }';
    }
}

// ── Transition Styles ──

function updateTransitionStyles() {
    const existingStyle = document.getElementById('pointer-transition-style');
    if (existingStyle) existingStyle.remove();

    if (config.disableTransitions) {
        const transitionStyle = document.createElement('style');
        transitionStyle.id = 'pointer-transition-style';
        transitionStyle.textContent = `
            /* Target common pointer selectors */
            #laser-pointer,
            .wheel-pointer,
            [class*="pointer"],
            [id*="pointer"],
            [class*="cursor"],
            [id*="cursor"],
            .top-wheel-pointer,
            g[transform],
            [style*="transform"]:not(.playing-flipper):not(.playing-face):not(.cd-arc-item):not(.cd-track-transition),
            [transform],
            *[style*="transition"]:not(.playing-flipper):not(.playing-face):not(.cd-arc-item):not(.cd-track-transition),
            *[style*="rotate"]:not(.playing-flipper):not(.playing-face):not(.cd-arc-item),
            path, line, polygon {
                transition: none !important;
                animation: none !important;
                transition-property: none !important;
                animation-duration: 0s !important;
                transition-duration: 0s !important;
                will-change: transform;
                backface-visibility: hidden;
                transform: translateZ(0);
            }

            /* Speed up rendering with hardware acceleration hints */
            body, svg, #viewport {
                will-change: transform;
                backface-visibility: hidden;
                transform: translateZ(0);
            }
        `;
        document.head.appendChild(transitionStyle);
    }
}

// ── Laser Processing ──

function processLaserEvents() {
    const now = performance.now();
    const frameDelta = now - lastUpdateTime;

    if (lastUpdateTime > 0) {
        frameTimeAvg = frameTimeAvg * 0.9 + frameDelta * 0.1;
    }
    lastUpdateTime = now;

    if (lastLaserEvent !== null) {
        processLaserEvent(lastLaserEvent);
    }

    requestAnimationFrame(processLaserEvents);
}

function processLaserEvent(data) {
    const pos = data.position;

    if (!window.LaserPositionMapper) {
        console.error('[LASER] LaserPositionMapper not loaded');
        return;
    }
    const { laserPositionToAngle } = window.LaserPositionMapper;
    const angle = laserPositionToAngle(pos);

    lastKnownPointerAngle = angle;
    updateViaStore(angle, pos);

    lastLaserEvent = null;
    eventsProcessed++;
}

function updateViaStore(angle, laserPosition) {
    const uiStore = window.uiStore;
    if (!uiStore) return;

    uiStore.wheelPointerAngle = angle;

    if (laserPosition !== undefined) {
        uiStore.laserPosition = laserPosition;
    }

    if (config.disableTransitions) {
        if (typeof uiStore.forceUpdate === 'function') {
            uiStore.forceUpdate();
        }
    }

    if (uiStore.setLaserPosition) {
        uiStore.setLaserPosition(laserPosition || lastLaserEvent?.position || 0);
    }

    uiStore.handleWheelChange();
}

// ── Navigation Wheel ──

function handleNavEvent(uiStore, data) {
    const page = uiStore.currentRoute || 'unknown';

    if (routeNavToView(page, data, uiStore)) return;

    // Default: main menu wheel
    uiStore.topWheelPosition = data.direction === 'clock' ? 1 : -1;
    uiStore.handleWheelChange();
}

function routeNavToView(page, data, uiStore) {
    const viewId = page.startsWith('menu/') ? page.slice(5) : null;

    // Source page — controller owns nav
    const sourceCtrl = viewId && window.SourcePresets?.[viewId]?.controller;
    if (sourceCtrl) {
        if (sourceCtrl.isActive && sourceCtrl.handleNavEvent) sourceCtrl.handleNavEvent(data);
        return true; // source page always consumes
    }

    // Playing page — active source owns nav
    if (page === 'menu/playing' && uiStore.activeSource) {
        const ctrl = window.SourcePresets?.[uiStore.activeSource]?.controller;
        if (ctrl?.isActive && ctrl.handleNavEvent && ctrl.handleNavEvent(data)) return true;
    }

    // Iframe page — iframe owns nav
    if (window.IframeMessenger?.routeHasIframe(page)) {
        window.IframeMessenger.sendNavEvent(page, data);
        return true;
    }

    // Webpage iframe — scroll the page, never move the laser
    const webpageIframe = document.querySelector('#contentArea .webpage-iframe');
    if (webpageIframe) {
        try {
            const scrollAmount = (data.direction === 'clock' ? 1 : -1) * 120;
            webpageIframe.contentWindow.scrollBy(0, scrollAmount);
        } catch (e) { /* cross-origin — scroll not possible, just consume */ }
        return true;
    }

    return false;
}

// ── Volume ──
//
// Volume has two independent paths by design:
//
// Path A — USB HID volume wheel (physical BS5 wheel):
//   input.py → WebSocket → handleVolumeEvent() here → local JS math → POST /router/volume
//   Uses non-linear scaling (faster at low, slower at high) and fast-spin-to-zero,
//   because the wheel provides speed data that enables these features.
//
// Path B — BeoRemote / IR remote (volup/voldown buttons):
//   bluetooth.py / masterlink.py → POST /router/event {action: "volup"}
//   Router applies a fixed step (from config volume.step). No speed data available.
//   Volume changes are broadcast back to the UI via volume_update events.
//
// Both paths converge at the router, which owns the canonical volume state
// and forwards to the volume adapter (BeoLab 5, Sonos, etc.).

let currentVolume = 50;
let volumeOutputDevice = '';
let volumeHideTimer = null;
let volumeSendTimer = null;
const VOLUME_ARC_LENGTH = Math.PI * 274;

function initVolumeArc() {
    const arcPath = document.getElementById('volume-arc-path');
    if (arcPath) {
        arcPath.style.strokeDasharray = VOLUME_ARC_LENGTH;
        arcPath.style.strokeDashoffset = VOLUME_ARC_LENGTH;
    }
    fetchVolumeFromRouter();
}

async function fetchVolumeFromRouter() {
    try {
        const resp = await fetch(`${AppConfig.routerUrl}/router/status`);
        const data = await resp.json();
        currentVolume = data.volume || 0;
        volumeOutputDevice = data.output_device || '';
        const deviceEl = document.getElementById('volume-device');
        if (deviceEl) deviceEl.textContent = volumeOutputDevice;
        updateVolumeArc(currentVolume);
        console.log(`[VOLUME] Synced from router: ${currentVolume}% (${volumeOutputDevice})`);
    } catch (e) {
        console.warn('[VOLUME] Could not fetch router status:', e);
    }
}

/**
 * Handle volume updates broadcast from the router (e.g., remote control
 * adjusted volume, or Sonos reported a change). Updates the local state
 * and arc visual without sending back to the router.
 */
function handleVolumeUpdate(data) {
    const newVol = data.volume;
    if (newVol == null || typeof newVol !== 'number') return;
    currentVolume = newVol;
    updateVolumeArc(currentVolume);
}

function sendVolumeToRouter(volume) {
    if (volumeSendTimer) clearTimeout(volumeSendTimer);
    volumeSendTimer = setTimeout(() => {
        fetch(`${AppConfig.routerUrl}/router/volume`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({volume: Math.round(volume)})
        }).catch(e => console.warn('[VOLUME] Router send failed:', e));
        volumeSendTimer = null;
    }, 50);
}

function updateVolumeArc(volume) {
    const arcPath = document.getElementById('volume-arc-path');
    if (!arcPath) return;
    const arcFraction = 0.18 + (volume / 100) * (0.82 - 0.18);
    arcPath.style.strokeDashoffset = VOLUME_ARC_LENGTH * (1 - arcFraction);
}

function handleVolumeEvent(uiStore, data) {
    if (!uiStore) return;

    const speed = data.speed || 10;
    const direction = data.direction === 'clock' ? 1 : -1;

    // Fast spin down → snap to 0
    if (direction === -1 && speed > 25) {
        currentVolume = 0;
    } else {
        // Non-linear: faster at low volumes, slower at high volumes
        const scale = 1.5 - (currentVolume / 100) * 0.9;
        const step = (speed / 14) * scale;
        currentVolume = Math.max(0, Math.min(100, currentVolume + direction * step));
    }

    const overlay = document.getElementById('volume-overlay');
    if (overlay) {
        updateVolumeArc(currentVolume);
        overlay.classList.add('visible');

        if (volumeHideTimer) clearTimeout(volumeHideTimer);
        volumeHideTimer = setTimeout(() => {
            overlay.classList.remove('visible');
            volumeHideTimer = null;
        }, 1000);
    }

    sendVolumeToRouter(currentVolume);
    console.log(`[VOLUME] ${Math.round(currentVolume)}%`);
}

// ── Buttons ──

// HA webhook context aliases for backwards compatibility
const webhookContextAliases = {
    'playing': 'now_playing',
    'showing': 'now_showing'
};

function getWebhookContext(page) {
    const context = page.startsWith('menu/') ? page.slice(5) : 'unknown';
    return webhookContextAliases[context] || context;
}

function handleButtonEvent(uiStore, data) {
    const page = uiStore.currentRoute || 'unknown';
    const button = data.button.toLowerCase();
    console.log(`[BUTTON] ${button} on ${page}`);

    // Global overlay intercept — camera overlay captures all buttons when active
    if (window.CameraOverlayManager?.isActive &&
        window.CameraOverlayManager.handleAction(button)) return;

    // Route to current view — if handled, done
    if (routeButtonToView(page, button, uiStore)) return;

    // Fallback: HA webhook (pages without local handling)
    sendWebhook(getWebhookContext(page), button);
}

function routeButtonToView(page, button, uiStore) {
    const viewId = page.startsWith('menu/') ? page.slice(5) : null;

    // Source page — controller owns all buttons
    const sourceCtrl = viewId && window.SourcePresets?.[viewId]?.controller;
    if (sourceCtrl) {
        if (sourceCtrl.isActive && sourceCtrl.handleButton) {
            sourceCtrl.handleButton(button);
        }
        return true; // source page always consumes
    }

    // Playing page — active source owns buttons
    if (page === 'menu/playing') {
        if (uiStore.activeSource) {
            const ctrl = window.SourcePresets?.[uiStore.activeSource]?.controller;
            if (ctrl?.isActive && ctrl.handleButton && ctrl.handleButton(button)) return true;
            // Source didn't handle it — map to playback actions via router
            const playbackAction = { go: 'go', left: 'left', right: 'right' }[button];
            if (playbackAction) {
                sendToRouter(playbackAction);
                return true;
            }
        }
        if (window.EmulatorBridge?.isInEmulator) {
            const action = { left: 'prev_track', right: 'next_track', go: 'toggle_playback' }[button];
            if (action) { window.EmulatorBridge.notifyPlaybackControl(action); return true; }
        }
        return false; // no handler — fall through to webhook
    }

    // Iframe page — iframe owns all buttons
    if (window.IframeMessenger?.routeHasIframe(page)) {
        window.IframeMessenger.sendButtonEvent(page, button);
        return true;
    }

    // Security GO — opens camera overlay (parent-side overlay, not iframe)
    if (page === 'menu/security' && button === 'go' && window.CameraOverlayManager) {
        window.CameraOverlayManager.show();
        return true;
    }

    // Webpage views: buttons fall through to HA webhook (gate/lock, etc.)
    return false;
}

// ── Router ──

function sendToRouter(action) {
    const payload = {
        device_type: 'Audio',
        device_name: AppConfig.deviceName || 'unknown',
        action: action
    };
    console.log(`[ROUTER] Sending action: ${action}`);
    fetch(`${AppConfig.routerUrl}/router/event`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    }).catch(e => console.warn('[ROUTER] Send failed:', e));
}

// ── Webhooks ──

function sendWebhook(panelContext, button, id = '1') {
    const webhookUrl = AppConfig.webhookUrl;

    const payload = {
        device_type: 'Panel',
        device_name: AppConfig.deviceName || 'unknown',
        panel_context: panelContext,
        button: button,
        id: id
    };

    console.log(`[WEBHOOK] Sending ${panelContext} POST to: ${webhookUrl}`);

    if (window.uiStore && window.uiStore.logWebsocketMessage) {
        window.uiStore.logWebsocketMessage(`Sending ${panelContext} webhook: ${button}`);
    }

    const startTime = Date.now();

    fetch(webhookUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        timeout: 2000
    })
    .then(response => {
        const duration = Date.now() - startTime;
        if (response.ok) {
            console.log(`[WEBHOOK] SUCCESS: ${panelContext} ${button} (${duration}ms)`);
        } else {
            console.log(`[WEBHOOK] FAILED: ${panelContext} ${button} - HTTP ${response.status} (${duration}ms)`);
        }
    })
    .catch(error => {
        const duration = Date.now() - startTime;
        console.log(`[WEBHOOK] ERROR: ${panelContext} ${button} - ${error.message} (${duration}ms)`);
    });
}

// ── Initialization ──

document.addEventListener('DOMContentLoaded', () => {
    // Cursor visibility style
    const style = document.createElement('style');
    style.id = 'cursor-style';

    if (config.showMouseCursor) {
        style.textContent = `
            body, div, svg, path, ellipse { cursor: auto !important; }
            #viewport { cursor: auto !important; }
            .list-item { cursor: pointer !important; }
            .flow-item { cursor: pointer !important; }
            iframe, .webpage-iframe { cursor: auto !important; pointer-events: auto !important; z-index: 1000 !important; }
        `;
    } else {
        style.textContent = `
            *, iframe, .webpage-iframe { cursor: none !important; }
            iframe, .webpage-iframe { pointer-events: auto !important; z-index: 1000 !important; }
        `;
        console.log('[CURSOR] Mouse cursor hidden');
    }
    document.head.appendChild(style);

    // Disable pointer transitions for responsiveness
    updateTransitionStyles();

    // Process initial laser position
    if (lastLaserEvent && lastLaserEvent.position) {
        processLaserEvent(lastLaserEvent);
    }
    processLaserEvents();

    // Volume arc overlay
    initVolumeArc();

    // Auto-hide cursor on inactivity
    if (config.showMouseCursor) {
        document.addEventListener('mousemove', () => {
            showCursor();
            if (cursorHideTimeout) clearTimeout(cursorHideTimeout);
            cursorHideTimeout = setTimeout(hideCursor, config.cursorHideDelay);
        });
    }

});
