"""launch_demo must tolerate Gradio 5.x and 6.x launch() signatures."""

import importlib
import sys
import types


class FakeDemo5:
    def __init__(self):
        self.kwargs = None

    def launch(self, server_name, server_port, prevent_thread_lock, show_api):
        self.kwargs = {
            "server_name": server_name,
            "server_port": server_port,
            "prevent_thread_lock": prevent_thread_lock,
            "show_api": show_api,
        }


class FakeDemo6:
    """Gradio 6: show_api removed."""

    def __init__(self):
        self.kwargs = None

    def launch(self, server_name, server_port, prevent_thread_lock=True):
        self.kwargs = {
            "server_name": server_name,
            "server_port": server_port,
            "prevent_thread_lock": prevent_thread_lock,
        }


def test_launch_demo_gradio5():
    from fief.ui_gradio import launch_demo

    demo = FakeDemo5()
    launch_demo(demo, server_name="127.0.0.1", server_port=7861)
    assert demo.kwargs == {
        "server_name": "127.0.0.1",
        "server_port": 7861,
        "prevent_thread_lock": True,
        "show_api": False,
    }


def test_launch_demo_gradio6():
    from fief.ui_gradio import launch_demo

    demo = FakeDemo6()
    launch_demo(demo, server_name="127.0.0.1", server_port=7861)
    assert demo.kwargs == {
        "server_name": "127.0.0.1",
        "server_port": 7861,
        "prevent_thread_lock": True,
    }


def test_gpu_probe_defined_when_spaces_present(monkeypatch):
    import fief.ui_gradio as gradio_ui

    stub = types.ModuleType("spaces")
    stub.GPU = lambda fn: fn  # bare @spaces.GPU usage
    monkeypatch.setitem(sys.modules, "spaces", stub)
    importlib.reload(gradio_ui)
    try:
        assert callable(gradio_ui._GPU_PROBE)
    finally:
        monkeypatch.undo()
        importlib.reload(gradio_ui)
    assert gradio_ui._GPU_PROBE is None


def test_register_probe_binds_invisible_button(monkeypatch):
    from fief import ui_gradio

    clicks = []

    class FakeButton:
        def __init__(self, visible=True):
            self.visible = visible

        def click(self, fn):
            clicks.append((self.visible, fn))

    class FakeGr:
        Button = FakeButton

    def sentinel() -> None:
        return None

    monkeypatch.setattr(ui_gradio, "_GPU_PROBE", sentinel)
    ui_gradio._register_probe(FakeGr)
    assert clicks == [(False, sentinel)]

    monkeypatch.setattr(ui_gradio, "_GPU_PROBE", None)
    ui_gradio._register_probe(FakeGr)
    assert len(clicks) == 1  # no new handler without the probe
