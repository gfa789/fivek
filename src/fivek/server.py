"""Launch the local FiveK HTTP application."""
from .config import Config
from .webapp import LocalServer


def serve(config: Config, port: int):
    if not 1 <= port <= 65535:
        raise ValueError("port must be from 1 to 65535")
    try:
        server = LocalServer(config, port)
    except OSError:
        raise RuntimeError(f"Could not open the local dashboard on port {port}. Stop the existing dashboard or choose another port with --port.") from None
    print(f"Your 5K dashboard: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.app.cancel_event.set()
        server.server_close()
        if server.app.worker_thread is not None:
            server.app.worker_thread.join(timeout=5)
        if server.app.calendar_thread is not None:
            server.app.calendar_thread.join(timeout=5)
