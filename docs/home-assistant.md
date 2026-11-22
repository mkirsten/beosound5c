# Home Assistant Integration

BeoSound 5c communicates with Home Assistant via **MQTT** (recommended) or **HTTP webhooks**. The transport is configured in the web UI (Home Assistant → Transport) or via `transport.mode` in `config.json`.

## MQTT (recommended)

Requires an MQTT broker — the [Mosquitto add-on](https://github.com/home-assistant/addons/tree/master/mosquitto) works well. Create a user for the BS5c in the add-on config, then configure the broker in the web UI. MQTT credentials go in `/etc/beosound5c/secrets.env`.

Topics follow the pattern `beosound5c/{device_slug}/out|in|status`:

```
beosound5c/living_room/out      → BS5c sends button events to HA
beosound5c/living_room/in       → HA sends commands to BS5c
beosound5c/living_room/status   → Online/offline (retained)
```

The device slug is derived from your device name (e.g. "Living Room" → `living_room`).

### Receiving events from BS5c

```yaml
trigger:
  - platform: mqtt
    topic: "beosound5c/living_room/out"
```

### Sending commands to BS5c

```yaml
action:
  - action: mqtt.publish
    data:
      topic: "beosound5c/living_room/in"
      payload: '{"command": "wake", "params": {"page": "now_playing"}}'
```

See [`config/homeassistant/example-automation.yaml`](../config/homeassistant/example-automation.yaml) for complete examples covering both MQTT and webhook transports.

## Webhooks

Set `transport.mode` to `"webhook"` and configure a webhook URL in the web UI. The BS5c will POST events to that URL. No broker required, but there is no inbound command path — HA can't send commands back to the BS5c.

For bidirectional control, use MQTT or `"both"`.

## Showing Home Assistant readings on the device

The HOME view puts entity states on the arc, scrolled with the wheel like any
other list. It reads through `beo-input`, which already holds a long-lived
token, so **no Home Assistant configuration is needed** — in particular none of
the framing and auth changes described in the next section.

List the entities in `config.json`, in the order you want them:

```json
"home_assistant": {
  "url": "http://homeassistant.local:8123",
  "panel": [
    "sensor.outdoor_temperature",
    { "entity": "sensor.battery_level", "label": "Battery", "icon": "battery-charging" },
    { "entity": "sensor.solar_now", "label": "Solar", "icon": "sun-dim" }
  ]
}
```

A bare string uses the entity's own `friendly_name`. The object form overrides
the label and the [Phosphor](https://phosphoricons.com) icon; without an icon,
one is picked from the entity domain.

Add the menu item, pointing at the local page:

```json
"menu": {
  "HOME": { "url": "softarc/home.html" }
}
```

Values refresh every 15 seconds and the selected row is preserved across a
refresh. Readings are **read-only**: GO does nothing, because a status panel
that acted on a button press would be a trap. To *control* Home Assistant from
the device, use SCENES, which already sends actions over the configured
transport.

An entity that does not resolve is shown dimmed as `unavailable` rather than
being dropped, since the likeliest cause is a typo in the config.

Requires `HA_TOKEN` in `/etc/beosound5c/secrets.env` (settable from the device's
own configuration page). Without it the view says so instead of failing
silently.

## HA configuration.yaml

Add the following if you want to embed Home Assistant pages in the BS5c UI (e.g. the Security camera view):

```yaml
http:
  cors_allowed_origins:
    - "http://<BEOSOUND5C_IP>"
  use_x_frame_options: false

homeassistant:
  auth_providers:
    - type: trusted_networks
      trusted_networks:
        - <BEOSOUND5C_IP>
      allow_bypass_login: true
    - type: homeassistant
```

**Security note**: These settings allow the BeoSound 5c to embed Home Assistant pages without authentication. Only add IPs you trust. This is intended for local network use.
