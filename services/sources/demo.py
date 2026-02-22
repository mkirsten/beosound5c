#!/usr/bin/env python3
"""
BeoSound 5c Demo Source (beo-demo)

Simple TTS demo source — plays spoken audio clips to demonstrate
the source framework. Always registers as 'available' on startup.

Port: 8775
"""

import asyncio
import os
import subprocess
import sys
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.source_base import SourceBase

try:
    import edge_tts
    HAS_EDGE_TTS = True
except ImportError:
    HAS_EDGE_TTS = False

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('beo-demo')

SOUNDS = [
    {
        "id": "welcome",
        "title": "Welcome",
        "subtitle": "A friendly greeting",
        "text": "Welcome to the BeoSound 5c. This is a recreation of the original Bang and Olufsen BeoSound 5, built with modern technology.",
    },
    {
        "id": "time",
        "title": "Current Time",
        "subtitle": "Speaks the current time",
        "text": None,  # generated dynamically
    },
    {
        "id": "about",
        "title": "Source Demo",
        "subtitle": "Explains the source system",
        "text": "This is a demo source, built using the source base class. Any developer can create a new source by subclassing source base and adding a view script. The system handles registration, menu items, and event routing automatically.",
    },
]


class DemoService(SourceBase):
    id = "demo"
    name = "Demo"
    port = 8775
    player = "local"
    action_map = {
        "play": "play",
        "go": "play",
        "left": "prev",
        "right": "next",
        "stop": "stop",
        "pause": "stop",
    }

    def __init__(self):
        super().__init__()
        self.selected = 0
        self.state = "available"  # available | playing
        self._mpv_process = None
        self._watcher_task = None

    async def on_start(self):
        await self.register("available")
        await self._broadcast_update()

    async def handle_status(self) -> dict:
        return {
            "source": self.id,
            "state": self.state,
            "selected": self.selected,
            "sounds": [{"id": s["id"], "title": s["title"], "subtitle": s["subtitle"]} for s in SOUNDS],
        }

    async def handle_resync(self) -> dict:
        await self.register(self.state)
        await self._broadcast_update()
        return {"status": "ok", "resynced": True}

    async def handle_command(self, cmd, data) -> dict:
        if cmd == "play":
            await self._play_selected()
        elif cmd == "stop":
            await self._stop()
        elif cmd == "next":
            self.selected = (self.selected + 1) % len(SOUNDS)
            await self._broadcast_update()
        elif cmd == "prev":
            self.selected = (self.selected - 1) % len(SOUNDS)
            await self._broadcast_update()
        elif cmd == "select":
            idx = data.get("index", 0)
            if 0 <= idx < len(SOUNDS):
                self.selected = idx
                await self._play_selected()
        else:
            return {"status": "error", "message": f"Unknown: {cmd}"}
        return {"state": self.state, "selected": self.selected}

    async def _play_selected(self):
        await self._stop()
        sound = SOUNDS[self.selected]

        # Build text (dynamic for "time")
        if sound["id"] == "time":
            now = datetime.now()
            text = f"The current time is {now.strftime('%I:%M %p')} on {now.strftime('%A, %B %d')}."
        else:
            text = sound["text"]

        self.state = "playing"
        await self.register("playing")
        await self._broadcast_update()

        tts_file = "/tmp/beo-demo-tts.mp3"
        env = os.environ.copy()
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")

        # Generate TTS audio
        generated = False
        if HAS_EDGE_TTS:
            try:
                communicate = edge_tts.Communicate(text, voice="en-US-AndrewNeural")
                await communicate.save(tts_file)
                generated = True
            except Exception as e:
                log.warning("edge-tts failed: %s", e)

        if not generated:
            # Fallback: espeak-ng
            try:
                proc = await asyncio.create_subprocess_exec(
                    "espeak-ng", "-v", "en-us", "-s", "160", "--stdout", text,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                )
                audio_data, _ = await proc.communicate()
                with open(tts_file, "wb") as f:
                    f.write(audio_data)
                generated = True
            except Exception as e:
                log.error("espeak-ng failed: %s", e)

        if not generated:
            log.error("No TTS engine available")
            self.state = "available"
            await self.register("available")
            await self._broadcast_update()
            return

        # Play via mpv
        try:
            self._mpv_process = subprocess.Popen(
                ["mpv", "--ao=pulse", "--no-video", "--no-terminal", tts_file],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
            )
            self._watcher_task = asyncio.create_task(self._watch_playback())
            log.info("Playing: %s", sound["title"])
        except Exception as e:
            log.error("mpv playback failed: %s", e)
            self.state = "available"
            await self.register("available")
            await self._broadcast_update()

    async def _watch_playback(self):
        """Wait for mpv to finish, then transition back to available."""
        try:
            while self._mpv_process and self._mpv_process.poll() is None:
                await asyncio.sleep(0.25)
            log.info("Playback finished")
        except asyncio.CancelledError:
            return
        self._mpv_process = None
        self.state = "available"
        await self.register("available")
        await self._broadcast_update()

    async def _stop(self):
        if self._watcher_task:
            self._watcher_task.cancel()
            self._watcher_task = None
        if self._mpv_process:
            self._mpv_process.terminate()
            try:
                self._mpv_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._mpv_process.kill()
            self._mpv_process = None
        if self.state == "playing":
            self.state = "available"
            await self.register("available")
            await self._broadcast_update()

    async def on_stop(self):
        await self._stop()

    async def _broadcast_update(self):
        await self.broadcast("demo_update", {
            "state": self.state,
            "selected": self.selected,
            "sounds": [{"id": s["id"], "title": s["title"], "subtitle": s["subtitle"]} for s in SOUNDS],
        })


if __name__ == "__main__":
    service = DemoService()
    asyncio.run(service.run())
