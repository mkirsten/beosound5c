# Wheel and buttons in your own webpage items

A menu item with `"type": "webpage"` shows a page in an iframe. By default the wheel scrolls that page and the buttons go to the Home Assistant webhook.

A page served from the device itself (same origin, for example a file in `web/`) can take the wheel and buttons instead. Set the flag and listen for messages:

```html
<script>
  window.beoHandlesInput = true;

  window.addEventListener('message', e => {
    const msg = e.data || {};
    if (msg.type === 'nav') {
      // msg.data.direction: 'clock' | 'counter'
    } else if (msg.type === 'button') {
      // msg.button: 'left' | 'right' | 'go' | ...
    }
  });
</script>
```

These are the same `nav` and `button` messages that the built-in iframe pages receive. Pages on another origin (such as a Home Assistant dashboard) can't set the flag, so they keep the default behaviour.
