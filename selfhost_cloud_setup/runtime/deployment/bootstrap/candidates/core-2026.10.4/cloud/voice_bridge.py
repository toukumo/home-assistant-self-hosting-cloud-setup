"""Translate the native Cloud voice HTTP contract into real Wyoming requests."""
import asyncio
import io
import os
import wave
import base64
import re
from xml.etree import ElementTree as ET

from aiohttp import web
from aiohttp.web_protocol import RequestPayloadError
from wyoming.client import AsyncClient
from wyoming.event import async_read_event
from wyoming.audio import AudioStart, AudioChunk, AudioStop
from wyoming.asr import Transcribe, Transcript
from wyoming.error import Error
from wyoming.tts import Synthesize

STT_URI = os.environ.get("CLOUD_STT_URI")
TTS_URI = os.environ.get("CLOUD_TTS_URI")
TTS_JA_URI = os.environ.get("CLOUD_TTS_JA_URI", TTS_URI)
RELAY_PROVIDER = None
LANGUAGES = {"ko-KR": "ko", "ja-JP": "ja"}
MAX_AUDIO = 8 * 1024 ** 2


def relay_response(result):
    if not isinstance(result, dict):
        raise web.HTTPBadGateway(text="Invalid voice executor response")
    body, content_type = result.get("body"), result.get("content_type")
    token = r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
    if (not isinstance(body, str) or not isinstance(content_type, str)
            or re.fullmatch(token + "/" + token, content_type) is None):
        raise web.HTTPBadGateway(text="Invalid voice executor response")
    if len(body) > 4 * ((MAX_AUDIO + 2) // 3):
        raise web.HTTPBadGateway(text="Voice executor response exceeds limit")
    try:
        decoded = base64.b64decode(body, validate=True)
    except ValueError:
        raise web.HTTPBadGateway(text="Invalid voice executor response") from None
    if len(decoded) > MAX_AUDIO:
        raise web.HTTPBadGateway(text="Voice executor response exceeds limit")
    return web.Response(body=decoded, content_type=content_type)


class VoiceFrameReader:
    """Apply the existing audio budget before Wyoming allocates frame bodies."""
    def __init__(self, reader):
        self.reader = reader

    async def readline(self):
        return await self.reader.readline()

    async def readexactly(self, size):
        if type(size) is not int or size < 0 or size > MAX_AUDIO:
            raise ValueError('Wyoming frame exceeds audio budget')
        return await self.reader.readexactly(size)


async def voice_event(client):
    try:
        # Wyoming 1.10 exposes no read-size option. All supported AsyncClient
        # transports own this reader; retain its parser with a bounded adapter.
        event = await async_read_event(VoiceFrameReader(client._reader))
        if event is not None and (not isinstance(event.type, str) or not isinstance(event.data, dict)):
            raise ValueError()
        return event
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError, asyncio.IncompleteReadError):
        raise web.HTTPBadGateway(text="Invalid Wyoming response") from None


def audio_format(audio):
    values = (audio.rate, audio.width, audio.channels)
    if any(type(value) is not int for value in values):
        raise web.HTTPBadGateway(text="Invalid TTS audio format")
    return values


async def convert(content, args):
    try:
        proc = await asyncio.create_subprocess_exec("ffmpeg", "-hide_banner", "-loglevel", "error",
                                                   *args, stdin=asyncio.subprocess.PIPE,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except OSError:
        raise web.HTTPServiceUnavailable(text="Audio converter unavailable") from None
    output = bytearray()
    actual_size = 0
    overflow = False
    workers = []

    def stop_converter():
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    async def feed_input():
        try:
            proc.stdin.write(content)
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            proc.stdin.close()

    async def discard(reader):
        while await reader.read(65536):
            pass

    async def collect_output():
        nonlocal actual_size, overflow
        while chunk := await proc.stdout.read(65536):
            actual_size += len(chunk)
            if actual_size > MAX_AUDIO:
                if not overflow:
                    overflow = True
                    stop_converter()
            else:
                output.extend(chunk)

    try:
        async with asyncio.timeout(15):
            workers = [asyncio.create_task(feed_input()), asyncio.create_task(collect_output()),
                       asyncio.create_task(discard(proc.stderr))]
            await asyncio.gather(*workers)
            await proc.wait()
        if overflow:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_AUDIO, actual_size=actual_size)
        if proc.returncode or not output:
            raise web.HTTPBadRequest(text="Unsupported or invalid audio")
        return bytes(output)
    except TimeoutError:
        raise web.HTTPGatewayTimeout(text="Audio conversion timed out") from None
    finally:
        stop_converter()
        proc.stdin.close()
        for worker in workers:
            worker.cancel()
        async def finish_cleanup():
            await asyncio.gather(*workers, return_exceptions=True)
            # Resume paused pipes before waiting for process exit. Never retain
            # diagnostic output, including when the caller cancels again.
            await asyncio.gather(discard(proc.stdout), discard(proc.stderr), return_exceptions=True)
            try:
                await proc.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
            await proc.wait()

        cleanup = asyncio.create_task(finish_cleanup())
        cancellation = None
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError as error:
                cancellation = error
        cleanup.result()
        if cancellation is not None:
            raise cancellation


async def voice_body(request, limit, *, status=413):
    """Bound decoded HTTP input; retain the native read-only request interface."""
    http_request = isinstance(request, web.Request)
    framework_limit = request.client_max_size if http_request else 0

    def check_size(size):
        if framework_limit and size > framework_limit:
            raise web.HTTPRequestEntityTooLarge(max_size=framework_limit, actual_size=size)
        if size > limit:
            if status == 400:
                raise web.HTTPBadRequest()
            raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=size)

    try:
        check_size(0)
        if not http_request or not request.can_read_body:
            content = await request.read()  # Native body or already-cached HTTP body.
            check_size(len(content))
            return content
        maximum = min(limit, framework_limit) if framework_limit else limit
        content = bytearray()
        while True:
            chunk = await request.content.read(min(65536, maximum + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            check_size(len(content))
        return bytes(content)
    except RequestPayloadError:
        error = web.HTTPBadRequest(text="Invalid voice body")
        error.force_close()
        # This parser has failed; skip draining only this closing connection.
        request.protocol._lingering_time = 0
        raise error from None


class VoiceXMLBuilder(ET.TreeBuilder):
    def doctype(self, name, public_id, system_id):
        raise ValueError('DOCTYPE is unsupported in voice input')


async def tts(request):
    if RELAY_PROVIDER is None and not TTS_URI:
        raise web.HTTPNotImplemented()
    document = await voice_body(request, 16384, status=400)
    if len(document) > 16384 or b"<!" in document:
        raise web.HTTPBadRequest()
    try:
        root = ET.fromstring(document, parser=ET.XMLParser(target=VoiceXMLBuilder()))
    except (ET.ParseError, LookupError, ValueError):
        raise web.HTTPBadRequest() from None
    language = root.attrib.get("{http://www.w3.org/XML/1998/namespace}lang")
    if language not in LANGUAGES:
        raise web.HTTPBadRequest(text="Only Korean and Japanese are configured")
    text = "".join(root.itertext())
    if not text.strip() or len(text) > 2000:
        raise web.HTTPBadRequest()
    output = request.headers.get("X-Microsoft-OutputFormat")
    if output not in ("raw-16khz-16bit-mono-pcm", "riff-24khz-16bit-mono-pcm",
                      "audio-24khz-48kbitrate-mono-mp3"):
        raise web.HTTPBadRequest(text="Unsupported output format")
    if RELAY_PROVIDER is not None:
        result = await RELAY_PROVIDER({"operation": "tts", "body": base64.b64encode(document).decode(),
                                       "output": output})
        return relay_response(result)
    chunks = []
    rate = width = channels = None
    total = 0
    try:
        async with asyncio.timeout(45):
            uri = TTS_JA_URI if language == "ja-JP" else TTS_URI
            async with AsyncClient.from_uri(uri) as client:
                await client.write_event(Synthesize(text=text).event())
                while True:
                    event = await voice_event(client)
                    if event is None or Error.is_type(event.type):
                        raise web.HTTPBadGateway(text="Wyoming TTS failed")
                    if AudioStart.is_type(event.type):
                        audio = AudioStart.from_event(event)
                        current = audio_format(audio)
                        if total and current != (rate, width, channels):
                            raise web.HTTPBadGateway(text="Inconsistent TTS audio format")
                        rate, width, channels = current
                    elif AudioChunk.is_type(event.type):
                        chunk = AudioChunk.from_event(event)
                        current = audio_format(chunk)
                        if (current != (rate, width, channels) or current[1:] != (2, 1)
                                or not 8000 <= current[0] <= 48000):
                            raise web.HTTPBadGateway(text="Inconsistent TTS audio format")
                        total += len(chunk.audio)
                        if total > MAX_AUDIO:
                            raise web.HTTPBadGateway(text="TTS output too large")
                        if chunk.audio:
                            chunks.append(chunk.audio)
                    elif AudioStop.is_type(event.type):
                        break
    except (OSError, TimeoutError):
        raise web.HTTPBadGateway(text="Wyoming TTS unavailable") from None
    except (KeyError, TypeError, ValueError):
        raise web.HTTPBadGateway(text="Invalid Wyoming TTS response") from None
    # Chunk boundaries may split samples; only the completed PCM must align.
    if not total or total % (width * channels):
        raise web.HTTPBadGateway(text="Invalid TTS audio format")
    pcm = b"".join(chunks)
    common = ["-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0", "-ar"]
    if output == "raw-16khz-16bit-mono-pcm":
        data = await convert(pcm, common + ["16000", "-f", "s16le", "pipe:1"])
        return web.Response(body=data, content_type="application/octet-stream")
    if output == "riff-24khz-16bit-mono-pcm":
        data = await convert(pcm, common + ["24000", "-f", "s16le", "pipe:1"])
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(data)
        return web.Response(body=buffer.getvalue(), content_type="audio/wav")
    if output == "audio-24khz-48kbitrate-mono-mp3":
        data = await convert(pcm, common + ["24000", "-codec:a", "libmp3lame", "-b:a", "48k", "-f", "mp3", "pipe:1"])
        return web.Response(body=data, content_type="audio/mpeg")
    raise web.HTTPBadRequest(text="Unsupported output format")


async def stt(request):
    if RELAY_PROVIDER is None and not STT_URI:
        raise web.HTTPNotImplemented()
    language = LANGUAGES.get(request.query.get("language"))
    if not language:
        raise web.HTTPBadRequest(text="Only Korean and Japanese are configured")
    content = await voice_body(request, MAX_AUDIO)
    if len(content) > MAX_AUDIO:
        raise web.HTTPRequestEntityTooLarge(max_size=MAX_AUDIO, actual_size=len(content))
    container_audio = content.startswith((b"RIFF", b"OggS"))
    # Native HA Assist labels its raw s16le stream as WAV/PCM. Only that exact
    # negotiated 16-kHz mono contract may omit a container header.
    native_pcm = not container_audio and bool(re.fullmatch(
        r"audio/wav;\s*codecs=audio/pcm;\s*samplerate=16000",
        (request.headers.get("Content-Type") or "").strip(), re.IGNORECASE))
    if not container_audio and (not native_pcm or not content or len(content) % 2):
        raise web.HTTPBadRequest(text="A WAV, OGG or negotiated native PCM stream is required")
    if RELAY_PROVIDER is not None:
        payload = {"operation": "stt", "body": base64.b64encode(content).decode(),
                   "language": request.query.get("language")}
        if native_pcm:
            payload["content_type"] = "audio/wav; codecs=audio/pcm; samplerate=16000"
        result = await RELAY_PROVIDER(payload)
        return relay_response(result)
    input_format = ["-f", "s16le", "-ar", "16000", "-ac", "1"] if native_pcm else []
    pcm = await convert(content, input_format + ["-i", "pipe:0", "-t", "60", "-ar", "16000", "-ac", "1", "-f", "s16le", "pipe:1"])
    try:
        async with asyncio.timeout(120):
            async with AsyncClient.from_uri(STT_URI) as client:
                await client.write_event(Transcribe(language=language).event())
                await client.write_event(AudioStart(rate=16000, width=2, channels=1).event())
                for i in range(0, len(pcm), 3200):
                    await client.write_event(AudioChunk(rate=16000, width=2, channels=1, audio=pcm[i:i+3200]).event())
                await client.write_event(AudioStop().event())
                while True:
                    event = await voice_event(client)
                    if event is None or Error.is_type(event.type):
                        raise web.HTTPBadGateway(text="Wyoming STT failed")
                    if Transcript.is_type(event.type):
                        text = Transcript.from_event(event).text
                        if not isinstance(text, str):
                            raise web.HTTPBadGateway(text="Invalid Wyoming STT response")
                        return web.json_response({"RecognitionStatus": "Success" if text.strip() else "NoMatch", "DisplayText": text})
    except (OSError, TimeoutError):
        raise web.HTTPBadGateway(text="Wyoming STT unavailable") from None
    except (KeyError, TypeError, ValueError):
        raise web.HTTPBadGateway(text="Invalid Wyoming STT response") from None
