import os

import asyncio
from aiohttp import web, ClientSession

from urllib.parse import urlparse, urljoin

TARGET_SERVER = os.environ.get('LLM_URL')
PORT = os.environ.get('PORT', 8000)

models = {
    "object": "list",
    "data": [
        {
            "id": "mixtral-8x7b",
            "object": "model",
            "created": 0,
            "owned_by": "openai"
        },
        {
            "id": "sdxl",
            "object": "model",
            "created": 0,
            "owned_by": "openai"
        },
    ],
}


async def handler(request):
    method = request.method
    url = request.url
    path = request.url.path.rstrip('/')

    print(url)
    print(request.content_type)

    if method == "CONNECT":
        # Handle HTTPS CONNECT requests
        host = url.host
        port = url.port
        reader, writer = await asyncio.open_connection(host, port, ssl=False)
        writer.write(b'HTTP/1.1 200 Connection established\r\n\r\n')
        await asyncio.gather(
            _relay_data(writer, reader),
            _relay_data(reader, writer),
        )
    else:
        # Handle regular HTTP requests
        async with ClientSession() as session:
            if path == '/models':
                return web.json_response(status=200, data=models)
            elif path == '/models/mixtral-8x7b':
                return web.json_response(status=200, data=models[0])
            elif path == '/models/sdxl':
                return web.json_response(status=200, data=models[1])

            target_url = parsed_url
            target_path = urljoin(target_url.path, path.lstrip('/'))

            if request.body_exists and request.can_read_body:
                json_body = await request.json()

            # Send the original request to the target server with all headers
            headers = {
                'User-Agent': 'openai-proxy',
                'Accept': '*/*',
                'Authorization': 'Bearer ' + target_url.username,
                'Content-Type': 'application/json',
            }

            print(json_body)
            stream = json_body.get('stream', False)
            json_body['stream'] = False

            u = target_url.scheme + '://' + target_url.hostname + target_path

            response = await session.request(method, str(u), headers=headers, json=json_body)

            print(await response.text())

            return web.Response(body=response.content, status=response.status, content_type=request.content_type)


async def _relay_data(writer, reader):
    while True:
        line = await reader.readline()
        if not line:
            break
        writer.write(line)


if __name__ == '__main__':
    server_url = TARGET_SERVER
    if not server_url.endswith('/'):
        server_url += '/'
    server_url = urljoin(server_url, 'v1/')
    parsed_url = urlparse(server_url)

    app = web.Application()
    app.add_routes([web.route('*', '/{tail:.*}', handler)])

    web.run_app(app, host='0.0.0.0', port=PORT)
    print(f'Reverse proxy server running on port {PORT}...')
