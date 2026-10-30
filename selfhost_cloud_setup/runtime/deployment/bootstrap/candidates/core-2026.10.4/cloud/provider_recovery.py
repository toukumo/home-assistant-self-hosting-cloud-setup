"""Request reset mail through HA's existing administrator session."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
import ssl
import stat

import aiohttp
from aiohttp import web
from aiohttp.web_protocol import RequestPayloadError
from homeassistant.components.http import HomeAssistantView, require_admin


@contextmanager
def _regular_file(value):
    fd = os.open(value, os.O_RDONLY | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('Invalid recovery configuration')
        yield fd
    finally:
        os.close(fd)


class PasswordRecoveryView(HomeAssistantView):
    url = '/api/selfhost_cloud/password/request'
    name = 'api:selfhost_cloud:password:request'

    @require_admin
    async def post(self, request):
        if request.content_length is None or request.content_length > 4096:
            raise web.HTTPBadRequest()
        framework_limit = request.client_max_size
        maximum = min(4096, framework_limit) if framework_limit else 4096

        def check_size(size):
            if framework_limit and size > framework_limit:
                raise web.HTTPRequestEntityTooLarge(max_size=framework_limit, actual_size=size)
            if size > 4096:
                raise web.HTTPBadRequest()

        try:
            if request.can_read_body:
                content = bytearray()
                while True:
                    chunk = await request.content.read(maximum + 1 - len(content))
                    if not chunk:
                        break
                    content.extend(chunk)
                    check_size(len(content))
            else:
                content = await request.read()
                check_size(len(content))
        except RequestPayloadError:
            error = web.HTTPBadRequest(text="Invalid password recovery body")
            error.force_close()
            # This parser has failed; skip draining only this closing connection.
            request.protocol._lingering_time = 0
            raise error from None
        try:
            data = json.loads(content.decode(request.charset or 'utf-8'))
        except (ValueError, LookupError):
            raise web.HTTPBadRequest() from None
        if (not isinstance(data, dict) or set(data) != {'email', 'language'}
                or not all(isinstance(v, str) for v in data.values())
                or len(data['email']) > 320):
            raise web.HTTPBadRequest()
        try:
            path = Path(__file__).with_name('selfhost.json')
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError('Invalid recovery configuration')
            with _regular_file(path) as fd:
                with os.fdopen(fd, "r", closefd=False) as stream:
                    settings = json.load(stream)
            if (not isinstance(settings, dict)
                    or not all(isinstance(settings.get(key), str) and settings[key]
                               for key in ('url', 'ca'))):
                raise ValueError('Invalid recovery configuration')
            base = settings['url']
            parsed = urlsplit(base)
            if (parsed.scheme != 'https' or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or '?' in base or '#' in base):
                raise ValueError('Invalid recovery HTTPS base')
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError('Invalid recovery HTTPS port')
            context = ssl.create_default_context()
            with _regular_file(settings['ca']) as fd:
                context.load_verify_locations(cafile=f'/dev/fd/{fd}')
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.post(settings['url'].rstrip('/') + '/auth/password/request',
                                        json=data, ssl=context, allow_redirects=False) as response:
                    status = response.status
                    # Plain-text 400/429 responses still carry a useful status.
                    result = await response.json() if status in (200, 503) else None
        except (aiohttp.ClientError, OSError, TimeoutError, ValueError, LookupError, RecursionError):
            return self.json({'error': 'mail_unavailable'}, status_code=503)
        if status == 200:
            return self.json_message('Request accepted.')
        code = result.get('error') if isinstance(result, dict) else None
        return self.json({'error': 'mail_not_configured' if code == 'mail_not_configured' else 'mail_unavailable'},
                         status_code=status if status in (400, 429, 503) else 503)
