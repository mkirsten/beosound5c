"""
Webhook sender interface and implementations.
This module provides different ways to handle webhook messages from the B&O remote.
"""

import asyncio
import aiohttp
import json
from abc import ABC, abstractmethod
from datetime import datetime
import time

class WebhookSender(ABC):
    """Base class for webhook senders"""
    
    @abstractmethod
    async def initialize(self):
        """Initialize the sender"""
        pass
        
    @abstractmethod
    async def send_webhook(self, message):
        """Send a webhook message"""
        pass
        
    @abstractmethod
    async def close(self):
        """Clean up resources"""
        pass

class HAWebhookSender(WebhookSender):
    """Webhook sender that forwards to Home Assistant"""
    
    def __init__(self, webhook_url="http://homeassistant.local:8123/api/webhook/beosound5c"):
        self.webhook_url = webhook_url
        self.session = None
        
    async def initialize(self):
        """Initialize the aiohttp session"""
        # Configure TCP connector with keepalive and limits
        connector = aiohttp.TCPConnector(
            limit=10,  # Limit number of simultaneous connections
            ttl_dns_cache=300,  # Cache DNS results for 5 minutes
            keepalive_timeout=60,  # Keep connections alive for 60 seconds
            force_close=False,  # Don't force close connections
        )
        
        # Create session with the connector
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=1.0),  # Default timeout
            headers={"User-Agent": "BeosoundSniffer/1.0"}
        )
        print(f"Initialized webhook sender for {self.webhook_url}")
        
    async def send_webhook(self, message):
        """Send a message via webhook asynchronously with no retries"""
        if not self.session:
            await self.initialize()
            
        # Prepare webhook payload for Home Assistant
        webhook_data = {
            'device': 'beosound5c',
            'action': message.get('key_name', ''),
            'device_type': message.get('device_type', ''),
            'count': message.get('count', 1),
            'timestamp': datetime.now().isoformat()
        }
        
        try:
            # Send the webhook asynchronously
            async with self.session.post(
                self.webhook_url, 
                json=webhook_data, 
                timeout=aiohttp.ClientTimeout(total=0.3),  # Short timeout
                raise_for_status=False  # Don't raise exception to avoid blocking
            ) as response:
                if response.status < 300:
                    # Success
                    print(f"Webhook sent: {webhook_data['action']}")
                    return True
                else:
                    print(f"Webhook response error: {response.status}")
            
            return False
        except asyncio.TimeoutError:
            # Just log and continue
            print(f"Webhook timeout: {webhook_data['action']}")
            return False
        except Exception as e:
            print(f"Webhook error: {str(e)}")
            return False
            
    async def close(self):
        """Close the aiohttp session"""
        if self.session:
            await self.session.close()
            self.session = None 