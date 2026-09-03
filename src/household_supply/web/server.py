from __future__ import annotations

from ipaddress import ip_address


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def serve_local_web(
    app,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    allow_remote: bool = False,
    log_level: str = "info",
) -> None:
    """Run a local ASGI web app with an explicit no-remote default.

    The current M11 surface has no authentication. Binding to a non-loopback
    interface therefore requires an explicit ``allow_remote=True`` opt-in.
    """

    host = host.strip()
    if not host:
        raise ValueError("web host must not be empty")
    if type(port) is not int or not 1 <= port <= 65_535:
        raise ValueError("web port must be an integer from 1 to 65535")
    if not allow_remote and not _is_loopback_host(host):
        raise ValueError(
            "refusing to expose unauthenticated local web app on a non-loopback host"
        )
    try:
        import uvicorn
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "uvicorn is required to run the local web UI; install household-supply-planner[web]"
        ) from exc

    uvicorn.run(app, host=host, port=port, log_level=log_level)
