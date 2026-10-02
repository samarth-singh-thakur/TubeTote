#!/bin/zsh
# Launch TubeTote in your default browser.
cd "$(dirname "$0")"
exec .venv/bin/python web_app.py "$@"
