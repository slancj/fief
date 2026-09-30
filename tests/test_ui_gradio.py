"""launch_demo must tolerate Gradio 5.x and 6.x launch() signatures."""


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
