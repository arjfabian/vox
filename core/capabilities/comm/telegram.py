import httpx
import asyncio
from core.logger import log_info

class TelegramCapability:
    def __init__(self, agent, **kwargs):
        self.agent = agent
        self.token = agent.config.get("telegram_token")
        self.user_id = str(kwargs.get("user_id"))
        self.api_url = f"https://api.telegram.org/bot{self.token}"
        self._is_running = False
        self.last_id = 0                 # Offset persistence during the session

    async def boot(self):
        """Prepares the driver and clears old messages."""
        if not self.token:
            self.agent.log_agent_fail("Telegram token missing in the manifest.")
            return

        # ---  BOOT-TIME CLEANUP (Anti-loop mechanism)  ------------------------
        # Do a quick query to get the ID of the last sent message while VOX was
        # offline, so they are ignored.
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(
                    f"{self.api_url}/getUpdates",
                    params={"offset": -1})
                if r.status_code == 200:
                    results = r.json().get("result", [])
                    if results:
                        # We mark everything up to the last message as "Read"
                        self.last_id = results[0]['update_id']
        except Exception as e:
            self.agent.log_agent_warn(f"Could not sync initial offset: {e}")

        self._is_running = True
        # Launch the polling by passing the security ID
        asyncio.create_task(self._poll())
        self.agent.log_agent_ok("Communication link (Polling) established.")

    async def _poll(self):
        """Polling loop with strict Offset handling."""
        while self._is_running:
            try:
                # Use last_id to tell Telegram to show only "new" messages.
                params = {"offset": self.last_id + 1, "timeout": 20}
                async with httpx.AsyncClient(timeout=30.0) as client:
                    r = await client.get(f"{self.api_url}/getUpdates", params=params)
                    
                    if r.status_code == 200:
                        updates = r.json().get("result", [])
                        for up in updates:
                            self.last_id = up['update_id'] # Update cursor
                            await self._normalize(up)
                    
                    elif r.status_code == 409: # Conflict (another instance running?)
                        self.agent.log_agent_fail("Polling conflict: more than one instance of VOX running?")
                        await asyncio.sleep(10)
                        
            except Exception as e:
                # Si hay error de red, no reseteamos last_id, solo esperamos
                await asyncio.sleep(5) 
            
            await asyncio.sleep(0.1)

    async def _normalize(self, raw_data):
        msg = raw_data.get("message", {})
        if not msg: return

        text = msg.get("text")
        sender = str(msg.get("from", {}).get("id"))

        if text and sender == self.user_id:
            log_info(f"Inbound message: '{text}'")
            await self.agent.emit(
                "inbound_message", 
                content=text, 
                origin="comm.telegram", 
                sender_id=sender
            )

    async def send(self, content):
        url = f"{self.api_url}/sendMessage"
        try:
            async with httpx.AsyncClient() as client:
                await client.post(url, json={"chat_id": self.user_id, "text": content})
        except Exception as e:
            self.agent.log_agent_fail(f"Error sending message: {e}")