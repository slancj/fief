#!/usr/bin/env python3
"""HF Spaces entrypoint (Gradio SDK). Thin shim over the shared fief package.

The deploy workflow vendors src/fief into this folder before upload, so
`fief.*` resolves locally on the Space. All logic lives in the package.
"""

import os
from dataclasses import replace

os.environ.setdefault("PORT", "7860")  # gradio SDK exposes 7860

from fief.config import hub_config_from_env
from fief.hub import main

if __name__ == "__main__":
    cfg = replace(hub_config_from_env(), ui="gradio")
    raise SystemExit(main(cfg))
