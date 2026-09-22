// Camera Overlay Manager for BeoSound 5c
// Displays doorbell camera feed triggered from Home Assistant

const CameraOverlayManager = {
    isActive: false,
    overlayElement: null,
    timeoutId: null,
    refreshIntervalId: null,
    currentData: null,
    currentCameraIndex: 0,
    cameras: [],
    maxCameras: 2,  // feed slots in the overlay markup

    // Configuration
    config: {
        autoTimeoutMs: 30000,  // 30 seconds auto-dismiss
        fadeTransitionMs: 300,
        refreshIntervalMs: 1000,  // Refresh snapshots every 1 second
        cameraStreamUrl: 'http://localhost:8767/camera/stream',
        cameraSnapshotUrl: 'http://localhost:8767/camera/snapshot'
    },

    // Cameras come from config.json ("cameras"). No invented fallback: a
    // device with none configured has no camera overlay.
    get defaultCameras() {
        return window.AppConfig?.cameras || [];
    },

    // Button action definitions - matches physical BeoSound 5 button bar
    actions: {
        left: { label: 'Open door', icon: '«', action: 'open_door' },
        right: { label: 'Open/Close gate', icon: '»', action: 'toggle_gate' },
        go: { label: 'Dismiss', icon: 'GO', action: 'dismiss' }
    },

    init() {
        this.createOverlayElement();
    },

    createOverlayElement() {
        this.overlayElement = document.createElement('div');
        this.overlayElement.id = 'camera-overlay';
        this.overlayElement.innerHTML = `
            <div class="camera-overlay-content">
                <div class="camera-header">
                    <div class="camera-live-indicator">
                        <span class="camera-live-dot"></span>
                        <span class="camera-live-text">LIVE</span>
                    </div>
                </div>
                <div class="camera-feeds-dual">
                    <div class="camera-feed-wrapper">
                        <div class="camera-feed-label"></div>
                        <div class="camera-feed-container" data-camera="0">
                            <img class="camera-feed" alt="" />
                            <div class="camera-loading">Loading...</div>
                        </div>
                    </div>
                    <div class="camera-feed-wrapper">
                        <div class="camera-feed-label"></div>
                        <div class="camera-feed-container" data-camera="1">
                            <img class="camera-feed" alt="" />
                            <div class="camera-loading">Loading...</div>
                        </div>
                    </div>
                </div>
                <div class="camera-nav-hint">
                    <span><span class="camera-key">«</span> Open door</span>
                    <span><span class="camera-key">»</span> Open/Close gate</span>
                    <span><span class="camera-key">GO</span> Dismiss</span>
                </div>
            </div>
        `;

        document.body.appendChild(this.overlayElement);

        // Set up image load/error handlers for both cameras
        this.overlayElement.querySelectorAll('.camera-feed-container').forEach(container => {
            const img = container.querySelector('.camera-feed');
            const loading = container.querySelector('.camera-loading');

            img.onload = () => {
                loading.style.display = 'none';
                img.style.display = 'block';
            };

            img.onerror = () => {
                loading.textContent = 'Unavailable';
                img.style.display = 'none';
            };
        });
    },

    show(data = {}) {
        if (!this.overlayElement) {
            this.init();
        }

        this.currentData = data;

        // Cameras named in the show_camera command win outright; otherwise
        // fall back to config. Both are capped at the number of slots.
        const provided = this.normalizeCameras(data.cameras);
        this.cameras = provided.length ? provided
                                       : this.normalizeCameras(this.defaultCameras);
        if (!this.cameras.length) {
            // Nothing configured: an empty overlay, or one wired to guessed
            // entity ids, is worse than no overlay.
            console.log('[CAMERA] No cameras configured — overlay skipped');
            this.isActive = false;
            return;
        }

        this.applyCameraLayout();

        this.isActive = true;

        // Update action labels if provided
        if (data.actions) {
            Object.keys(data.actions).forEach(btn => {
                const labelEl = this.overlayElement.querySelector(`[data-button="${btn}"] .camera-action-label`);
                if (labelEl && data.actions[btn]) {
                    labelEl.textContent = data.actions[btn];
                }
            });
        }

        // Load all cameras
        this.loadAllCameras();

        // Show overlay with fade-in
        this.overlayElement.classList.add('visible');

        // Start auto-timeout
        this.startTimeout();

        console.log(`[CAMERA] Overlay shown with ${this.cameras.length} cameras`);

        if (window.uiStore && window.uiStore.logWebsocketMessage) {
            window.uiStore.logWebsocketMessage(`Camera overlay: ${this.cameras.map(c => c.title).join(', ')}`);
        }
    },

    // Keep only usable entries and never more than there are slots. Accepts
    // the config.json shape ({id, title, entity}) and the show_camera
    // command's ({title, entity}) — they're the same two fields.
    normalizeCameras(list) {
        if (!Array.isArray(list)) return [];
        return list
            .filter(cam => cam && typeof cam.entity === 'string' && cam.entity.trim())
            .slice(0, this.maxCameras)
            .map(cam => ({ ...cam, entity: cam.entity.trim() }));
    },

    // Labels come from the camera titles; unused slots are hidden so a
    // single camera gets the full width instead of a dead "Loading..." box.
    applyCameraLayout() {
        const feeds = this.overlayElement.querySelector('.camera-feeds-dual');
        if (feeds) feeds.classList.toggle('single', this.cameras.length === 1);

        this.overlayElement.querySelectorAll('.camera-feed-wrapper')
            .forEach((wrapper, index) => {
                const cam = this.cameras[index];
                if (!cam) {
                    wrapper.style.display = 'none';
                    return;
                }
                wrapper.style.display = '';
                const title = cam.title || `Camera ${index + 1}`;
                const label = wrapper.querySelector('.camera-feed-label');
                if (label) label.textContent = title;
                const img = wrapper.querySelector('.camera-feed');
                if (img) img.alt = title;
            });
    },

    loadAllCameras() {
        const containers = this.overlayElement.querySelectorAll('.camera-feed-container');

        // Initial load
        this.cameras.forEach((cam, index) => {
            const container = containers[index];
            if (!container) return;

            const loading = container.querySelector('.camera-loading');
            loading.style.display = 'flex';
            loading.textContent = 'Loading...';

            this.loadCameraSnapshot(container, cam);
        });

        // Start auto-refresh
        this.startCameraRefresh();
    },

    loadCameraSnapshot(container, cam) {
        const img = container.querySelector('.camera-feed');
        const loading = container.querySelector('.camera-loading');

        // Check if emulator mode is active
        if (window.EmulatorModeManager?.isActive) {
            const mockUrl = window.EmulatorModeManager.getMockCameraUrl(cam.title);
            img.src = mockUrl;
            loading.style.display = 'none';
            img.style.display = 'block';
            return;
        }

        // Build snapshot URL
        let snapshotUrl = this.config.cameraSnapshotUrl;
        if (cam.entity) {
            snapshotUrl += `?entity=${encodeURIComponent(cam.entity)}`;
        }
        // Add cache buster
        snapshotUrl += (snapshotUrl.includes('?') ? '&' : '?') + `t=${Date.now()}`;

        // Create a new image to preload
        const newImg = new Image();
        newImg.onload = () => {
            img.src = newImg.src;
            loading.style.display = 'none';
            img.style.display = 'block';
        };
        newImg.onerror = () => {
            // Auto-activate emulator mode on camera failure if autoDetect enabled
            if (window.AppConfig?.demo?.autoDetect && window.EmulatorModeManager && !window.EmulatorModeManager.isActive) {
                window.EmulatorModeManager.activate('camera unavailable');
                const mockUrl = window.EmulatorModeManager.getMockCameraUrl(cam.title);
                img.src = mockUrl;
                loading.style.display = 'none';
                img.style.display = 'block';
                return;
            }
            loading.textContent = 'Unavailable';
            img.style.display = 'none';
        };
        newImg.src = snapshotUrl;
    },

    startCameraRefresh() {
        this.stopCameraRefresh();
        this.refreshIntervalId = setInterval(() => {
            if (!this.isActive) return;

            const containers = this.overlayElement.querySelectorAll('.camera-feed-container');
            this.cameras.forEach((cam, index) => {
                const container = containers[index];
                if (container) {
                    this.loadCameraSnapshot(container, cam);
                }
            });
        }, this.config.refreshIntervalMs);
    },

    stopCameraRefresh() {
        if (this.refreshIntervalId) {
            clearInterval(this.refreshIntervalId);
            this.refreshIntervalId = null;
        }
    },

    hide() {
        if (!this.isActive) return;

        this.isActive = false;
        this.clearTimeout();
        this.stopCameraRefresh();

        this.overlayElement.classList.remove('visible');

        // Clear all images
        this.overlayElement.querySelectorAll('.camera-feed').forEach(img => {
            img.src = '';
        });

        console.log('[CAMERA] Overlay hidden');

        if (window.uiStore && window.uiStore.logWebsocketMessage) {
            window.uiStore.logWebsocketMessage('Camera overlay dismissed');
        }
    },

    startTimeout() {
        this.clearTimeout();
        this.timeoutId = setTimeout(() => {
            console.log('[CAMERA] Auto-timeout reached');
            this.handleAction('timeout');
        }, this.config.autoTimeoutMs);
    },

    clearTimeout() {
        if (this.timeoutId) {
            clearTimeout(this.timeoutId);
            this.timeoutId = null;
        }
    },

    handleAction(button) {
        if (!this.isActive) return false;

        const actionDef = this.actions[button];
        if (!actionDef && button !== 'timeout') {
            return false;
        }

        console.log(`[CAMERA] Action: ${button}`);

        // GO and timeout dismiss the overlay
        // Left/right send webhook but keep overlay open
        if (button === 'go' || button === 'timeout') {
            this.hide();
        } else {
            // Send webhook for left/right buttons (keep overlay open)
            this.sendWebhook(button);
        }

        return true;
    },

    sendWebhook(button) {
        const webhookUrl = AppConfig.webhookUrl;

        // Use same format as hardware-input.js sendWebhook()
        const payload = {
            device_type: 'Panel',
            panel_context: 'camera_overlay',
            button: button,
            id: '1'
        };

        console.log(`[CAMERA] Sending webhook: ${button}`);

        fetch(webhookUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
        .then(response => {
            if (response.ok) {
                console.log(`[CAMERA] Webhook sent successfully: ${button}`);
            } else {
                console.log(`[CAMERA] Webhook failed: HTTP ${response.status}`);
            }
        })
        .catch(error => {
            console.log(`[CAMERA] Webhook error: ${error.message}`);
        });
    }
};

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    CameraOverlayManager.init();
});

// Make available globally
window.CameraOverlayManager = CameraOverlayManager;
