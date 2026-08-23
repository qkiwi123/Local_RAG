"""Windows 用户级 DPAPI 密钥存储。

文件只保存 DPAPI 密文；DPAPI 不可用时拒绝写入，绝不降级为明文。
保护器可注入，便于在非 Windows CI 中验证存储协议和原子写语义。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
from typing import Protocol
from uuid import uuid4


ALLOWED_SECRET_NAMES = frozenset({"llm_api_key"})
_ENTROPY = b"RAG-runtime-settings-v1"


class SecretStoreError(RuntimeError):
    """密钥存储错误；异常消息不得包含密钥值。"""


class SecretStoreUnavailable(SecretStoreError):
    """当前系统无法使用 DPAPI。"""


class SecretStoreCorrupted(SecretStoreError):
    """密钥文件无法解密或内容损坏。"""


class SecretProtector(Protocol):
    def protect(self, plaintext: bytes) -> bytes: ...

    def unprotect(self, ciphertext: bytes) -> bytes: ...


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _make_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, max(1, len(data)))
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    return _DataBlob(len(data), pointer), buffer


class DPAPIProtector:
    """使用当前 Windows 用户凭据加解密。"""

    _CRYPTPROTECT_UI_FORBIDDEN = 0x1

    def __init__(self):
        if os.name != "nt":
            raise SecretStoreUnavailable("DPAPI 仅在 Windows 上可用")
        try:
            self._crypt32 = ctypes.WinDLL("crypt32.dll", use_last_error=True)
            self._kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
        except (AttributeError, OSError) as exc:
            raise SecretStoreUnavailable("无法加载 Windows DPAPI") from exc
        self._crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        self._crypt32.CryptProtectData.restype = wintypes.BOOL
        self._crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        self._crypt32.CryptUnprotectData.restype = wintypes.BOOL
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p

    def _transform(self, data: bytes, *, decrypt: bool) -> bytes:
        input_blob, input_buffer = _make_blob(data)
        entropy_blob, entropy_buffer = _make_blob(_ENTROPY)
        output_blob = _DataBlob()
        # input_buffer/entropy_buffer 局部变量必须存活到系统调用返回；
        # blob 本身只保存裸地址。
        function = (
            self._crypt32.CryptUnprotectData
            if decrypt
            else self._crypt32.CryptProtectData
        )
        try:
            if decrypt:
                success = function(
                    ctypes.byref(input_blob),
                    None,
                    ctypes.byref(entropy_blob),
                    None,
                    None,
                    self._CRYPTPROTECT_UI_FORBIDDEN,
                    ctypes.byref(output_blob),
                )
            else:
                success = function(
                    ctypes.byref(input_blob),
                    None,
                    ctypes.byref(entropy_blob),
                    None,
                    None,
                    self._CRYPTPROTECT_UI_FORBIDDEN,
                    ctypes.byref(output_blob),
                )
            if not success:
                operation = "解密" if decrypt else "加密"
                raise SecretStoreUnavailable(f"DPAPI {operation}失败")
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            if output_blob.pbData:
                self._kernel32.LocalFree(output_blob.pbData)

    def protect(self, plaintext: bytes) -> bytes:
        return self._transform(plaintext, decrypt=False)

    def unprotect(self, ciphertext: bytes) -> bytes:
        return self._transform(ciphertext, decrypt=True)


class SecretStore:
    def __init__(
        self,
        path: str | Path,
        *,
        protector: SecretProtector | None = None,
    ):
        self.path = Path(path)
        self._protector = protector

    def _get_protector(self) -> SecretProtector:
        if self._protector is None:
            self._protector = DPAPIProtector()
        return self._protector

    def _read_all(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            ciphertext = self.path.read_bytes()
            plaintext = self._get_protector().unprotect(ciphertext)
            payload = json.loads(plaintext.decode("utf-8"))
        except SecretStoreUnavailable:
            raise
        except Exception:
            raise SecretStoreCorrupted("运行时密钥文件无法解密或解析") from None
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise SecretStoreCorrupted("运行时密钥文件格式不受支持")
        secrets = payload.get("secrets")
        if not isinstance(secrets, dict) or any(
            name not in ALLOWED_SECRET_NAMES
            or not isinstance(value, str)
            or not value.strip()
            or len(value) > 8192
            for name, value in secrets.items()
        ):
            raise SecretStoreCorrupted("运行时密钥文件字段不合法")
        return secrets

    def _write_all(self, secrets: dict[str, str]) -> None:
        raw = json.dumps(
            {"version": 1, "secrets": secrets},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        ciphertext = self._get_protector().protect(raw)
        if not ciphertext:
            raise SecretStoreUnavailable("DPAPI 返回了空密文")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            with temp_path.open("xb") as handle:
                handle.write(ciphertext)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    @staticmethod
    def _validate_name(name: str) -> None:
        if name not in ALLOWED_SECRET_NAMES:
            raise ValueError(f"不允许保存的密钥名称: {name}")

    def get_secret(self, name: str) -> str | None:
        self._validate_name(name)
        return self._read_all().get(name)

    def set_secret(self, name: str, value: str) -> None:
        self._validate_name(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("密钥不能为空")
        clean = value.strip()
        if len(clean) > 8192:
            raise ValueError("密钥长度不能超过 8192 个字符")
        secrets = self._read_all()
        secrets[name] = clean
        self._write_all(secrets)

    def delete_secret(self, name: str) -> bool:
        self._validate_name(name)
        secrets = self._read_all()
        if name not in secrets:
            return False
        del secrets[name]
        if secrets:
            self._write_all(secrets)
        elif self.path.exists():
            self.path.unlink()
        return True
