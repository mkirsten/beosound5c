/**
 * Visualizer backdrop for the PLAYING view.
 *
 * Draws what the device is playing into a full-screen layer behind the
 * now-playing text: VU meters, a spectrum over a lake in two colourways, or
 * a MilkDrop-style plasma. Measurements come from beo-visualizer over a
 * WebSocket; everything time-based is smoothed here, at display refresh,
 * rather than in the service, which would tie the look to its analysis
 * block rate and make it stutter.
 *
 * This is a backdrop, not a view: which one is showing, and whether it is
 * showing at all, is decided by js/backdrop.js. All this module does is
 * mount a canvas, draw the chosen mode, and stop when asked.
 */
(function () {
    'use strict';

    const FEED = (typeof getServiceUrl === 'function')
        ? getServiceUrl('visualizerUrl', 8783)
        : (window.AppConfig?.visualizerUrl || 'http://localhost:8783');

    let canvas = null, ctx = null, parent = null;
    // The track text, copied on top. The canvas covers the whole screen and
    // is painted on the body, so the real text — nested inside #viewport,
    // which has a transform and therefore its own stacking context — cannot
    // be raised above it. See js/text-mirror.js.
    let textLayer = null;
    let mode = 'vu';
    let running = false, rafId = null;
    let W = 0, H = 0, DPR = 1;

    const MODES = ['vu', 'lake', 'lake_blue', 'milkdrop'];
    const LABELS = {
        vu: 'VU Meters',
        lake: 'Spectral Lake',
        lake_blue: 'Spectral Lake — Blue',
        milkdrop: 'Milkdrop',
    };

    // Two palettes for the lake. The blue one takes its colours from the
    // menu arc gradient in index.html (#6699FF to #00FFCC) so the view
    // sits in the same family as the rest of the interface.
    const LAKE_PALETTES = {
        lake: {
            sky:   ['#150e04', '#48310f', '#b8862a', '#f2dfb0'],
            water: ['#7d5c1e', '#2c1f0a', '#050402'],
            bars:  ['#fff3cf', '#e8b55c', '#8a5a12'],
            ridge: ['rgba(24,16,6,0.88)', 'rgba(9,6,2,0.96)'],
            peak:  'rgba(255,246,216,0.95)',
            ripple: '#050402',
        },
        lake_blue: {
            sky:   ['#02060f', '#0d2540', '#2f7fc4', '#a9ecdf'],
            water: ['#6fc0c8', '#123244', '#010305'],
            bars:  ['#d9fff7', '#00ffcc', '#6699ff'],
            // Lifted off black: near-black ridges vanish against a dark
            // blue sky, where the amber palette had a bright sky to
            // silhouette them.
            ridge: ['rgba(30,52,72,0.92)', 'rgba(12,24,36,0.97)'],
            peak:  'rgba(217,255,247,0.95)',
            ripple: '#010305',
        },
    };

    // ── Live measurements from beo-visualizer ──────────────────────────
    // rms/peak are dBFS; bands are already mapped to 0..1 over 60 dB by
    // the service. Everything time-based below is smoothed here rather
    // than there, so it runs at display refresh instead of block rate.
    const audio = {
        bands: new Float32Array(24),
        rms: [-120, -120],
        silent: true,
        connected: false,
    };

    // ── Connection ─────────────────────────────────────────────────────
    let ws = null, reconnectTimer = null;

    // A socket can die without the page hearing about it: the server drops
    // the client, the close event never arrives, and the page keeps
    // redrawing whatever it last received, forever. Seen twice on a TV
    // over Cast, where the service logged the client gone while the page
    // still believed it was connected. Measurements arrive ~43 times a
    // second, so a couple of seconds of silence means the feed is gone
    // whatever the socket says about itself.
    const FEED_SILENCE_S = 3.0;
    let lastFrameAt = 0;

    function connect() {
        // In the emulator there is no feed and never will be, so skip the
        // socket entirely rather than retrying it every two seconds and
        // filling the console. The synthetic signal below takes over.
        if (window.__demoBackendInstalled) return;
        // Already up, or on its way. mount() is called again on every step
        // between visualizer modes, and without this each step opened
        // another socket while the last stayed open: the service counted
        // one client, then two, then three, all of them fed at 43 frames a
        // second. Caught on a device, invisible in the emulator, which has
        // no socket at all.
        if (ws && (ws.readyState === WebSocket.CONNECTING
                   || ws.readyState === WebSocket.OPEN)) return;
        try {
            ws = new WebSocket(FEED.replace(/^http/, 'ws') + '/ws');
        } catch (e) { scheduleReconnect(); return; }

        ws.onopen = () => { audio.connected = true; lastFrameAt = performance.now(); };
        ws.onmessage = (ev) => {
            const d = JSON.parse(ev.data);
            if (d.mode && !d.rms) {
                // The handshake carries the service's own remembered mode.
                // It is ignored: which backdrop is showing is the router's
                // to decide now, so that the panel and a television showing
                // the same UI never disagree. Only the tuning is taken.
                if (typeof d.vu_reference_dbfs === 'number') {
                    VU_REF_DBFS = d.vu_reference_dbfs;
                }
                return;
            }
            lastFrameAt = performance.now();
            if (d.bands) {
                if (audio.bands.length !== d.bands.length) {
                    audio.bands = new Float32Array(d.bands.length);
                    peaks = new Float32Array(d.bands.length);
                    peakAge = new Float32Array(d.bands.length);
                    peakVel = new Float32Array(d.bands.length);
                }
                audio.bands.set(d.bands);
                audio.rms = d.rms;
                audio.silent = d.silent;
            }
        };
        ws.onclose = () => { audio.connected = false; scheduleReconnect(); };
        ws.onerror = () => { try { ws.close(); } catch (e) {} };
    }

    function scheduleReconnect() {
        if (reconnectTimer) return;
        reconnectTimer = setTimeout(() => { reconnectTimer = null; connect(); }, 2000);
    }

    // Drop a silent socket ourselves rather than wait for a close that
    // may never come. The handler is detached first so the teardown
    // cannot schedule a second reconnect on top of this one.
    function checkFeedAlive() {
        if (!audio.connected) return;
        if ((performance.now() - lastFrameAt) / 1000 < FEED_SILENCE_S) return;
        audio.connected = false;
        try {
            if (ws) { ws.onclose = null; ws.onerror = null; ws.close(); }
        } catch (e) { /* already gone */ }
        ws = null;
        scheduleReconnect();
    }
    setInterval(checkFeedAlive, 1000);

    // Demo fallback: with no service behind the feed, synthesise something
    // plausible so the emulator can show all three modes. Only ever runs
    // when the socket is down.
    let demoPhase = 0;
    function demoFrame(dt) {
        demoPhase += dt;
        const n = audio.bands.length;
        for (let i = 0; i < n; i++) {
            const f = i / n;
            const env = Math.pow(1 - f, 1.6);
            const wob = Math.sin(demoPhase * (1.1 + f * 5) + i) * 0.5 + 0.5;
            audio.bands[i] = Math.max(0, Math.min(1, env * (0.45 + 0.55 * wob)));
        }
        const level = -22 + 8 * (Math.sin(demoPhase * 1.7) * 0.5 + 0.5);
        audio.rms = [level, level - 1.2 * Math.sin(demoPhase * 2.3)];
        audio.silent = false;
    }


    // ── Canvas sizing ──────────────────────────────────────────────────
    // A budget on pixels, not on the ratio. Device pixel ratio is a
    // property of the screen; what costs time each frame is how many
    // pixels the canvas has to fill. The device's own 1024x768 panel
    // reports ratio 1 and never comes near this. A 1280x720 TV reporting
    // ratio 2 asks for 3.7 megapixels a frame, nearly five times as many,
    // on far weaker silicon: measured at 20 fps on a BRAVIA over Cast.
    // Capping the area holds the cost roughly level wherever this runs,
    // including phones, which commonly report 3.
    const MAX_CANVAS_PX = 1.5e6;

    function resize() {
        W = canvas.clientWidth; H = canvas.clientHeight;
        const want = Math.min(window.devicePixelRatio || 1, 2);
        const area = W * H * want * want;
        // Below the floor the upscale starts to show. A screen big enough
        // to hit it would be slower still at full ratio, so softness is
        // the better trade.
        DPR = area > MAX_CANVAS_PX
            ? Math.max(0.5, want * Math.sqrt(MAX_CANVAS_PX / area))
            : want;
        canvas.width = Math.round(W * DPR);
        canvas.height = Math.round(H * DPR);
        ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
        lakeSky = null; lakeWater = null;   // size-dependent
        feedback = null;
    }
    window.addEventListener('resize', resize);

    // ══ MODE 1: VU meters ══════════════════════════════════════════════
    // Real VU ballistics: the needle reaches 99% of a step in 300 ms, so
    // the time constant is 300/ln(100) ≈ 65 ms. Without this a meter just
    // looks like a jittery bar graph.
    const VU_TAU = 0.065;
    // 0 VU reference, in dBFS. Programme level varies a lot between
    // tracks — measured here from -15 dBFS on loud masters down to -31
    // in quiet passages — so this is a judgement call, not a constant.
    // -14 puts typical material around three quarters up the scale and
    // keeps the red section for real peaks. Overridable from config
    // (visualizer.vu_reference_dbfs) via the feed's hello frame, so it
    // can be tuned without touching this file.
    let VU_REF_DBFS = -14;
    const vuNeedle = [-20, -20];  // in VU units

    function vuFromDbfs(db) { return db - VU_REF_DBFS; }

    function drawVU(dt) {
        for (let i = 0; i < 2; i++) {
            const target = Math.max(-24, Math.min(4, vuFromDbfs(audio.rms[i] ?? -120)));
            const a = 1 - Math.exp(-dt / VU_TAU);
            vuNeedle[i] += (target - vuNeedle[i]) * a;
        }
        ctx.fillStyle = '#0a0a0b';
        ctx.fillRect(0, 0, W, H);

        // Stacked, not side by side: one meter above the other, each as
        // wide as the display allows. The aspect is capped so a wide
        // screen does not stretch the dial into a letterbox — a VU face
        // reads wrong much past 2:1.
        const pad = Math.min(W, H) * 0.05;
        const gap = pad * 0.7;
        const mh = (H - pad * 2 - gap) / 2;
        const mw = Math.min(W - pad * 2, mh * 1.55);   // matches a real VU face
        const left = (W - mw) / 2;
        drawMeter(left, pad, mw, mh, vuNeedle[0]);
        drawMeter(left, pad + mh + gap, mw, mh, vuNeedle[1]);
    }

    function roundRect(x, y, w, h, r) {
        ctx.beginPath();
        ctx.moveTo(x + r, y);
        ctx.arcTo(x + w, y, x + w, y + h, r);
        ctx.arcTo(x + w, y + h, x, y + h, r);
        ctx.arcTo(x, y + h, x, y, r);
        ctx.arcTo(x, y, x + w, y, r);
        ctx.closePath();
    }

    function drawMeter(x, y, w, h, vu) {
        // Bezel
        const bez = h * 0.07;
        ctx.save();
        const bg = ctx.createLinearGradient(x, y, x, y + h);
        bg.addColorStop(0, '#2a2a2c'); bg.addColorStop(0.5, '#131315'); bg.addColorStop(1, '#0b0b0c');
        ctx.fillStyle = bg;
        roundRect(x, y, w, h, h * 0.06); ctx.fill();

        // Backlit face
        const fx = x + bez, fy = y + bez, fw = w - bez * 2, fh = h - bez * 2;
        ctx.save();
        roundRect(fx, fy, fw, fh, h * 0.03); ctx.clip();
        const glow = ctx.createRadialGradient(
            fx + fw / 2, fy + fh * 1.0, fh * 0.05,
            fx + fw / 2, fy + fh * 0.9, fw * 0.85);
        glow.addColorStop(0, '#fff6d8');
        glow.addColorStop(0.55, '#f7e9c0');
        glow.addColorStop(1, '#e6d3a4');
        ctx.fillStyle = glow;
        ctx.fillRect(fx, fy, fw, fh);

        // Geometry of the arc the needle sweeps.
        //
        // Radius comes from the face WIDTH, not its height: a VU scale is
        // a wide, shallow arc, and deriving it from the height produces a
        // small steep one with the numbers piled on top of each other.
        const cx = fx + fw / 2;
        const HALF = 0.52;                    // half sweep, radians
        const R = fw * 0.80;                  // spans ~80% of the face
        const arcTop = fy + fh * 0.42;        // where the arc crests
        const cy = arcTop + R;                // pivot, well below the face
        const vuToAngle = (v) => -Math.PI / 2 + HALF * ((v + 8.5) / 11.5);

        const px = (a, r) => cx + Math.cos(a) * r;
        const py = (a, r) => cy + Math.sin(a) * r;

        // Scale arc: black up to 0 VU, thicker red above it
        ctx.lineWidth = Math.max(1.2, fh * 0.012);
        ctx.strokeStyle = '#1a1a1a';
        ctx.beginPath();
        ctx.arc(cx, cy, R, vuToAngle(-20), vuToAngle(0)); ctx.stroke();
        ctx.strokeStyle = '#b83024';
        ctx.lineWidth = Math.max(2.2, fh * 0.026);
        ctx.beginPath();
        ctx.arc(cx, cy, R, vuToAngle(0), vuToAngle(3)); ctx.stroke();

        // Ticks above the arc line, numbers above the ticks
        const majors = [-20, -10, -7, -5, -3, -2, -1, 0, 1, 2, 3];
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.font = `600 ${Math.round(fh * 0.105)}px Montserrat, Helvetica, sans-serif`;
        for (const m of majors) {
            const a = vuToAngle(m);
            const red = m > 0;
            ctx.strokeStyle = red ? '#b83024' : '#1a1a1a';
            ctx.lineWidth = Math.max(1, fh * 0.010);
            ctx.beginPath();
            ctx.moveTo(px(a, R), py(a, R));
            ctx.lineTo(px(a, R * 1.035), py(a, R * 1.035));
            ctx.stroke();
            ctx.fillStyle = red ? '#b83024' : '#1a1a1a';
            ctx.fillText(String(Math.abs(m)), px(a, R * 1.075), py(a, R * 1.075));
        }
        // Minor ticks between the majors, as on the original
        ctx.strokeStyle = 'rgba(26,26,26,0.75)';
        ctx.lineWidth = Math.max(0.8, fh * 0.006);
        for (const m of [-15, -8.5, -6, -4, -2.5, -1.5, -0.5]) {
            const a = vuToAngle(m);
            ctx.beginPath();
            ctx.moveTo(px(a, R), py(a, R));
            ctx.lineTo(px(a, R * 1.022), py(a, R * 1.022));
            ctx.stroke();
        }

        // Secondary percentage scale, below the arc line
        ctx.font = `${Math.round(fh * 0.072)}px Montserrat, Helvetica, sans-serif`;
        ctx.fillStyle = 'rgba(26,26,26,0.55)';
        for (const [pct, v] of [[20, -14], [40, -8], [60, -4.4], [80, -1.9], [100, 0]]) {
            const a = vuToAngle(v);
            ctx.fillText(String(pct), px(a, R * 0.945), py(a, R * 0.945));
        }

        // Labels
        ctx.textAlign = 'left';
        ctx.font = `500 ${Math.round(fh * 0.13)}px Montserrat, Helvetica, sans-serif`;
        ctx.fillStyle = '#1a1a1a';
        ctx.fillText('VU', fx + fw * 0.08, fy + fh * 0.18);

        // Needle. Drawn from just inside the face bottom to the arc, so
        // the pivot itself stays hidden below the dial like the real thing.
        const na = vuToAngle(Math.max(-20, Math.min(3, vu)));
        ctx.strokeStyle = '#151515';
        ctx.lineWidth = Math.max(1.4, fh * 0.016);
        ctx.beginPath();
        ctx.moveTo(px(na, R * 0.62), py(na, R * 0.62));
        ctx.lineTo(px(na, R * 0.995), py(na, R * 0.995));
        ctx.stroke();
        ctx.restore();

        // Screw
        ctx.fillStyle = '#0d0d0e';
        ctx.beginPath();
        ctx.arc(x + w - bez * 2.2, y + bez * 2.2, h * 0.035, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
    }

    // ══ MODE 2: Spectral lake ══════════════════════════════════════════
    // Peak markers hold for a moment, then accelerate downward — the
    // classic behaviour, and the thing that makes a spectrum readable.
    const PEAK_HOLD_S = 1.0;
    const PEAK_GRAVITY = 1.8;       // units per second squared
    let peaks = new Float32Array(24);
    let peakAge = new Float32Array(24);
    let peakVel = new Float32Array(24);
    let lakeSky = null, lakeWater = null, lakeKey = null;

    function buildLake(horizon, pal) {
        // Sky and water are separate gradients. One gradient across the
        // whole frame keeps the bright band going below the horizon, which
        // washes the reflection out and stops it reading as water.
        lakeSky = ctx.createLinearGradient(0, 0, 0, horizon);
        [0.00, 0.40, 0.82, 1.00].forEach((o, i) => lakeSky.addColorStop(o, pal.sky[i]));

        lakeWater = ctx.createLinearGradient(0, horizon, 0, H);
        // 0.28 rather than 0.12: a short transition leaves a hard bright
        // rule along the waterline instead of a fading reflection.
        [0.00, 0.28, 1.00].forEach((o, i) => lakeWater.addColorStop(o, pal.water[i]));
    }

    function drawLake(dt, key) {
        const pal = LAKE_PALETTES[key];
        const horizon = H * 0.62;
        if (!lakeSky || lakeKey !== key) { buildLake(horizon, pal); lakeKey = key; }

        ctx.fillStyle = lakeSky;
        ctx.fillRect(0, 0, W, horizon);

        // Mountains: two static ridges from a fixed pseudo-random profile,
        // so the silhouette does not crawl between frames.
        drawRidge(horizon, H * 0.20, 0.9, pal.ridge[0], 7);
        drawRidge(horizon, H * 0.13, 1.7, pal.ridge[1], 13);

        ctx.fillStyle = lakeWater;
        ctx.fillRect(0, horizon, W, H - horizon);

        // Reflection: mirrored, heavily dimmed, and broken up by a few
        // horizontal bands so it reads as a surface rather than a mirror.
        ctx.save();
        ctx.beginPath(); ctx.rect(0, horizon, W, H - horizon); ctx.clip();
        ctx.globalAlpha = 0.20;
        ctx.translate(0, horizon * 2);
        ctx.scale(1, -1);
        drawBars(horizon, dt, true, pal);
        ctx.restore();

        ctx.save();
        ctx.globalAlpha = 0.35;
        ctx.fillStyle = pal.ripple;
        for (let y = horizon + 6; y < H; y += 9) {
            ctx.fillRect(0, y, W, 3);
        }
        ctx.restore();

        drawBars(horizon, dt, false, pal);
    }

    function drawRidge(horizon, height, seed, fill, teeth) {
        ctx.fillStyle = fill;
        ctx.beginPath();
        ctx.moveTo(0, horizon);
        for (let i = 0; i <= teeth; i++) {
            const x = (i / teeth) * W;
            // Deterministic pseudo-noise: same silhouette every frame.
            const n = Math.abs(Math.sin(i * 12.9898 * seed) * 43758.5453) % 1;
            ctx.lineTo(x, horizon - height * (0.35 + n * 0.65));
        }
        ctx.lineTo(W, horizon);
        ctx.closePath();
        ctx.fill();
    }

    function drawBars(horizon, dt, reflection, pal) {
        const n = audio.bands.length;
        const bw = W / n;
        const maxH = horizon * 0.86;

        for (let i = 0; i < n; i++) {
            const v = audio.bands[i];

            if (!reflection) {
                // Peak marker physics, run once per frame on the real pass
                if (v >= peaks[i]) {
                    peaks[i] = v; peakAge[i] = 0; peakVel[i] = 0;
                } else {
                    peakAge[i] += dt;
                    if (peakAge[i] > PEAK_HOLD_S) {
                        peakVel[i] += PEAK_GRAVITY * dt;
                        peaks[i] = Math.max(v, peaks[i] - peakVel[i] * dt);
                    }
                }
            }

            const x = i * bw + bw * 0.14;
            const w = bw * 0.72;
            const h = v * maxH;

            const g = ctx.createLinearGradient(0, horizon - h, 0, horizon);
            g.addColorStop(0, pal.bars[0]);
            g.addColorStop(0.5, pal.bars[1]);
            g.addColorStop(1, pal.bars[2]);
            ctx.fillStyle = g;
            ctx.fillRect(x, horizon - h, w, h);

            const p = peaks[i] * maxH;
            if (p > 2) {
                ctx.fillStyle = pal.peak;
                ctx.fillRect(x, horizon - p - 3, w, 2.5);
            }
        }
    }

    // ══ MODE 3: Milkdrop-inspired ══════════════════════════════════════
    // Feedback warp: each frame the previous one is drawn back slightly
    // zoomed and rotated, then new geometry is added on top. That
    // recursion is what gives MilkDrop its tunnels and flowers.
    let feedback = null, fbCtx = null, mdPhase = 0, mdSpin = 0;

    function drawMilkdrop(dt) {
        if (!feedback) {
            feedback = document.createElement('canvas');
            feedback.width = canvas.width; feedback.height = canvas.height;
            fbCtx = feedback.getContext('2d');
            fbCtx.setTransform(DPR, 0, 0, DPR, 0, 0);
            fbCtx.fillStyle = '#000'; fbCtx.fillRect(0, 0, W, H);
        }
        const n = audio.bands.length;
        const bass = avg(0, Math.max(1, n * 0.15));
        const mid = avg(n * 0.15, n * 0.5);
        const treble = avg(n * 0.5, n);
        mdPhase += dt * (0.35 + bass * 1.4);
        mdSpin += dt * (0.15 + mid * 0.75);

        // Warp the previous frame. The per-frame step has to stay small —
        // a large zoom throws the trail off screen before it can build up
        // into the tunnel that makes this look like Milkdrop at all.
        fbCtx.save();
        fbCtx.translate(W / 2, H / 2);
        fbCtx.rotate((0.12 + mid * 0.7) * dt);
        const zoom = 1 + (0.16 + bass * 0.95) * dt;
        fbCtx.scale(zoom, zoom);
        fbCtx.translate(-W / 2, -H / 2);
        fbCtx.drawImage(feedback, 0, 0, W, H);
        fbCtx.restore();

        // Gentle fade so trails die over a second or so rather than
        // instantly or never.
        fbCtx.globalAlpha = 0.030 + (1 - bass) * 0.020;
        fbCtx.fillStyle = '#000';
        fbCtx.fillRect(0, 0, W, H);
        fbCtx.globalAlpha = 1;

        // Two counter-rotating spectral rosettes, drawn as smooth closed
        // curves. One alone reads as a wobbling blob; the pair gives the
        // interference that carries the effect.
        fbCtx.lineJoin = 'round';
        rosette(bass, treble, mdSpin, 0.30, 1.0, 34 + treble * 30);
        rosette(mid, treble, -mdSpin * 0.6, 0.19, 0.55, 18 + mid * 26);

        ctx.drawImage(feedback, 0, 0, W, H);
    }

    function rosette(drive, treble, spin, radiusFrac, weight, hue) {
        const n = audio.bands.length;
        const cx = W / 2, cy = H / 2;
        const R = Math.min(W, H) * radiusFrac;
        const pts = [];
        for (let i = 0; i < n; i++) {
            const a = (i / n) * Math.PI * 2 + spin;
            const r = R * (0.5 + audio.bands[i] * 1.05)
                * (1 + 0.16 * Math.sin(a * 4 + mdPhase * 1.7));
            pts.push([cx + Math.cos(a) * r, cy + Math.sin(a) * r]);
        }
        // Midpoint-quadratic smoothing: straight segments between 24 points
        // look like a polygon, which nothing in Milkdrop ever does.
        fbCtx.beginPath();
        let [mx, my] = mid2(pts[n - 1], pts[0]);
        fbCtx.moveTo(mx, my);
        for (let i = 0; i < n; i++) {
            const cur = pts[i], nxt = pts[(i + 1) % n];
            const [ex, ey] = mid2(cur, nxt);
            fbCtx.quadraticCurveTo(cur[0], cur[1], ex, ey);
        }
        fbCtx.closePath();

        fbCtx.globalCompositeOperation = 'lighter';
        // Barely-there body. The feedback redraws this every frame, so
        // anything heavier accumulates into an opaque blob within a second.
        fbCtx.fillStyle = `hsla(${hue}, 90%, ${22 + drive * 20}%, ${0.030 * weight})`;
        fbCtx.fill();
        fbCtx.lineWidth = (1.2 + drive * 3.5) * weight;
        fbCtx.strokeStyle = `hsla(${hue + treble * 20}, 95%, ${55 + drive * 32}%, ${0.9 * weight})`;
        fbCtx.stroke();
        fbCtx.globalCompositeOperation = 'source-over';
    }

    function mid2(a, b) { return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]; }

    function avg(a, b) {
        a = Math.floor(a); b = Math.ceil(b);
        let s = 0, c = 0;
        for (let i = a; i < b && i < audio.bands.length; i++) { s += audio.bands[i]; c++; }
        return c ? s / c : 0;
    }

    // ── Main loop ──────────────────────────────────────────────────────
    let last = performance.now();
    function frame(now) {
        if (!running) return;
        const dt = Math.min(0.1, (now - last) / 1000);
        last = now;

        // Re-measure every frame. The layer can be mounted while hidden,
        // and a hidden element reports zero size — sizing once at mount
        // then leaves everything drawn into a 0x0 surface, which shows up
        // as a black screen.
        if (canvas.clientWidth !== W || canvas.clientHeight !== H) {
            if (canvas.clientWidth > 0 && canvas.clientHeight > 0) resize();
        }
        if (W === 0 || H === 0) { rafId = requestAnimationFrame(frame); return; }

        // Synthesise only where there is nothing to measure. On a device a
        // dead feed should look dead, needles at rest, rather than
        // inventing a light show for music that is not playing.
        if (!audio.connected && window.__demoBackendInstalled) demoFrame(dt);

        if (mode === 'vu') drawVU(dt);
        else if (mode === 'lake' || mode === 'lake_blue') drawLake(dt, mode);
        else drawMilkdrop(dt);
        rafId = requestAnimationFrame(frame);
    }

    // ── Public surface ─────────────────────────────────────────────────

    function mount(host, which) {
        if (!canvas) {
            canvas = document.createElement('canvas');
            canvas.className = 'visualizer-backdrop';
            ctx = canvas.getContext('2d');
        }
        if (!textLayer) {
            textLayer = document.createElement('div');
            textLayer.className = 'visualizer-text';
        }
        parent = host || document.body;
        if (canvas.parentNode !== parent) parent.appendChild(canvas);
        if (textLayer.parentNode !== parent) parent.appendChild(textLayer);
        window.TextMirror?.register(textLayer);
        window.TextMirror?.render(textLayer);
        if (which) mode = which;
        // Stale caches: these are sized to the previous canvas.
        lakeSky = null; lakeWater = null; feedback = null;
        connect();
        if (!running) {
            running = true;
            last = performance.now();
            rafId = requestAnimationFrame(frame);
        }
    }

    function unmount() {
        running = false;
        if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
        // Drop the socket too. A backdrop nobody is looking at has no
        // business holding a 43-frames-a-second feed open.
        if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
        try { if (ws) { ws.onclose = null; ws.onerror = null; ws.close(); } } catch (e) {}
        ws = null;
        audio.connected = false;
        if (canvas && canvas.parentNode) canvas.parentNode.removeChild(canvas);
        if (textLayer) {
            window.TextMirror?.unregister(textLayer);
            if (textLayer.parentNode) textLayer.parentNode.removeChild(textLayer);
        }
    }

    window.BeoVisualizer = {
        MODES: MODES,
        LABELS: LABELS,
        mount: mount,
        unmount: unmount,
        setMode: function (which) {
            if (MODES.includes(which)) mode = which;
        },
        get mode() { return mode; },
        get connected() { return audio.connected; },
    };
})();
