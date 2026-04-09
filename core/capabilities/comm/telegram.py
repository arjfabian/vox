"""
Telegram Capability Module

Handles persistent bi-directional communication with the authorized user via Telegram.
Implements non-blocking long-polling, message normalization, and 
strict identity verification (White-listing).
"""

import httpx
import asyncio
from typing import Any, Optional, Dict
from core.capability import VOXCapability

class TelegramCapability(VOXCapability):
    """
    Standard Communication Interface.
    Acts as an asynchronous bridge between the VOX Event Router and the Telegram Bot API.
    """
    
    # Unified Parameter Contract
    PARAMS = {
        "TELEGRAM_USER_ID": ["Whitelisted Telegram User ID (Numeric)", None],
        "POLLING_TIMEOUT": ["Long Polling timeout in seconds", 20],
    }

    def __init__(self, agent: Any, **kwargs):
        """Initializes the gateway using secure configuration injection."""
        super().__init__(agent, **kwargs)
        
        # Accessing protected token directly from agent's secure config area
        self.token = agent.config.get("AGENT_TELEGRAM_TOKEN")
        self.user_id = str(self.params["TELEGRAM_USER_ID"])
        
        # Base API URL Construction
        self.api_url = f"https://api.telegram.org/bot{self.token}"
        
        # Polling State Management
        self._is_running = False
        self.last_update_id = 0
        
        self.agent.log_agent_info(f"Link established for Authorized User: {self.user_id}")

    async def boot(self):
        """
        Capability Ignition Sequence.
        Synchronizes message offsets and spawns the background listener.
        """
        if not self.token:
            self.agent.log_agent_fail("Gateway Blocked: Protected token missing.")
            return

        # Phase 1: Clean start (Synchronize Offset to avoid processing stale data)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{self.api_url}/getUpdates", params={"offset": -1})
                if r.status_code == 200:
                    results = r.json().get("result", [])
                    if results:
                        self.last_update_id = results[0]['update_id']
                        self.agent.log_agent_info(f"Gateway Synchronized at offset {self.last_update_id}")
        except Exception as e:
            self.agent.log_agent_warn(f"Gateway Sync Issue: {e}")

        # Phase 2: Spawn Background Worker
        self._is_running = True
        asyncio.create_task(self._poll_worker())
        self.agent.log_agent_ok("Communication link (Polling) online.")

    async def _poll_worker(self):
        """
        Resilient Long-Polling Loop.
        Monitors incoming updates and handles network-level exceptions gracefully.
        """
        while self._is_running:
            try:
                p_timeout = int(self.params["POLLING_TIMEOUT"])
                params = {
                    "offset": self.last_update_id + 1, 
                    "timeout": p_timeout
                }
                
                # Timeout added to client to be slightly higher than polling timeout
                async with httpx.AsyncClient(timeout=p_timeout + 5) as client:
                    response = await client.get(f"{self.api_url}/getUpdates", params=params)
                    
                    if response.status_code == 200:
                        updates = response.json().get("result", [])
                        for update in updates:
                            self.last_update_id = update['update_id']
                            await self._process_update(update)
                    
                    elif response.status_code == 409:
                        self.agent.log_agent_fail("Conflict Alert: Multiple bot instances detected.")
                        await asyncio.sleep(15) # Back off on conflict
                        
            except httpx.RequestError:
                # Silent retry on network blips
                await asyncio.sleep(5)
            except Exception as e:
                self.agent.log_agent_fail(f"Gateway Worker Error: {e}")
                await asyncio.sleep(5) 
            
            # Cooperative multitasking yield
            await asyncio.sleep(0.1)

    async def _process_update(self, raw_data: Dict[str, Any]):
        """
        Normalization Layer.
        Transforms raw Telegram data into internal VOX events.
        """
        message = raw_data.get("message", {})
        if not message: return

        text = message.get("text")
        sender = str(message.get("from", {}).get("id"))

        # Zero-Trust Protocol: Strict sender verification
        if text and sender == self.user_id:
            self.agent.log_agent_info(f"Signal verified: Inbound message.")
            
            # Emit to Agent's internal synapsis
            await self.agent.emit(
                "inbound_message", 
                content=text, 
                origin="comm.telegram", 
                sender_id=sender
            )
        elif text:
            self.agent.log_agent_warn(f"Security Alert: Unauthorized message from ID {sender}.")

    async def send(self, content: str):
        """
        Outbound Delivery Protocol.
        Sends an encrypted/plain message to the whitelisted user.
        """
        url = f"{self.api_url}/sendMessage"
        payload = {
            "chat_id": self.user_id, 
            "text": content,
            "parse_mode": "Markdown" # Allows for professional technical formatting
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
        except Exception as e:
            self.agent.log_agent_fail(f"Delivery Failure: {e}")