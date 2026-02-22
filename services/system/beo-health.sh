#!/bin/bash
# Auto-recover failed beo-* services.
# Runs every 5 minutes via beo-health.timer.
# Discovers all beo-* services dynamically — no hardcoded list to maintain.
for svc in $(systemctl list-units 'beo-*.service' --no-legend --no-pager --plain --state=failed | awk '{print $1}'); do
    logger -t beo-health "Auto-recovering $svc"
    systemctl reset-failed "$svc"
    systemctl start "$svc"
done
