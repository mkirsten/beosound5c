"""
Webhook sender factory.
This module provides a factory for creating webhook senders.
"""

from hw.webhook_sender import HAWebhookSender
from hw.sonos_sender import SonosSender

# Sender types
HA_WEBHOOK = "ha_webhook"
SONOS_DIRECT = "sonos_direct"

def create_webhook_sender(sender_type=HA_WEBHOOK, **kwargs):
    """
    Create a webhook sender of the specified type.
    
    Args:
        sender_type: The type of sender to create (HA_WEBHOOK or SONOS_DIRECT)
        **kwargs: Additional arguments to pass to the sender constructor
        
    Returns:
        A webhook sender instance
    """
    if sender_type == HA_WEBHOOK:
        webhook_url = kwargs.get("webhook_url", "http://homeassistant.local:8123/api/webhook/beosound5c")
        return HAWebhookSender(webhook_url=webhook_url)
    elif sender_type == SONOS_DIRECT:
        sonos_ip = kwargs.get("sonos_ip", "192.168.0.116")
        return SonosSender(sonos_ip=sonos_ip)
    else:
        raise ValueError(f"Unknown sender type: {sender_type}") 