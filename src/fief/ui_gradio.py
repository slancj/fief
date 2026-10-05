"""Optional Gradio status UI. Imported lazily — core never requires gradio."""

from __future__ import annotations

import inspect
from collections.abc import Callable

try:
    import spaces
except ImportError:
    spaces = None  # only present on HF ZeroGPU images


def _define_probe() -> Callable[[], None] | None:
    """Invisible ZeroGPU watchdog probe. Never called → zero GPU quota."""
    if spaces is None:
        return None

    @spaces.GPU
    def _gpu_probe() -> None:
        return None

    return _gpu_probe


_GPU_PROBE = _define_probe()


def _register_probe(gr: object) -> None:
    # ZeroGPU's startup scan walks Gradio's registered handlers; a bound
    # @spaces.GPU handler keeps the Space alive on ZeroGPU hardware.
    # The button is invisible and never clicked, so no GPU is ever used.
    if _GPU_PROBE is not None:
        gr.Button(visible=False).click(fn=_GPU_PROBE)  # type: ignore[attr-defined]


def launch_demo(demo: object, *, server_name: str, server_port: int) -> None:
    """Launch a Blocks demo, dropping kwargs the installed Gradio lacks.

    Gradio 6 removed e.g. ``show_api``; signature-filtering keeps this
    working on both 5.x and 6.x without version parsing.
    """
    launch = demo.launch  # type: ignore[attr-defined]
    try:
        params = inspect.signature(launch).parameters.values()
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
        names = {p.name for p in params}
    except (TypeError, ValueError):
        accepts_kwargs, names = False, set()
    extras = {"prevent_thread_lock": True, "show_api": False}
    if not accepts_kwargs:
        extras = {k: v for k, v in extras.items() if k in names}
    launch(server_name=server_name, server_port=server_port, **extras)


def build_ui(
    auth_ok: bool, hub_url: str, snapshot: Callable[[], tuple[str, str]]
) -> object:
    import gradio as gr

    if not auth_ok:
        with gr.Blocks(title="fief monitor") as demo:
            _register_probe(gr)
            gr.Markdown("# fief monitor hub (not configured)")
            gr.Markdown(
                "Set the `CHISEL_AUTH` secret (format `user:secret`) "
                "in this service's environment, then restart.\n\n"
                "The secret is never baked into the build."
            )
        return demo

    with gr.Blocks(title="fief monitor") as demo:
        _register_probe(gr)
        gr.Markdown("# fief monitor hub")
        gr.Markdown(
            f"Chisel hub on `{hub_url}`. Browser traffic here is proxied "
            "through chisel to this page; tunnel clients connect to the "
            "same URL with the chisel client."
        )
        status_box = gr.Textbox(label="Hub status", value="starting...", lines=6)
        log_box = gr.Textbox(label="Recent hub log", value="", lines=20)
        refresh = gr.Button("Refresh")
        gr.Markdown(
            "## Clients\n\n"
            "Exit node (a Linux box on the LAN, opens the reverse SOCKS):\n"
            f"`HUB_URL='{hub_url}' CHISEL_AUTH='user:secret' fief exit`\n\n"
            "Consumers (forwards over one connection):\n"
            f"`HUB_URL='{hub_url}' CHISEL_AUTH='user:secret' fief forward`\n\n"
            "`1080` exits via the LAN exit node, `1081` via hub egress.\n"
            "Add `--no-ssh` on hubs without `SSH_PUBKEY` set.\n"
            "Shell on Docker hubs (needs `forward` running): `fief ssh`."
        )
        refresh.click(fn=snapshot, outputs=[status_box, log_box])
        demo.load(fn=snapshot, outputs=[status_box, log_box])
    return demo
