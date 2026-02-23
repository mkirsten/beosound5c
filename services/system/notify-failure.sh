#!/bin/bash
# Called by systemd OnFailure=beo-notify-failure@%n.service
# Publishes failure info to MQTT using aiomqtt (already installed).
# HA automation picks it up and sends a notification.
SERVICE="$1"

RESTARTS=$(systemctl show "$SERVICE" --property=NRestarts --value 2>/dev/null || echo "0")
RESULT=$(systemctl show "$SERVICE" --property=Result --value 2>/dev/null || echo "unknown")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Log locally regardless of whether MQTT succeeds
logger -t beo-health "Service failed: $SERVICE (restarts: $RESTARTS, result: $RESULT)"

# Publish to MQTT using Python + aiomqtt (same library our services use).
# Reads config from the same files our services use. Fails gracefully if
# HA/broker is unreachable — the local syslog entry above still records it.
python3 - "$SERVICE" "$RESTARTS" "$RESULT" "$TIMESTAMP" << 'PYEOF'
import asyncio, json, os, sys

async def publish():
    try:
        import aiomqtt
    except ImportError:
        print("aiomqtt not available, skipping MQTT notification", file=sys.stderr)
        return

    service, restarts, result, timestamp = sys.argv[1:5]

    # Read config.json for device name and broker
    config = {}
    try:
        with open("/etc/beosound5c/config.json") as f:
            config = json.load(f)
    except Exception:
        pass

    device = config.get("device", "unknown")
    transport = config.get("transport", {})
    broker = transport.get("mqtt_broker", "homeassistant.local")

    # Read MQTT credentials from secrets.env
    mqtt_user = ""
    mqtt_pass = ""
    try:
        with open("/etc/beosound5c/secrets.env") as f:
            for line in f:
                line = line.strip()
                if line.startswith("MQTT_USER="):
                    mqtt_user = line.split("=", 1)[1].strip("'\"")
                elif line.startswith("MQTT_PASSWORD="):
                    mqtt_pass = line.split("=", 1)[1].strip("'\"")
    except Exception:
        pass

    slug = device.lower().replace(" ", "_")
    topic = f"beosound5c/{slug}/failure"
    payload = json.dumps({
        "service": service,
        "device": device,
        "restarts": int(restarts) if restarts.isdigit() else 0,
        "result": result,
        "timestamp": timestamp,
    })

    try:
        async with aiomqtt.Client(
            hostname=broker,
            username=mqtt_user or None,
            password=mqtt_pass or None,
            timeout=5,
        ) as client:
            await client.publish(topic, payload)
            print(f"Published failure notification to {topic}", file=sys.stderr)
    except Exception as e:
        print(f"MQTT publish failed (broker {broker}): {e}", file=sys.stderr)

asyncio.run(publish())
PYEOF
