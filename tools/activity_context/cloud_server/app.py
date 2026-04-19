"""FastAPI 应用：接收本地同步的摘要 + 对外拉取并落库。"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from . import config, db
from .fetcher import fetch_url, try_decode_body

security = HTTPBearer(auto_error=False)


def verify_token(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> str:
    expected = config.ingest_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server token not configured (ACTIVITY_CONTEXT_SERVER_TOKEN)",
        )
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    if creds.credentials != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid token",
        )
    return creds.credentials


class SummaryIngest(BaseModel):
    """与 cloud_sync.build_public_payload 输出字段一致；含 UTC + reference_timezone 本地时间。"""

    model_config = ConfigDict(extra="allow")

    summary_id: int = Field(..., ge=0)
    start_at: str
    end_at: str
    project_hint: str | None = None
    task_summary: str | None = None
    observed_apps: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    data_status: str
    confidence: float
    missing_ranges: list[dict[str, Any]] = Field(default_factory=list)
    source_event_count: int = 0
    observed_facts: str | None = None
    # 可选：本地客户端上传的时区语义（便于 OpenAPI 展示；extra=allow 也会保留其它键）
    start_at_utc: str | None = None
    end_at_utc: str | None = None
    reference_timezone: str | None = None
    start_at_local_iso: str | None = None
    end_at_local_iso: str | None = None
    start_at_local_clock: str | None = None
    end_at_local_clock: str | None = None
    time_semantics: str | None = None
    missing_ranges_local: list[dict[str, Any]] | None = None


_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "HEAD", "PATCH"})


class FetchRequest(BaseModel):
    url: str = Field(..., min_length=4)
    method: str = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    label: str | None = None


def create_app() -> FastAPI:
    app = FastAPI(
        title="Activity Context Cloud",
        description="接收本地 activity_context 摘要同步，并支持受控的外部 HTTP 拉取与落库。",
        version="1.0.0",
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "activity-context-cloud"}

    @app.post("/api/v1/summaries", status_code=status.HTTP_201_CREATED)
    def ingest_summary(
        payload: SummaryIngest,
        request: Request,
        _: Annotated[str, Depends(verify_token)],
    ) -> dict[str, Any]:
        client_id = request.headers.get("X-Client-Id", "").strip() or ""
        body = payload.model_dump()
        with db.connect() as conn:
            row_id = db.upsert_summary(conn, client_id=client_id, payload=body)
            conn.commit()
        return {"ok": True, "id": row_id, "client_summary_id": payload.summary_id}

    @app.get("/api/v1/summaries")
    def list_summaries(
        _: Annotated[str, Depends(verify_token)],
        limit: int = Query(50, ge=1, le=500),
        project: str | None = None,
        since: str | None = None,
    ) -> dict[str, Any]:
        with db.connect() as conn:
            if project:
                cur = conn.execute(
                    """
                    SELECT * FROM received_summaries
                    WHERE project_hint LIKE ?
                    ORDER BY end_at DESC
                    LIMIT ?
                    """,
                    (f"%{project}%", limit),
                )
            elif since:
                cur = conn.execute(
                    """
                    SELECT * FROM received_summaries
                    WHERE end_at >= ?
                    ORDER BY end_at DESC
                    LIMIT ?
                    """,
                    (since, limit),
                )
            else:
                cur = conn.execute(
                    """
                    SELECT * FROM received_summaries
                    ORDER BY end_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            rows = cur.fetchall()
        items = []
        for r in rows:
            try:
                full_payload = json.loads(r["payload_json"] or "{}")
            except json.JSONDecodeError:
                full_payload = {}
            items.append(
                {
                    "id": r["id"],
                    "client_id": r["client_id"],
                    "client_summary_id": r["client_summary_id"],
                    "start_at": r["start_at"],
                    "end_at": r["end_at"],
                    "project_hint": r["project_hint"],
                    "task_summary": r["task_summary"],
                    "observed_apps": json.loads(r["observed_apps_json"] or "[]"),
                    "tags": json.loads(r["tags_json"] or "[]"),
                    "data_status": r["data_status"],
                    "confidence": r["confidence"],
                    "missing_ranges": json.loads(r["missing_ranges_json"] or "[]"),
                    "source_event_count": r["source_event_count"],
                    "observed_facts": r["observed_facts"],
                    "received_at": r["received_at"],
                    "payload": full_payload,
                }
            )
        return {"ok": True, "count": len(items), "items": items}

    @app.post("/api/v1/fetch")
    def fetch_external(
        req: FetchRequest,
        _: Annotated[str, Depends(verify_token)],
    ) -> dict[str, Any]:
        method = req.method.upper().strip()
        if method not in _ALLOWED_METHODS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported method: {req.method}",
            )
        body_bytes = req.body.encode("utf-8") if req.body else None
        status_code, resp_headers, raw, err = fetch_url(
            req.url,
            method=method,
            headers=req.headers,
            body=body_bytes,
        )
        text = try_decode_body(raw)
        with db.connect() as conn:
            log_id = db.insert_fetch_log(
                conn,
                label=req.label,
                url=req.url,
                method=method,
                request_headers=req.headers,
                status_code=status_code,
                response_headers=resp_headers or None,
                response_body=text if not err else None,
                error=err,
                bytes_fetched=len(raw or b""),
            )
        return {
            "ok": err is None and status_code is not None and status_code < 400,
            "log_id": log_id,
            "status_code": status_code,
            "error": err,
            "body_preview": text[:4000] if text else None,
        }

    @app.get("/api/v1/fetches")
    def list_fetches(
        _: Annotated[str, Depends(verify_token)],
        limit: int = Query(30, ge=1, le=200),
    ) -> dict[str, Any]:
        with db.connect() as conn:
            cur = conn.execute(
                """
                SELECT id, label, url, method, status_code, error,
                       bytes_fetched, fetched_at
                FROM external_fetch_logs
                ORDER BY fetched_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cur.fetchall()
        return {
            "ok": True,
            "items": [dict(r) for r in rows],
        }

    return app


app = create_app()
