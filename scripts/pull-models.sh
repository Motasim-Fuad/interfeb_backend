#!/bin/sh
# Downloads the local AI models named in .env into the ollama volume (already-present ones are skipped).
set -e
sleep 3
VISION="${VISION_FALLBACK_MODEL:-qwen2.5vl:3b}"
echo "Pulling vision model: $VISION"
ollama pull "$VISION"
case "$ASSISTANT_LLM_BASE_URL" in
  *ollama*)
    if [ -n "$ASSISTANT_LLM_MODEL" ]; then
      echo "Pulling chat model: $ASSISTANT_LLM_MODEL"
      ollama pull "$ASSISTANT_LLM_MODEL"
    fi
    ;;
esac
echo "Models ready."
