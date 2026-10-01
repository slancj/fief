#!/usr/bin/env python3
"""Sync the assembled Space payload to Hugging Face.

Env:
  HF_TOKEN    write token for the Space
  HF_SPACE_ID "<owner>/<name>"
  GITHUB_SHA  short commit label (optional, defaults to "local")

Creation is best-effort: if the API refuses ``create_repo`` (observed:
402 Payment Required), warn and continue — the sync succeeds whenever
the Space already exists. Only a genuinely missing Space fails, at
``upload_folder`` with a clear error.
"""

from __future__ import annotations

import os


def sync(api, space_id: str, folder: str, commit_message: str, log) -> str:
    try:
        api.create_repo(
            space_id,
            repo_type="space",
            space_sdk="gradio",
            private=True,
            exist_ok=True,
        )
    except Exception as exc:  # noqa: BLE001 - any create failure is non-fatal
        log(f"warning: create_repo failed ({exc}); continuing to sync")
    info = api.upload_folder(
        repo_id=space_id,
        repo_type="space",
        folder_path=folder,
        commit_message=commit_message,
    )
    log(f"deployed: {info.commit_url}")
    return info.commit_url


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--folder", default="dist/space")
    args = p.parse_args(argv)

    def log(msg: str) -> None:
        print(msg, flush=True)

    space_id = os.environ.get("HF_SPACE_ID", "")
    if "/" not in space_id:
        raise SystemExit("Set the HF_SPACE_ID repo variable, e.g. '<owner>/<name>'")
    sha = os.environ.get("GITHUB_SHA", "local")[:12]

    from huggingface_hub import HfApi

    sync(
        HfApi(token=os.environ["HF_TOKEN"]),
        space_id,
        args.folder,
        f"sync from fief@{sha}",
        log,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
