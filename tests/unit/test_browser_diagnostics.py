from __future__ import annotations

from xaa.runtime.browser import BrowserWorkerResponse


def test_browser_worker_error_does_not_require_success_payload() -> None:
    response = BrowserWorkerResponse.model_validate_json(
        b'{"ok":false,"error":"Timeout waiting for locator(body)."}'
    )

    assert response.ok is False
    assert response.result == {}
    assert response.error == "Timeout waiting for locator(body)."


def test_browser_worker_success_requires_page_evidence_in_acceptance() -> None:
    response = BrowserWorkerResponse.model_validate_json(
        b'{"ok":true,"result":{"title":"Example Domain"}}'
    )

    assert response.ok is True
    assert response.result == {"title": "Example Domain"}
