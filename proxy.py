import json
import os
from urllib.parse import urlparse, urljoin
import io
import tiktoken

import typer
from typing import Optional
from typing_extensions import Annotated

import asyncio
from aiohttp import web, ClientSession

LLM_URL = os.environ.get('LLM_URL')
DIFFUSION_URL = os.environ.get('DIFFUSION_URL')

DIFF_TOKEN_LIMIT = int(os.environ.get('DIFF_TOKEN_LIMIT', 75))

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


async def handle_chat_completions(request, session):
    req_json = {}
    if request.body_exists:
        req_json = await request.json()

    stream = req_json.get('stream', False)
    if 'stream' in req_json:
        req_json['stream'] = False

    response = await proxy_request(request, session, json_body=req_json)

    response_stream = io.BytesIO(await response.content.read())

    res_json = json.loads(response_stream.read().decode('utf-8'))
    print("Response:")
    print(res_json)
    response_stream.seek(0)

    if stream:
        # generate test events async
        async def generate_events():
            ret_json = res_json
            ret_json['object'] = 'chat.completion.chunk'

            msg = ret_json["choices"][0]["message"]
            ret_json["choices"] = [{
                "index": 0,
                "delta": {
                    "role": msg["role"],
                    "content": msg["content"]
                },
                "finish_reason": None
            }]

            yield f"data: {json.dumps(ret_json)}\n\n"

            json_stop = ret_json
            json_stop["choices"] = [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop"
            }]

            yield f"data: {json.dumps(json_stop)}\n\n"

            yield f"data: [DONE]"

        resp = web.StreamResponse(status=response.status, headers={
            "Content-Type": "text/event-stream",
        })
        await resp.prepare(request=request)
        async for e in generate_events():
            await resp.write(e.encode('utf-8'))
    else:
        resp = web.Response(body=response_stream, status=response.status, content_type='application/json')

    return resp


async def handle_image_generations(request, session):
    req_json = {}
    if request.body_exists:
        req_json = await request.json()

    req_json["n"] = None

    prompt = req_json.get("prompt", "")
    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(prompt)
    if len(tokens) > DIFF_TOKEN_LIMIT:
        prompt = enc.decode(tokens[:DIFF_TOKEN_LIMIT])
        req_json["prompt"] = prompt

    response = await proxy_request(request, session, target='diff', json_body=req_json)

    response_stream = io.BytesIO(await response.content.read())
    res_json = json.loads(response_stream.read().decode('utf-8'))

    response_stream.seek(0)

    if response.status != 200:
        print(f"Response (code: {response.status}):")
        print(res_json)

    return web.Response(body=response_stream, status=response.status, content_type=response.content_type)


async def handle_default(request, session):
    response = await proxy_request(request, session)

    return web.Response(body=response.content, status=response.status, content_type=response.content_type)


async def proxy_request(request, session, json_body=None, target='llm'):
    path = request.url.path.rstrip('/')
    target_url = request.app['llm_url'] if target == 'llm' else request.app['diff_url']
    target_path = urljoin(target_url.path, path.lstrip('/'))

    req_json = {}
    if request.body_exists:
        req_json = await request.json()

    print("Request JSON:")
    print(req_json)

    if json_body:
        print("Modified Request JSON:")
        print(json_body)

    # Send the original request to the target server with all headers
    headers = {
        'User-Agent': 'openai-proxy',
        'Accept': '*/*',
        'Authorization': 'Bearer ' + target_url.username,
        'Content-Type': 'application/json',
    }

    u = target_url.scheme + '://' + target_url.hostname + target_path
    return await session.request(request.method, str(u), headers=headers,
                                 json=json_body if json_body else req_json)


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
            elif path == '/chat/completions':
                return await handle_chat_completions(request, session)
            elif path == '/images/generations':
                return await handle_image_generations(request, session)

            return await handle_default(request, session)


async def _relay_data(writer, reader):
    while True:
        line = await reader.readline()
        if not line:
            break
        writer.write(line)


def main(
        port: Annotated[Optional[int], typer.Option("--port", "-p", help="The Port of the server.")] = 8000
):
    llm_url = LLM_URL
    if not llm_url.endswith('/'):
        llm_url += '/'
    llm_url = urljoin(llm_url, 'v1/')
    parsed_llm_url = urlparse(llm_url)

    diff_url = DIFFUSION_URL
    if not diff_url.endswith('/'):
        diff_url += '/'
    diff_url = urljoin(diff_url, 'v1/')
    parsed_diff_url = urlparse(diff_url)

    app = web.Application()
    app['llm_url'] = parsed_llm_url
    app['diff_url'] = parsed_diff_url
    app.add_routes([web.route('*', '/{tail:.*}', handler)])

    web.run_app(app, host='0.0.0.0', port=port)
    print(f'Reverse proxy server running on port {port}...')


if __name__ == '__main__':
    typer.run(main)
