import pytest
from xaa.credentials import CredentialStore


def test_credential_store_uses_a_validated_opt_in_namespace(monkeypatch):
    monkeypatch.setenv("XAA_CREDENTIAL_NAMESPACE", "acceptance-123")
    assert CredentialStore().service == "Xyst.AI.Agent.acceptance-123"


@pytest.mark.parametrize("namespace", [".", "space is invalid", "x" * 65])
def test_credential_store_rejects_invalid_namespaces(monkeypatch, namespace):
    monkeypatch.setenv("XAA_CREDENTIAL_NAMESPACE", namespace)
    with pytest.raises(ValueError, match="Credential namespace"):
        CredentialStore()
