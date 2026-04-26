"""
Telegram Capability Module

Handles persistent bi-directional communication with the authorized user via Telegram.
Implements non-blocking long-polling, message normalization, and 
strict identity verification (White-listing) under a Zero-Trust paradigm.
"""

import asyncio
import httpx
# from typing import Any, Optional, Dict
from core.capability import VOXCapability

class Capability(VOXCapability):
    """
    Standard Communication Interface.
    Acts as an asynchronous bridge between the VOX Event Router and the Telegram Bot API.
    """
    
    # Configuration Blueprint (Injected from Agent environment)
    PARAMS = {
        "TELEGRAM_USER_ID":      ["Whitelisted Telegram User ID (Numeric)", None],
        "TELEGRAM_LONG_TIMEOUT": ["Long Polling timeout in seconds",        20]
    }

    async def health_check(self) -> bool:
        # Token validation happens at boot() — no token available here.
        return True

    async def boot(self):
        token = self._agent.config.get("AGENT_TELEGRAM_TOKEN")
        if not token:
            self._agent.log_agent_fail("Gateway Blocked: Bot token missing.")
            return

        self.api_url      = f"https://api.telegram.org/bot{token}"
        self.user_id      = str(self.TELEGRAM_USER_ID)
        self.client       = httpx.AsyncClient(timeout=float(self.TELEGRAM_LONG_TIMEOUT) + 5)
        self.last_update_id = 0
        self._is_running  = True

        asyncio.create_task(self._poll_worker())
        self._agent.log_agent_ok("Communication link established. Polling active.")
 
    async def shutdown(self):
        self._is_running = False
        if hasattr(self, "client"):
            await self.client.aclose()
        self._agent.log_agent_info("Communication link terminated.")

    # -------------------------------------------------------------------------
    # Polling
    # -------------------------------------------------------------------------
 
    async def _poll_worker(self):
        """
        Resilient Long-Polling Loop.
        Monitors incoming updates and handles network-level exceptions gracefully.
        """
        while self._is_running:
            try:
                p_timeout = int(self.TELEGRAM_LONG_TIMEOUT)
                params = {
                    "offset": self.last_update_id + 1, 
                    "timeout": p_timeout
                }
                
                # We use the persistent client to avoid TCP handshake overhead
                response = await self.client.get(f"{self.api_url}/getUpdates", params=params)
                
                if response.status_code == 200:
                    updates = response.json().get("result", [])
                    for update in updates:
                        self.last_update_id = update["update_id"]
                        await self._process_update(update)
                
                elif response.status_code == 409:
                    self.agent.log_agent_fail("Conflict Alert: Multiple bot instances detected.")
                    await asyncio.sleep(15) 
                        
            except (httpx.RequestError, httpx.TimeoutException):
                # Silent recovery for transient network instability
                await asyncio.sleep(2)
            except Exception as e:
                self.agent.log_agent_fail(f"Gateway Worker Error: {e}")
                await asyncio.sleep(5) 
            
            await asyncio.sleep(0.1)

    async def _process_update(self, raw_data: dict):
        message = raw_data.get("message", {})
        if not message:
            return

        sender = str(message.get("from", {}).get("id"))

        # ------------------------------------------------------------------
        # Text message
        # ------------------------------------------------------------------
        text = message.get("text")
        if text:
            if sender == self.user_id:
                self._agent.log_agent_info("Signal verified: Inbound text message.")
                await self._agent.emit(
                    "inbound_message",
                    content=text,
                    origin="comm.telegram",
                    sender_id=sender,
                )
            else:
                self._agent.log_agent_warn(
                    f"Security Alert: Unauthorized text from ID {sender}."
                )
            return

        # ------------------------------------------------------------------
        # Voice message (Telegram voice note — always OGG/Opus)
        # ------------------------------------------------------------------
        voice = message.get("voice")
        if voice:
            if sender != self.user_id:
                self._agent.log_agent_warn(
                    f"Security Alert: Unauthorized voice from ID {sender}."
                )
                return

            audio_bytes = await self._download_file(voice["file_id"])
            if not audio_bytes:
                self._agent.log_agent_fail("Voice download failed.")
                return

            # Speaker verification via Orchestrator profile
            profile = getattr(
                getattr(self._agent, "orchestrator", None),
                "speaker_profile",
                None
            )

            if profile and profile.is_enrolled:
                is_master = profile.verify(audio_bytes)
            else:
                # No profile loaded — assume authorized (degraded mode)
                is_master = True
                self._agent.log_agent_warn(
                    "Speaker profile not loaded. Voice treated as authorized."
                )

            if is_master:
                self._agent.log_agent_info(
                    "Speaker verified: Voice command authorized."
                )
                await self._agent.emit(
                    "inbound_voice",
                    audio=audio_bytes,
                    origin="comm.telegram",
                    sender_id=sender,
                )
            else:
                # Unknown speaker — discard.
                # Future: if agent has an active "expect_audio" context,
                # route to inbound_audio for storage instead.
                self._agent.log_agent_warn(
                    "Speaker mismatch: Voice not recognized as operator. Discarding."
                )
            return

        # ------------------------------------------------------------------
        # Generic audio file (music, recordings, etc.)
        # Discarded for now.
        # Future: route to inbound_audio if agent has an active storage context.
        # ------------------------------------------------------------------
        audio = message.get("audio")
        if audio:
            if sender != self.user_id:
                self._agent.log_agent_warn(
                    f"Security Alert: Unauthorized audio from ID {sender}."
                )
                return
            self._agent.log_agent_info(
                "Generic audio file received. No active storage context — discarding."
            )

    async def _download_file(self, file_id: str) -> bytes:
        """
        Resolves a Telegram file_id to raw bytes.
        Two-step: getFile → download from CDN path.
        """
        try:
            response = await self.client.get(
                f"{self.api_url}/getFile",
                params={"file_id": file_id}
            )
            response.raise_for_status()
            file_path = response.json()["result"]["file_path"]

            token = self._agent.config.get("AGENT_TELEGRAM_TOKEN")
            cdn_url = f"https://api.telegram.org/file/bot{token}/{file_path}"

            download = await self.client.get(cdn_url)
            download.raise_for_status()
            return download.content

        except Exception as e:
            self._agent.log_agent_fail(f"File download error: {e}")
            return b""

    # -------------------------------------------------------------------------
    # Outbound
    # -------------------------------------------------------------------------

    async def send(self, content: str):
        """
        Outbound Delivery Protocol.
        Transmits a formatted payload to the whitelisted administrative endpoint.
        """
        # url = f"{self.api_url}/sendMessage"
        payload = {
            "chat_id":    self.user_id, 
            "text":       content,
            "parse_mode": "Markdown" 
        }
        
        try:
            response = await self.client.post(
                f"{self.api_url}/sendMessage", json=payload
            )
            response.raise_for_status()
        except Exception as e:
            self.agent.log_agent_fail(f"Delivery Failure: {e}")

    async def send_typing(self):
        """
        UX Presence Signal.
        Informs the administrative endpoint that the LLM is currently generating a payload.
        """
        # url = f"{self.api_url}/sendChatAction"
        payload = {
            "chat_id": self.user_id,
            "action":  "typing"
        }
        try:
            # Persistent client ensures minimal latency for the typing pulse
            await self.client.post(
                f"{self.api_url}/sendChatAction", json=payload
            )
        except Exception as e:
            self.agent.log_agent_warn(f"Typing Signal Failure: {e}")



    # def __init__(self):
    #     self.active_sessions = {}

    # def __init__(self, agent: Any, **kwargs):
    #     """Initializes the gateway using secure configuration injection."""
    #     super().__init__(agent, **kwargs)
        
    #     # Accessing protected token directly from agent's secure config area
    #     self.token = agent.config.get("AGENT_TELEGRAM_TOKEN")
    #     self.user_id = str(self.params["TELEGRAM_USER_ID"])
        
    #     # Base API URL Construction
    #     self.api_url = f"https://api.telegram.org/bot{self.token}"
        
    #     # Persistent HTTPX Session for connection pooling and performance
    #     self.client = httpx.AsyncClient(timeout=30.0)
        
    #     # Polling State Management
    #     self._is_running = False
    #     self.last_update_id = 0
        
    #     self.agent.log_agent_info(f"Link established for Authorized User: {self.user_id}")

    #     # Phase 1: Gateway Synchronization
    #     # Prevents processing of stale data by fast-forwarding the update offset.
    #     try:
    #         r = await self.client.get(f"{self.api_url}/getUpdates", params={"offset": -1})
    #         if r.status_code == 200:
    #             results = r.json().get("result", [])
    #             if results:
    #                 self.last_update_id = results[0]['update_id']
    #                 self.agent.log_agent_info(f"Gateway Synchronized at offset {self.last_update_id}")
    #     except Exception as e:
    #         self.agent.log_agent_warn(f"Gateway Sync Issue: {e}")

    #     # Phase 2: Spawn Background Listener
    #     self._is_running = True
    #     asyncio.create_task(self._poll_worker())
    #     self.agent.log_agent_ok("Communication link (Polling) online.")

    # async def send_message(self, text, **kwargs):
    #     # Agent-specific parameters, like chat_id, will be included in kwargs
    #     # thanks to BoundCapability.
    #     user_id = kwargs.get("TELEGRAM_USER_ID")
    #     print(f"Sending to {user_id}: {text}")

    # async def shutdown(self):
    #     """
    #     Clean Exit Protocol.
    #     Closes the persistent HTTPX session and releases system resources.
    #     """
    #     self._is_running = False
    #     await self.client.aclose()
    #     self.agent.log_agent_info("Communication link terminated.")