#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — yt-dlp (music video feature)
# =============================================================================
# yt-dlp resolves the YouTube streams behind the immersive music-video view.
# Two constraints shape how it's installed:
#
#   1. It must stay CURRENT. YouTube changes its player often enough that a
#      build more than a few weeks old stops extracting ("Requested format is
#      not available" / SABR), and the Debian package lags far behind. So we
#      install from pip and add a weekly self-update timer.
#
#   2. It must NOT be the PyInstaller standalone binary. That build
#      self-extracts ~70MB into /tmp on every run and leaks the extraction
#      dir whenever the process is killed — which music_video.py does on
#      every timeout. With sd-hardening's 200MB tmpfs /tmp, a handful of
#      leaked runs fills the disk and breaks yt-dlp (and everything else
#      using /tmp). The pip package runs in-place: no extraction, no leaks.
# =============================================================================

install_ytdlp() {
    log_section "Installing yt-dlp (music video feature)"

    # Remove any previously-installed standalone binary so pip's entry point
    # (also /usr/local/bin/yt-dlp) takes over and the /tmp leaks stop.
    # PyInstaller onefile binaries are >20MB; pip's entry point is a tiny
    # Python script — use size to tell them apart.
    local BIN="/usr/local/bin/yt-dlp"
    if [ -f "$BIN" ] && [ "$(stat -c %s "$BIN" 2>/dev/null || echo 0)" -gt 1000000 ]; then
        rm -f "$BIN"
        log_info "Removed legacy standalone yt-dlp binary"
    fi

    log_info "Installing/upgrading yt-dlp from pip..."
    if pip3 install -U -q --ignore-installed --break-system-packages "yt-dlp[default]" 2>/dev/null \
            || pip3 install -U -q --ignore-installed "yt-dlp[default]"; then
        log_success "yt-dlp installed: $(yt-dlp --version 2>/dev/null || echo '?')"
    else
        log_warn "yt-dlp install failed — music video feature will be unavailable"
        return
    fi

    # JS runtime for YouTube player solving. yt-dlp increasingly requires one
    # (deno is the only runtime it enables by default); without it, extraction
    # rides on signature-free fallback clients that YouTube keeps closing.
    # No armv7 deno build exists — those devices keep using the fallbacks.
    if ! command -v deno &>/dev/null; then
        local DENO_ASSET=""
        case "$(uname -m)" in
            aarch64) DENO_ASSET="deno-aarch64-unknown-linux-gnu.zip" ;;
            x86_64)  DENO_ASSET="deno-x86_64-unknown-linux-gnu.zip" ;;
        esac
        if [ -n "$DENO_ASSET" ]; then
            log_info "Installing deno (yt-dlp JS runtime)..."
            # Guarded end-to-end: a failed download/extract/install must not
            # abort the installer (set -e) — deno is an enhancement, yt-dlp
            # still works via its fallback clients without it.
            local TMP_DIR
            TMP_DIR=$(mktemp -d || true)
            if [ -n "$TMP_DIR" ] \
                    && curl -fsSL "https://github.com/denoland/deno/releases/latest/download/$DENO_ASSET" \
                    -o "$TMP_DIR/deno.zip" \
                    && python3 -m zipfile -e "$TMP_DIR/deno.zip" "$TMP_DIR" \
                    && install -m 755 "$TMP_DIR/deno" /usr/local/bin/deno 2>/dev/null; then
                log_success "deno installed: $(/usr/local/bin/deno --version 2>/dev/null | head -1)"
            else
                log_warn "deno install failed — yt-dlp will use fallback clients"
            fi
            rm -rf "$TMP_DIR"
        fi
    fi

    # Weekly self-update timer so the feature keeps working as YouTube changes.
    log_info "Installing weekly yt-dlp self-update timer..."
    cat > /etc/systemd/system/beo-ytdlp-update.service << 'EOF'
[Unit]
Description=BeoSound 5c — update yt-dlp (music video)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'pip3 install -U -q --ignore-installed --break-system-packages "yt-dlp[default]" || pip3 install -U -q --ignore-installed "yt-dlp[default]"'
EOF

    cat > /etc/systemd/system/beo-ytdlp-update.timer << 'EOF'
[Unit]
Description=BeoSound 5c — weekly yt-dlp update

[Timer]
OnCalendar=weekly
Persistent=true
RandomizedDelaySec=1h

[Install]
WantedBy=timers.target
EOF

    systemctl daemon-reload
    systemctl enable --now beo-ytdlp-update.timer >/dev/null 2>&1 \
        && log_success "yt-dlp weekly update timer enabled" \
        || log_warn "Could not enable yt-dlp update timer"
}
