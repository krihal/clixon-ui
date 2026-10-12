"""`--debug-http`: one line per request the UI sends to the controller, and a summary every minute.

Shows what is sent (method, path, the user header), what came back (status) and how long the controller took to
start answering. Output goes to stderr."""

from __future__ import annotations

import asyncio
import sys
import time
from collections import Counter, defaultdict

from .client import USER_HEADER, ClixonClient

SUMMARY_SECONDS = 60


def _short(url) -> str:
    path = url.path.removeprefix("/restconf")
    return path + (f"?{url.query.decode()}" if url.query else "")


def install(client: ClixonClient):
    """Hook the client. Returns the coroutine function of the periodic summary: start it once the app runs."""
    count: Counter = Counter()
    millis: dict = defaultdict(float)
    started = {"t": time.monotonic()}

    async def on_request(request) -> None:
        request.extensions["debug_t0"] = time.monotonic()

    async def on_response(response) -> None:
        request = response.request
        ms = (time.monotonic() - request.extensions.get("debug_t0", time.monotonic())) * 1000
        key = f"{request.method} {request.url.path.removeprefix('/restconf')}"
        count[key] += 1
        millis[key] += ms
        user = request.headers.get(USER_HEADER, "-")
        print(f"http {request.method} {_short(request.url)[:110]} -> {response.status_code} {ms:.0f} ms user={user}", file=sys.stderr, flush=True)

    async def summary() -> None:
        while True:
            await asyncio.sleep(SUMMARY_SECONDS)
            minutes = (time.monotonic() - started["t"]) / 60
            print(f"http summary, last {minutes:.1f} min: {sum(count.values())} requests", file=sys.stderr)
            for key, n in count.most_common(8):
                print(f"  {n:5d}  avg {millis[key] / n:6.0f} ms  {key[:100]}", file=sys.stderr)
            count.clear()
            millis.clear()
            started["t"] = time.monotonic()

    client._http.event_hooks["request"].append(on_request)
    client._http.event_hooks["response"].append(on_response)
    return summary
