#!/usr/bin/env python3
"""Fan out config/ to deploy targets. Values are NEVER printed.

Targets are declared per node in config/nodes.toml (`deploy` tags); this
script only knows the target KINDS below. Service IDs stay in env.
Unknown --targets entries or node tags fail fast.

Inputs (env — credentials and ID overrides only; nodes.toml is the default):
  SECRETS_JSON   path to sops-decrypted secrets.yaml as JSON (CI decrypts first)
  HF_TOKEN / HF_SPACE_ID (override for nodes' space_id)
  RENDER_API_KEY / RENDER_SERVICE_ID (override) / RENDER_REDEPLOY (default 1)

Refuses to run unless config/secrets.yaml carries a sops age envelope
(guards against fanning out a forgotten-plaintext file).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "src"))

from fief.config_cmd import assert_encrypted, load_nodes

RENDER_API = "https://api.render.com/v1"

#: Target kinds fan-out knows how to deliver to. Nodes opt in via
#: `deploy` tags in nodes.toml — add a kind here AND document it there.
TARGETS = ("hf-space", "render")


def check_envelope(path: Path) -> None:
    """Refuse plaintext: the committed file must be sops+age encrypted."""
    assert_encrypted(path)


def fanout_hf(
    api: object, space_id: str, node: dict, secrets: dict, dry: bool, log
) -> bool:
    did = False
    for key in node.get("secrets", []):
        if key not in secrets:
            raise SystemExit(f"secret {key!r} missing for HF fan-out")
        if dry:
            log(f"would set secret {key} on space {space_id}")
        else:
            api.add_space_secret(space_id, key, secrets[key])  # type: ignore[attr-defined]
            log(f"set secret {key} on space {space_id} (value hidden)")
        did = True
    for key, value in node.get("vars", {}).items():
        if dry:
            log(f"would set variable {key} on space {space_id}")
        else:
            api.add_space_variable(space_id, key, str(value))  # type: ignore[attr-defined]
            log(f"set variable {key} on space {space_id}")
        did = True
    return did


def _render_request(api_key: str, method: str, path: str, body: object = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        RENDER_API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return {"status": resp.status}


def fanout_render(
    api_key: str, service_id: str, node: dict, secrets: dict, dry: bool, log
) -> bool:
    did = False
    payload = {k: secrets[k] for k in node.get("secrets", []) if k in secrets}
    missing = [k for k in node.get("secrets", []) if k not in secrets]
    if missing:
        raise SystemExit(f"secrets missing for Render fan-out: {missing}")
    payload.update({k: str(v) for k, v in node.get("vars", {}).items()})
    for key, value in payload.items():
        if dry:
            log(f"would set env {key} on Render {service_id}")
        else:
            _render_request(
                api_key,
                "PUT",
                f"/services/{service_id}/env-vars/{key}",
                {"value": value},
            )
            log(f"set env {key} on Render {service_id} (value hidden)")
        did = True
    return did


def render_redeploy(api_key: str, service_id: str, log) -> None:
    _render_request(api_key, "POST", f"/services/{service_id}/deploys", {})
    log(f"triggered Render redeploy of {service_id} (env applies on deploy)")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--secrets-json", default=os.environ.get("SECRETS_JSON", ""))
    p.add_argument("--nodes", default=str(HERE / "config" / "nodes.toml"))
    p.add_argument("--secrets-file", default=str(HERE / "config" / "secrets.yaml"))
    p.add_argument("--targets", default=",".join(TARGETS))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    def log(msg: str) -> None:
        print(msg, flush=True)  # keys/targets only, never values

    if not args.secrets_json:
        raise SystemExit("SECRETS_JSON is required (sops-decrypted secrets as JSON)")
    check_envelope(Path(args.secrets_file))
    secrets = json.loads(Path(args.secrets_json).read_text())
    nodes = load_nodes(Path(args.nodes))["nodes"]
    targets = {t.strip() for t in args.targets.split(",") if t.strip()}
    unknown_targets = targets - set(TARGETS)
    if unknown_targets:
        raise SystemExit(
            f"unknown fan-out targets: {sorted(unknown_targets)} (want {list(TARGETS)})"
        )
    for name, node in nodes.items():
        unknown_tags = set(node.get("deploy", [])) - set(TARGETS)
        if unknown_tags:
            raise SystemExit(
                f"node {name!r} has unknown deploy tags: {sorted(unknown_tags)}"
            )

    def tagged(kind: str) -> list[tuple[str, dict]]:
        return [(n, d) for n, d in nodes.items() if kind in d.get("deploy", [])]

    hf_token = os.environ.get("HF_TOKEN", "")
    render_key = os.environ.get("RENDER_API_KEY", "")

    if "hf-space" in targets:
        hf_nodes = tagged("hf-space")
        if not hf_nodes:
            log("no node deploys to hf-space, skipped")
        elif not args.dry_run and not hf_token:
            log("HF skipped (need HF_TOKEN)")
        else:
            api = None
            for name, node in hf_nodes:
                sid = (
                    os.environ.get("HF_SPACE_ID", "")
                    or node.get("space_id", "")
                    or "<space>"
                )
                if not args.dry_run and "/" not in sid:
                    log(
                        f"node {name} has no space_id, skipped "
                        "(set nodes.toml space_id or HF_SPACE_ID)"
                    )
                    continue
                if api is None:
                    if args.dry_run:
                        from unittest.mock import MagicMock

                        api = MagicMock()
                    else:
                        from huggingface_hub import HfApi

                        api = HfApi(token=hf_token)
                log(f"node {name} -> space {sid}")
                fanout_hf(api, sid, node, secrets, args.dry_run, log)

    if "render" in targets:
        render_nodes = tagged("render")
        if not render_nodes:
            log("no node deploys to render, skipped")
        elif not args.dry_run and not render_key:
            log("Render skipped (need RENDER_API_KEY)")
        else:
            updated: set[str] = set()
            for name, node in render_nodes:
                svc = (
                    os.environ.get("RENDER_SERVICE_ID", "")
                    or node.get("service_id", "")
                    or "<service>"
                )
                if not args.dry_run and svc == "<service>":
                    log(
                        f"node {name} has no service_id, skipped "
                        "(set nodes.toml service_id or RENDER_SERVICE_ID)"
                    )
                    continue
                log(f"node {name} -> Render {svc}")
                if (
                    fanout_render(render_key, svc, node, secrets, args.dry_run, log)
                    and not args.dry_run
                ):
                    updated.add(svc)
            if (
                updated
                and not args.dry_run
                and os.environ.get("RENDER_REDEPLOY", "1") == "1"
            ):
                for svc in sorted(updated):
                    render_redeploy(render_key, svc, log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
