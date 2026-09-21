"""Private, authenticated browser-sidecar protocol. Never log payloads."""

import os
from pathlib import Path

import httpx


def token() -> str:
    return Path(os.environ["MFP_BROWSER_TOKEN_FILE"]).read_text().strip()


def call(operation: str, **data) -> dict:
    with httpx.Client(timeout=90, trust_env=False) as client:
        response = client.post(
            os.environ.get("MFP_BROWSER_URL", "http://browser:8090") + "/" + operation,
            headers={"Authorization": "Bearer " + token()}, json=data,
        )
        if response.status_code != 200:
            raise RuntimeError("Browser operation failed; reconnect or try again.")
        return response.json()
