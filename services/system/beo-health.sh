#!/bin/bash
# Auto-recover failed beo-* services.
# Runs every 5 minutes via beo-health.timer.
SERVICES="beo-http beo-input beo-router beo-sonos beo-masterlink beo-bluetooth beo-cd-source beo-usb-source"
for svc in $SERVICES; do
    if systemctl is-failed --quiet "$svc"; then
        logger -t beo-health "Auto-recovering $svc"
        systemctl reset-failed "$svc"
        systemctl start "$svc"
    fi
done
