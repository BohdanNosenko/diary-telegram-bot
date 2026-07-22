#!/usr/bin/env bash
for i in {1..20}; do
    echo "Attempt $i to run uv sync"
    if uv sync; then
        echo "uv sync completed successfully."
        exit 0
    fi
    echo "uv sync failed. Retrying in 2 seconds..."
    sleep 2
done
echo "uv sync failed after 20 attempts."
exit 1
