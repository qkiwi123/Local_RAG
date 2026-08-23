"""GPU 设备配置与静默回退防护测试。"""

from types import SimpleNamespace

import pytest
import torch

from backend.config import _is_supported_torch_device
from backend.services.torch_device import (
    assert_model_uses_device,
    require_torch_device,
)


def test_supported_torch_device_formats():
    assert _is_supported_torch_device("cpu")
    assert _is_supported_torch_device("cuda")
    assert _is_supported_torch_device("cuda:0")
    assert not _is_supported_torch_device("cuda:-1")
    assert not _is_supported_torch_device("auto")


def test_cuda_request_fails_instead_of_falling_back(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

    with pytest.raises(RuntimeError, match="拒绝静默回退到 CPU"):
        require_torch_device("cuda:0", "Embedding")


def test_cuda_request_checks_actual_model_device(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 1)
    model = SimpleNamespace(device=torch.device("cpu"))

    with pytest.raises(RuntimeError, match="实际模型位于 cpu"):
        assert_model_uses_device(model, "cuda:0", "Embedding")
