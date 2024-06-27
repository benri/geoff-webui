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
LLAMA_URL = os.environ.get('LLAMA_URL')
DIFFUSION_URL = os.environ.get('DIFFUSION_URL')
EMBEDDING_URL = os.environ.get('EMBEDDING_URL')

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
        {
            "id": "llama3-8b",
            "object": "model",
            "created": 0,
            "owned_by": "openai"
        },
        {
            "id": "gritlm-7b",
            "object": "model",
            "created": 0,
            "owned_by": "openai"
        }
    ],
}


async def handle_chat_completions(request, session):
    req_json = {}
    if request.body_exists:
        req_json = await request.json()

    stream = req_json.get('stream', False)
    if 'stream' in req_json:
        req_json['stream'] = False

    model = req_json.get('model', '')

    response = await proxy_request(request, session, target=model, json_body=req_json)

    response_stream = await inspect_response(response)

    if stream and response.status == 200:
        res_json = json.loads(response_stream.read().decode('utf-8'))
        print("Response:")
        print(res_json)
        response_stream.seek(0)

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

    response = await proxy_request(request, session, target=req_json.get("model"), json_body=req_json)

    response_stream = await inspect_response(response)
    return web.Response(body=response_stream, status=response.status, content_type=response.content_type)


async def handle_embeddings(request, session):
    req_json = {}
    if request.body_exists:
        req_json = await request.json()

    i = req_json.get('input', '')
    if isinstance(i, list) and len(i) > 0:
        req_json['input'] = i[0]

    response = await proxy_request(request, session, target=req_json.get("model"), json_body=req_json)
    response_stream = await inspect_response(response)

    if response.status == 200:
        res_json = json.loads(response_stream.read().decode('utf-8'))
        response_stream.seek(0)

        data = {
            "object": res_json["data"][0]["object"],
            "index": res_json["data"][0]["index"],
            "embedding": res_json["data"][0]["embeddings"]
        }
        ret_json = {
            "object": res_json["object"],
            "data": [data],
            "model": res_json["model"],
            "usage": res_json["usage"],
        }
        return web.Response(body=json.dumps(ret_json).encode('utf-8'),
                            status=response.status,
                            content_type='application/json')
    else:
        return web.Response(body=response_stream, status=response.status, content_type=response.content_type)


async def handle_default(request, session):
    response = await proxy_request(request, session)

    return web.Response(body=response.content, status=response.status, content_type=response.content_type)


async def inspect_response(response):
    response_stream = io.BytesIO(await response.content.read())
    res_json = json.loads(response_stream.read().decode('utf-8'))

    response_stream.seek(0)

    if response.status != 200:
        print(f"Response (code: {response.status}):")
        print(res_json)

    return response_stream


async def proxy_request(request, session, json_body=None, target='mixtral-8x7b'):
    path = request.url.path.rstrip('/')
    if target == "mixtral-8x7b":
        target_url = request.app['mixtral_url']
    elif target == "llama3-8b":
        target_url = request.app['llama_url']
    elif target == "sdxl":
        target_url = request.app['diff_url']
    elif target == "gritlm-7b":
        target_url = request.app['embedding_url']
    else:
        raise f"Unrecognized target: {target}"
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
            elif path == '/models/llama3-8b':
                return web.json_response(status=200, data=models[2])
            elif path == '/models/gritlm-7b':
                return web.json_response(status=200, data=models[3])
            elif path == '/chat/completions':
                return await handle_chat_completions(request, session)
            elif path == '/images/generations':
                return await handle_image_generations(request, session)
            elif path == '/embeddings':
                return await handle_embeddings(request, session)

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
    # mixtral
    mixtral_url = LLM_URL
    if not mixtral_url.endswith('/'):
        mixtral_url += '/'
    mixtral_url = urljoin(mixtral_url, 'v1/')
    parsed_mixtral_url = urlparse(mixtral_url)

    # llama
    llama_url = LLAMA_URL
    if not llama_url.endswith('/'):
        llama_url += '/'
    llama_url = urljoin(llama_url, 'v1/')
    parsed_llama_url = urlparse(llama_url)

    diff_url = DIFFUSION_URL
    if not diff_url.endswith('/'):
        diff_url += '/'
    diff_url = urljoin(diff_url, 'v1/')
    parsed_diff_url = urlparse(diff_url)

    embedding_url = EMBEDDING_URL
    if not embedding_url.endswith('/'):
        embedding_url += '/'
    embedding_url = urljoin(embedding_url, 'v1/')
    parsed_embedding_url = urlparse(embedding_url)

    app = web.Application()
    app['mixtral_url'] = parsed_mixtral_url
    app['llama_url'] = parsed_llama_url
    app['diff_url'] = parsed_diff_url
    app['embedding_url'] = parsed_embedding_url
    app.add_routes([web.route('*', '/{tail:.*}', handler)])

    web.run_app(app, host='0.0.0.0', port=port)
    print(f'Reverse proxy server running on port {port}...')


if __name__ == '__main__':
    typer.run(main)
