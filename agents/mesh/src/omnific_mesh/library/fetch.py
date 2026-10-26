"""Downloads for the library route: HTTPS only, public hosts only (checked on every redirect), size-capped.

Model files are data, never code: only GLB is accepted (magic-checked) and it is parsed by our own reader."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket

import httpx

USER_AGENT = "omnific-mesh/0.1"


class UnsafeDownload(ValueError):
    pass


def max_bytes() -> int:
    try:
        return max(1, int(float(os.environ.get("LIBRARY_MAX_MB", "50")) * 1024 * 1024))
    except ValueError:
        return 50 * 1024 * 1024


async def _public_https_only(request: httpx.Request) -> None:
    url = request.url
    if url.scheme != "https":
        raise UnsafeDownload(f"refusing non-HTTPS URL {url}")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(url.host, url.port or 443, type=socket.SOCK_STREAM)
    except OSError as e:
        raise UnsafeDownload(f"cannot resolve {url.host}: {e}") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise UnsafeDownload(f"refusing {url.host}: resolves to non-public address {ip}")


def client(timeout: float = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, follow_redirects=True, max_redirects=5,
                             headers={"User-Agent": USER_AGENT}, event_hooks={"request": [_public_https_only]})


async def download(url: str, limit: int | None = None, timeout: float = 120.0) -> bytes:
    limit = limit or max_bytes()
    async with client(timeout) as http, http.stream("GET", url) as r:
        r.raise_for_status()
        declared = int(r.headers.get("content-length") or 0)
        if declared > limit:
            raise UnsafeDownload(f"{declared / 1e6:.1f} MB exceeds the {limit / 1e6:.0f} MB limit")
        buf = bytearray()
        async for chunk in r.aiter_bytes():
            buf += chunk
            if len(buf) > limit:
                raise UnsafeDownload(f"download exceeds the {limit / 1e6:.0f} MB limit")
    return bytes(buf)


def check_glb(data: bytes) -> None:
    if len(data) < 20 or data[:4] != b"glTF":
        raise UnsafeDownload("not a GLB file (bad magic)")
    if int.from_bytes(data[4:8], "little") != 2:
        raise UnsafeDownload("unsupported glTF container version")
