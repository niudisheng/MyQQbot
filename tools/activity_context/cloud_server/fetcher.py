"""向外部 URL 发起请求并返回结果（用于云端拉取第三方数据）。"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import config


def fetch_url(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float | None = None,
    max_bytes: int | None = None,
) -> tuple[int | None, dict[str, str], bytes | None, str | None]:
    """
    返回 (status_code, response_headers, response_body, error_message)。
    body 超过 max_bytes 时截断并附带说明。
    """
    timeout = timeout if timeout is not None else config.fetch_timeout_seconds()
    max_b = max_bytes if max_bytes is not None else config.max_response_bytes()
    hdrs = {"User-Agent": "activity-context-cloud-server/1.0"}
    if headers:
        hdrs.update(headers)
    req = Request(url, data=body, headers=hdrs, method=method.upper())
    try:
        with urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            raw_headers = {k.lower(): v for k, v in resp.headers.items()}
            chunk = resp.read(max_b + 1)
            if len(chunk) > max_b:
                chunk = chunk[:max_b]
                note = f"\n...[truncated at {max_b} bytes]"
                try:
                    text = chunk.decode("utf-8", errors="replace") + note
                except Exception:
                    text = note
                return status, raw_headers, text.encode("utf-8", errors="replace"), None
            return status, raw_headers, chunk, None
    except HTTPError as exc:
        err_body = exc.read()
        if err_body and len(err_body) > max_b:
            err_body = err_body[:max_b]
        hdrs_err = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
        return exc.code, hdrs_err, err_body, None
    except URLError as exc:
        return None, {}, None, str(exc.reason or exc)
    except TimeoutError as exc:
        return None, {}, None, str(exc)
    except OSError as exc:
        return None, {}, None, str(exc)


def try_decode_body(raw: bytes | None) -> str:
    if raw is None:
        return ""
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return repr(raw)
