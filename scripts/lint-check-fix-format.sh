#!/bin/bash

# Core rules:
#   I  - Import organization (isort compatibility)
#   F  - Pyflakes (unused imports, undefined names, etc.)
poetry run ruff check --select=I,F --fix .

# Formatting (black-compatible)
poetry run ruff format .

