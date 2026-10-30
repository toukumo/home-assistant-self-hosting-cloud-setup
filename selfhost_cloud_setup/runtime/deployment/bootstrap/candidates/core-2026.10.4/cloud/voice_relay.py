"""Private voice execution on HA; only operator-configured Wyoming targets."""
import base64
from aiohttp import web
from hass_nabucasa.iot import HANDLERS
from . import voice_bridge


class Request:
    def __init__(self, payload):
        encoded = payload.get("body")
        if not isinstance(encoded, str) or len(encoded) > (voice_bridge.MAX_AUDIO * 4 // 3 + 8):
            raise ValueError("Voice request exceeds limit")
        self.body = base64.b64decode(encoded, validate=True)
        self.headers = {"X-Microsoft-OutputFormat": payload.get("output")}
        if "content_type" in payload:
            content_type = payload["content_type"]
            if not isinstance(content_type, str) or len(content_type) > 256:
                raise ValueError("Invalid voice input content type")
            self.headers["Content-Type"] = content_type
        self.query = {"language": payload.get("language")}

    async def read(self):
        return self.body


def install(config):
    voice_bridge.STT_URI = config["stt"]
    voice_bridge.TTS_URI = config["tts"]
    voice_bridge.TTS_JA_URI = config.get("tts_ja", config["tts"])
    voice_bridge.RELAY_PROVIDER = None

    @HANDLERS.register("selfhost_voice")
    async def handle(cloud, payload):
        request = Request(payload)
        operation = payload.get("operation")
        if operation not in ("tts", "stt"):
            raise ValueError("Unsupported voice operation")
        response = await (voice_bridge.tts(request) if operation == "tts" else voice_bridge.stt(request))
        if not isinstance(response, web.Response) or response.status != 200:
            raise ValueError("Voice execution failed")
        return {"body": base64.b64encode(response.body).decode(), "content_type": response.content_type}
