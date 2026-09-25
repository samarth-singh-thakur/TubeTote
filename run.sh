#!/bin/zsh
# Launch TubeTote using the project's virtualenv.
cd "$(dirname "$0")"
exec .venv/bin/python app.py
