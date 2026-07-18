"""Resolve Hugging Face model IDs to local snapshots without network probes."""


def resolve_cached_snapshot(model_id: str) -> str:
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    try:
        return snapshot_download(repo_id=model_id, local_files_only=True)
    except LocalEntryNotFoundError as exc:
        raise RuntimeError(
            f"模型未在本地 Hugging Face 缓存中：{model_id}。请先下载模型。"
        ) from exc
