"""PyTorch 推理设备校验，禁止 CUDA 配置静默回退到 CPU。"""

from __future__ import annotations


def require_torch_device(device: str, component: str):
    """返回规范化 torch.device；请求 CUDA 时先验证设备确实可用。"""
    import torch

    try:
        requested = torch.device(device)
    except (RuntimeError, ValueError) as error:
        raise RuntimeError(f"{component} 推理设备配置无效: {device}") from error

    if requested.type == "cpu":
        return requested
    if requested.type != "cuda":
        raise RuntimeError(
            f"{component} 仅支持 cpu 或 cuda[:序号]，当前配置为 {device}"
        )
    if not torch.cuda.is_available():
        raise RuntimeError(
            f"{component} 配置为 {device}，但当前 PyTorch/CUDA 环境不可用；"
            "已拒绝静默回退到 CPU"
        )

    device_index = requested.index if requested.index is not None else 0
    if device_index < 0 or device_index >= torch.cuda.device_count():
        raise RuntimeError(
            f"{component} 配置为 {device}，但仅检测到 "
            f"{torch.cuda.device_count()} 块 CUDA 设备"
        )
    return requested


def assert_model_uses_device(model, device: str, component: str) -> str:
    """CUDA 推理后核对模型参数所在设备，返回实际设备名称。"""
    requested = require_torch_device(device, component)
    if requested.type == "cpu":
        return "cpu"

    actual = getattr(model, "device", None)
    if actual is None:
        try:
            actual = next(model.parameters()).device
        except (AttributeError, StopIteration, TypeError) as error:
            raise RuntimeError(f"无法确认 {component} 模型的实际推理设备") from error

    import torch

    actual = torch.device(actual)
    expected_index = requested.index if requested.index is not None else 0
    actual_index = actual.index if actual.index is not None else 0
    if actual.type != "cuda" or actual_index != expected_index:
        raise RuntimeError(
            f"{component} 请求设备 {device}，实际模型位于 {actual}；"
            "已拒绝继续使用静默回退结果"
        )
    return str(actual)
