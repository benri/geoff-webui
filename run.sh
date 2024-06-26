#!/usr/bin/env bash

target=$TARGET

# if target is proxy
if [ "$target" = "proxy" ]
then
  python proxy.py --port $PORT
  exit 0
fi

export OPENAI_API_BASE_URL=$PROXY_URL
export OPENAI_API_KEY="foobar"

export IMAGES_OPENAI_API_BASE_URL=$PROXY_URL
export IMAGES_OPENAI_API_KEY="foobar"

open-webui serve --port $PORT
