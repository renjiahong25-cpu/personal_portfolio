"""Chunked HTTP Range streaming, shared by every endpoint that serves a file.

An ``<audio>`` element is not a download: it asks for the bytes it needs with a
``Range`` header, seeks by re-asking for a different window, and often re-requests
the tail after a seek. Answering that with ``Path.read_bytes()`` - which is what
the score track routes used to do - reads the whole 60 MB render into memory,
copies it into the response buffer, and only then starts writing, so the process
holds the entire file per concurrent client and the first byte of audio arrives
after the last one was read.

One implementation, used by the cache audio route and every score output route,
streams 64 KB at a time: memory is one chunk plus the socket buffer, no matter how
long the file is.

The Range semantics are RFC 9110's, and they are deliberately identical on every
route, because a player that can seek one source but not another is a bug report
waiting to happen:

* a satisfiable single range -> ``206`` plus ``Content-Range: bytes first-last/size``
* an unsatisfiable one (first byte past the end) -> ``416`` plus ``bytes */size``
* an unparsable or multi-range header -> ignored, i.e. the full ``200`` body

The last one is the rule people get wrong: a Range the server cannot understand is
not permission to fail the request, it is permission to serve the whole entity.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import tornado.web

from core.errors import ValidationError

logger = logging.getLogger(__name__)

#: Bytes per read: small enough that a seek never waits on a large read, large
#: enough that the syscall and flush overhead disappears.
CHUNK_SIZE = 64 * 1024

#: Only a single ``bytes=first-last`` range is answered. A multi-range request is
#: legal in RFC 9110 but no player sends one, and multipart/byteranges would cost
#: more than it is worth here.
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def parse_range(header: str | None, size: int) -> tuple[int, int, int]:
    """Resolve a ``Range`` header against a known file size.

    Returns ``(first, last, status)`` where ``last`` is inclusive. A 200 means
    "serve the whole file", whatever the client asked for.
    """
    if not header:
        return 0, size - 1, 200
    match = _RANGE_RE.match(header.strip())
    if not match:
        # An unparsable Range must be ignored, per RFC 9110.
        return 0, size - 1, 200
    first, last = match.group(1), match.group(2)
    try:
        if first:
            start = int(first)
            end = int(last) if last else size - 1
        else:
            if not last:
                return 0, size - 1, 200
            # A suffix range: the final N bytes.
            start = max(0, size - int(last))
            end = size - 1
    except ValueError as exc:
        raise ValidationError(f"malformed Range header: {header}") from exc
    end = min(end, size - 1)
    if start > end or start >= size:
        return 0, 0, 416
    return start, end, 206


async def send_file_range(
    handler: tornado.web.RequestHandler,
    path: Path,
    *,
    content_type: str,
    cache_control: str,
    content_disposition: str | None = None,
) -> None:
    """Serve ``path`` to ``handler``, honouring a ``Range`` header if there is one.

    The handler is given its status, headers and body here and nothing else, so
    every file route answers a seek the same way whichever module it belongs to.
    """
    size = path.stat().st_size
    start, end, status = parse_range(handler.request.headers.get("Range"), size)

    if status == 416:
        handler.set_status(416)
        handler.set_header("Content-Range", f"bytes */{size}")
        handler.finish()
        return

    handler.set_status(status)
    handler.set_header("Content-Type", content_type)
    handler.set_header("Accept-Ranges", "bytes")
    handler.set_header("Content-Length", str(end - start + 1))
    handler.set_header("Cache-Control", cache_control)
    if content_disposition is not None:
        handler.set_header("Content-Disposition", content_disposition)
    if status == 206:
        handler.set_header("Content-Range", f"bytes {start}-{end}/{size}")

    remaining = end - start + 1
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            handler.write(chunk)
            remaining -= len(chunk)
            await handler.flush()


__all__ = ["CHUNK_SIZE", "parse_range", "send_file_range"]