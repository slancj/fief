"""Optional Gradio status UI. Imported lazily — core never requires gradio."""

from __future__ import annotations

from collections.abc import Callable


def build_ui(
    auth_ok: bool, hub_url: str, snapshot: Callable[[], tuple[str, str]]
) -> object:
    import gradio as gr

    if not auth_ok:
        with gr.Blocks(title="fief-relay") as demo:
            gr.Markdown("# fief-relay hub (not configured)")
            gr.Markdown(
                "Set the `CHISEL_AUTH` secret (format `user:secret`) "
                "in this service's environment, then restart.\n\n"
                "The secret is never baked into the build."
            )
        return demo

    with gr.Blocks(title="fief-relay") as demo:
        gr.Markdown("# fief-relay hub")
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
            "Add `--no-ssh` on hubs without sshd."
        )
        refresh.click(fn=snapshot, outputs=[status_box, log_box])
        demo.load(fn=snapshot, outputs=[status_box, log_box])
    return demo
