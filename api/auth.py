from __future__ import annotations

import os

from fastapi import Header, HTTPException


def require_admin_key(x_api_key: str = Header(...)) -> str:
    """FastAPI dependency — validates the X-Api-Key header against ADMIN_API_KEY env var."""
    expected = os.environ.get("ADMIN_API_KEY", "")
    if not expected:
        raise HTTPException(status_code=500, detail="ADMIN_API_KEY not configured on server")
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key
