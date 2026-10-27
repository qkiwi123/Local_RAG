"""Portable-only local ZIP inspection and independent update worker dispatch."""
from pathlib import Path
import asyncio
import uuid

from fastapi import APIRouter, HTTPException, UploadFile, File
from backend.config import settings
from launcher import updater

router = APIRouter(tags=["local-update"])


def installation() -> Path:
    if settings.DISTRIBUTION_MODE != "portable":
        raise HTTPException(status_code=409, detail="本地更新用于便携版；源码目录请使用开发更新流程")
    return Path(__file__).resolve().parents[2]


def translate(error):
    return HTTPException(status_code=409, detail=str(error))


@router.get("/local-update/status")
async def status():
    if settings.DISTRIBUTION_MODE != "portable":
        return {"available": False, "message": "本地更新包适用于便携版，源码运行不替换当前工作目录。"}
    root = installation()
    try:
        marker = updater.safe_path(root, "state/update-latest.json")
        job = None
        if marker.exists():
            job = updater.read_json(updater.job_path(root, updater.read_json(marker)["id"]) / "journal.json")
        release = updater.read_json(updater.safe_path(root, "release-manifest.json"))
        return {"available": True, "version": release.get("application_version", release.get("source_commit", "便携预览版")[:12]),
                "job": public_job(job) if job else None}
    except (OSError, ValueError, KeyError) as error:
        raise translate(error)


def public_job(state: dict) -> dict:
    return {name: state[name] for name in ("id", "phase", "message", "version", "notes", "files", "bytes", "error", "recovery_error") if name in state}


@router.post("/local-update/inspect")
async def inspect(file: UploadFile = File(...)):
    root = installation()
    incoming = updater.safe_path(root, "state/update-incoming")
    incoming.mkdir(parents=True, exist_ok=True)
    temporary = incoming / (uuid.uuid4().hex + ".zip")
    size = 0
    try:
        with temporary.open("xb") as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > updater.MAX_ZIP_BYTES:
                    raise ValueError("文件过大，请选择应用更新包ZIP")
                stream.write(chunk)
        state = await asyncio.to_thread(updater.stage, root, temporary)
        return public_job(state)
    except Exception as error:
        raise translate(error)
    finally:
        temporary.unlink(missing_ok=True)
        await file.close()


@router.post("/local-update/{job_id}/apply")
async def apply(job_id: str):
    root = installation()
    try:
        state = await asyncio.to_thread(updater.queue, root, job_id)
        return public_job(state)
    except Exception as error:
        raise translate(error)
