"""Narrow, source-pinned changedetection transport and local-AI safeguards."""

import json
import os
import fcntl
import stat
from urllib.parse import urljoin, urlsplit

SAFE_HEADERS = frozenset({"accept", "accept-language", "accept-encoding", "user-agent",
                          "cache-control", "pragma", "dnt", "content-type", "content-length"})


def origin(url):
    parsed = urlsplit((url or "").removeprefix("source:"))
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port or (
        443 if parsed.scheme.lower() == "https" else 80)


def check_redirect(old, new, headers):
    if origin(old) != origin(new) and any(k.lower() not in SAFE_HEADERS for k in (headers or {})):
        raise ValueError("Cross-origin redirect with credential/custom headers refused")


def safe_headers(headers):
    return {k: v for k, v in (headers or {}).items() if k.lower() in SAFE_HEADERS}


def check_transport_override(proxy, browser_url=None, *, browser=False):
    expected = os.environ.get("playwright_proxy_server" if browser else "HTTP_PROXY")
    if not expected or (proxy and proxy != expected):
        raise ValueError("Only the private destination-checking proxy is permitted")
    if browser_url and browser_url != os.environ.get("PLAYWRIGHT_DRIVER_URL"):
        raise ValueError("Only the private fixed browser connection is permitted")


def browser_proxy(proxy):
    server = (proxy or {}).get("server")
    check_transport_override(server, browser=True)
    return {"server": os.environ["playwright_proxy_server"], "bypass": "<-loopback>"}


def check_watch_url(url):
    # HTTP monitoring must not invoke the local model management/generation API.
    # Only the guarded native AI client may reach it through the app egress role.
    if urlsplit(url).port in (11434, 11435):
        raise ValueError("Model management endpoints are not watch destinations")


async def scope_browser_headers(context, headers, start_url, page):
    """Keep user-supplied secret/custom headers on the initial origin only.

    Explicit Cookie headers are scoped to the exact origin, including its port.
    Normal browser cookies continue to follow Chromium's policies.
    Service workers must remain disabled so they cannot bypass route handling.
    """
    restricted = {k: v for k, v in (headers or {}).items()
                  if k.lower() not in SAFE_HEADERS}
    start_origin = origin(start_url)
    if not restricted:
        return

    session = await context.new_cdp_session(page)
    names = {key.lower() for key in restricted}

    async def paused(event):
        request_id = event["requestId"]
        request = event["request"]
        try:
            if "responseStatusCode" in event or "responseErrorReason" in event:
                location = next((item["value"] for item in event.get("responseHeaders", [])
                                 if item["name"].lower() == "location"), "")
                if (event.get("responseStatusCode") in (301, 302, 303, 307, 308)
                        and location and origin(request["url"]) == start_origin
                        and origin(urljoin(request["url"], location)) != start_origin):
                    # Reject before Chromium follows a credentialed redirect: even a
                    # 307/308 body must not reach a foreign destination.
                    await session.send("Fetch.failRequest", {"requestId": request_id,
                                                              "errorReason": "BlockedByClient"})
                else:
                    await session.send("Fetch.continueResponse", {"requestId": request_id})
                return
            current = {k: v for k, v in request.get("headers", {}).items() if k.lower() not in names}
            if origin(request["url"]) == start_origin:
                current.update(restricted)
            # CDP overrides are per hop. Playwright route.continue_ overrides can
            # persist across redirect hops and are deliberately not used here.
            await session.send("Fetch.continueRequest", {
                "requestId": request_id, "interceptResponse": True,
                "headers": [{"name": key, "value": value} for key, value in current.items()],
            })
        except Exception:
            try:
                await session.send("Fetch.failRequest", {"requestId": request_id,
                                                          "errorReason": "BlockedByClient"})
            except Exception:
                pass

    session.on("Fetch.requestPaused", paused)
    await session.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})


def local_llm_kwargs(kwargs):
    """No cloud inference, GPU offload, alternate models, tools or open-ended jobs."""
    model = os.environ.get("EDSYS_LLM_MODEL", "")
    endpoint = os.environ.get("EDSYS_LLM_BASE", "").rstrip("/")
    if (not model.startswith("ollama_chat/") or not endpoint
            or kwargs.get("model") != model
            or (kwargs.get("api_base") or "").rstrip("/") != endpoint
            or kwargs.get("api_key")):
        raise ValueError("Only the configured local Ollama model is permitted")
    if len(json.dumps(kwargs.get("messages", []), ensure_ascii=False).encode()) > 32768:
        raise ValueError("Local AI input exceeds its bounded context")
    kwargs.pop("extra_body", None)
    kwargs.update(num_gpu=0, num_thread=4, num_ctx=8192, keep_alive=0,
                  max_tokens=min(512, max(1, int(kwargs.get("max_tokens") or 400))),
                  timeout=min(120, max(1, int(kwargs.get("timeout") or 120))))
    return kwargs


def local_model_provider(provider, endpoint, api_key=None):
    if (provider != "ollama" or (endpoint or "").rstrip("/") !=
            os.environ.get("EDSYS_LLM_BASE", "").rstrip("/") or api_key):
        raise ValueError("Model discovery is restricted to the configured local Ollama server")


def local_completion(function, kwargs):
    """One local inference at a time across summary, extraction and UI subprocesses."""
    checked = local_llm_kwargs(kwargs)
    descriptor = os.open("/tmp/edsys-local-llm.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("Invalid local inference lock")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Local AI is busy; use the preserved raw diff") from exc
        return function(**checked)
    finally:
        os.close(descriptor)
