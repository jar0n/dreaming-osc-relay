#!/bin/bash
cd "$(dirname "$0")" || exit 1

uv run dreaming-osc-relay \
      --server https://cervh603vyrh85-8000.proxy.runpod.net --token b6a4cacbfd9329cd83bf72ee45885332 \
      --osc 127.0.0.1:57120
