import os

import pytest

from backend.services.secret_store import (
    DPAPIProtector,
    SecretStore,
    SecretStoreCorrupted,
    SecretStoreUnavailable,
)


class FakeProtector:
    prefix = b"TEST-ENCRYPTED:"

    def protect(self, plaintext: bytes) -> bytes:
        return self.prefix + plaintext[::-1]

    def unprotect(self, ciphertext: bytes) -> bytes:
        if not ciphertext.startswith(self.prefix):
            raise ValueError("invalid ciphertext")
        return ciphertext[len(self.prefix):][::-1]


class UnavailableProtector:
    def protect(self, plaintext: bytes) -> bytes:
        raise SecretStoreUnavailable("protector unavailable")

    def unprotect(self, ciphertext: bytes) -> bytes:
        raise SecretStoreUnavailable("protector unavailable")


def test_secret_round_trip_uses_ciphertext_and_delete_removes_override(temp_dir):
    path = os.path.join(temp_dir, "runtime_secrets.bin")
    store = SecretStore(path, protector=FakeProtector())
    secret = "sensitive-runtime-key-1234"

    store.set_secret("llm_api_key", secret)

    assert store.get_secret("llm_api_key") == secret
    assert secret.encode() not in open(path, "rb").read()
    assert store.delete_secret("llm_api_key") is True
    assert not os.path.exists(path)
    assert store.get_secret("llm_api_key") is None


def test_secret_store_rejects_unknown_name_and_empty_value(temp_dir):
    store = SecretStore(
        os.path.join(temp_dir, "runtime_secrets.bin"),
        protector=FakeProtector(),
    )

    with pytest.raises(ValueError, match="不允许"):
        store.set_secret("unknown_api_key", "value")
    with pytest.raises(ValueError, match="不能为空"):
        store.set_secret("llm_api_key", "  ")


def test_corrupted_ciphertext_never_echoes_existing_secret(temp_dir):
    path = os.path.join(temp_dir, "runtime_secrets.bin")
    with open(path, "wb") as handle:
        handle.write(b"sensitive-existing-content")
    store = SecretStore(path, protector=FakeProtector())

    with pytest.raises(SecretStoreCorrupted) as error:
        store.get_secret("llm_api_key")

    assert "sensitive-existing-content" not in str(error.value)


def test_unavailable_protector_does_not_write_plaintext_fallback(temp_dir):
    path = os.path.join(temp_dir, "runtime_secrets.bin")
    store = SecretStore(path, protector=UnavailableProtector())

    with pytest.raises(SecretStoreUnavailable):
        store.set_secret("llm_api_key", "must-never-be-plaintext")

    assert not os.path.exists(path)


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_real_windows_dpapi_round_trip(temp_dir):
    path = os.path.join(temp_dir, "runtime_secrets.bin")
    store = SecretStore(path, protector=DPAPIProtector())

    store.set_secret("llm_api_key", "dpapi-smoke-value")

    assert store.get_secret("llm_api_key") == "dpapi-smoke-value"
    assert b"dpapi-smoke-value" not in open(path, "rb").read()
