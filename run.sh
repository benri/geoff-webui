#!/usr/bin/env bash

target=$TARGET

# if target is proxy
if [ "$target" = "proxy" ]
then
  python proxy.py --port $PORT
  exit 0
fi

proto=$(echo $LLM_URL | grep :// | sed -e's,^\(.*://\).*,\1,g')
url=$(echo $LLM_URL | sed -e s,$proto,,g)
token="$(echo $url | grep @ | cut -d@ -f1)"
host="$(echo $url | grep @ | cut -d@ -f2)"

export OPENAI_API_BASE_URL=$PROXY_URL
export OPENAI_API_KEY=$token

open-webui serve --port $PORT
