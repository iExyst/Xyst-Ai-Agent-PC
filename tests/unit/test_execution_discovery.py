import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.execution_discovery import DistributionInventory, parse_distributions
from xaa.storage.runs import RunStore


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16-le", "utf-16"])
def test_distribution_encoding_and_profile_support(encoding):
    result = parse_distributions("Ubuntu-24.04\r\nCustom Linux\r\n\u96ea\r\n".encode(encoding))
    assert {entry.name: entry.supported_by_profile for entry in result} == {
        "Ubuntu-24.04": True,
        "Custom Linux": False,
        "\u96ea": False,
    }


@pytest.mark.parametrize("raw", [b"x\nx\n", b"\xff", b"bad\x01name", b"x" * 16385])
def test_invalid_distribution_output_refused(raw):
    with pytest.raises((ValueError, UnicodeError)):
        parse_distributions(raw)


def test_inventory_requires_auth_before_listing(workspace_tmp_path, monkeypatch):
    calls = []

    async def inventory():
        calls.append(True)
        return DistributionInventory(state="available", distributions=[])

    monkeypatch.setattr("xaa.api.app.installed_distributions", inventory)
    token = "inventory-test-token-at-least-32-characters"
    with TestClient(
        create_app(LMStudioProvider(), SecretStr(token), store=RunStore(workspace_tmp_path / "db"))
    ) as client:
        assert client.get("/execution/distributions").status_code == 401
        assert not calls
        result = client.get(
            "/execution/distributions", headers={"Authorization": "Bearer " + token}
        )
        assert result.status_code == 200 and result.json() == {
            "state": "available",
            "distributions": [],
        }
        assert len(calls) == 1
