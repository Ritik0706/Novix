#handles the swapping of the AI model currently in use

import logging
import os
import shutil
import threading
from functools import wraps
from django.conf import settings
from django.db import transaction
from synola.models import InstalledModel
from .gguf_inspector import inspect_gguf
from .inference_engine import engine

logger = logging.getLogger(__name__)

MODEL_DIR = settings.DATA_DIR / "models"
INSTALLED_DIR = MODEL_DIR / "installed"
ACTIVE_PATH = MODEL_DIR / "active_model.gguf"
STAGING_PATH = MODEL_DIR / "active_model.gguf.new"
BACKUP_PATH = MODEL_DIR / "active_model.gguf.backup"
MODEL_LOCK = threading.Lock()


def _serialized_operation(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with MODEL_LOCK:
            return function(*args, **kwargs)
    return wrapped


def _normalize_metadata(metadata: dict, staged_path) -> dict:
    metadata = dict(metadata or {})
    if not metadata.get("architecture") or not metadata.get("quantization"):
        try:
            inspected = inspect_gguf(staged_path)
        except Exception:
            inspected = {}
        for key, value in inspected.items():
            if key == "capabilities":
                metadata.setdefault("capabilities", {})
                metadata["capabilities"].update(value)
                continue
            metadata.setdefault(key, value)
    return metadata


@_serialized_operation
def swap_model(staged_path, metadata: dict):
    """Replace the active model and restore it if the candidate cannot start."""
    staged_path = os.fspath(staged_path)
    if not os.path.isfile(staged_path) or os.path.getsize(staged_path) == 0:
        raise RuntimeError("Staged model is missing or empty")

    metadata = _normalize_metadata(metadata, staged_path)

    required_metadata = {"display_name", "source_url", "size_bytes"}
    missing_metadata = required_metadata - metadata.keys()
    if missing_metadata:
        raise ValueError(f"Missing model metadata: {', '.join(sorted(missing_metadata))}")

    logger.info("Starting model swap from %s", staged_path)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    INSTALLED_DIR.mkdir(parents=True, exist_ok=True)
    library_name = metadata.get("sha_256") or os.path.basename(staged_path)
    library_path = INSTALLED_DIR / f"{library_name}.gguf"
    had_active_model = ACTIVE_PATH.exists()
    if BACKUP_PATH.exists():
        BACKUP_PATH.unlink()

    engine.stop()
    try:
        if had_active_model:
            os.replace(ACTIVE_PATH, BACKUP_PATH)
        os.replace(staged_path, library_path)
        shutil.copy2(library_path, ACTIVE_PATH)
        engine.start(ACTIVE_PATH)

        try:
            metadata.update(inspect_gguf(staged_path))
        except Exception:
            pass

        with transaction.atomic():
            InstalledModel.objects.filter(is_active=True).update(is_active=False)
            lookup = {"sha_256": metadata["sha_256"]} if metadata.get("sha_256") else {
                "source_url": metadata["source_url"]
            }
            InstalledModel.objects.update_or_create(
                **lookup,
                defaults={
                    **metadata,
                    "local_path": str(library_path),
                    "is_active": True,
                },
            )
        if BACKUP_PATH.exists():
            try:
                BACKUP_PATH.unlink()
            except OSError:
                logger.warning("Could not remove old model backup at %s", BACKUP_PATH)
    except Exception:
        logger.exception("Model swap failed; restoring previous model")
        engine.stop()
        if ACTIVE_PATH.exists():
            ACTIVE_PATH.unlink()
        if had_active_model and BACKUP_PATH.exists():
            os.replace(BACKUP_PATH, ACTIVE_PATH)
        if had_active_model:
            engine.start(ACTIVE_PATH)
        raise

    logger.info("Model swap completed; active model is %s", ACTIVE_PATH)


@_serialized_operation
def activate_installed_model(model_id):
    """Activate a previously installed model without deleting its library file."""
    model = InstalledModel.objects.get(pk=model_id)
    candidate_path = model.local_path
    if not candidate_path or not os.path.isfile(candidate_path):
        raise RuntimeError(f"Installed model file is missing: {candidate_path or model.display_name}")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    had_active_model = ACTIVE_PATH.exists()
    if BACKUP_PATH.exists():
        BACKUP_PATH.unlink()

    engine.stop()
    try:
        if had_active_model:
            os.replace(ACTIVE_PATH, BACKUP_PATH)
        shutil.copy2(candidate_path, ACTIVE_PATH)
        engine.start(ACTIVE_PATH)
        with transaction.atomic():
            InstalledModel.objects.filter(is_active=True).update(is_active=False)
            model.is_active = True
            model.save(update_fields=["is_active", "installed_at"])
        if BACKUP_PATH.exists():
            BACKUP_PATH.unlink()
    except Exception:
        logger.exception("Installed model activation failed; restoring previous model")
        engine.stop()
        if ACTIVE_PATH.exists():
            ACTIVE_PATH.unlink()
        if had_active_model and BACKUP_PATH.exists():
            os.replace(BACKUP_PATH, ACTIVE_PATH)
        if had_active_model:
            engine.start(ACTIVE_PATH)
        raise

    logger.info("Activated installed model %s", model.display_name)


@_serialized_operation
def remove_active_model():
    """Stop inference and remove the active model and its installed library copy."""
    model = InstalledModel.objects.filter(is_active=True).first()
    if model is None:
        return None

    library_path = os.fspath(model.local_path) if model.local_path else None
    active_backup = ACTIVE_PATH.with_name(f"{ACTIVE_PATH.name}.{model.pk}.remove-backup")
    library_backup = (
        os.path.join(
            os.path.dirname(library_path),
            f"{os.path.basename(library_path)}.{model.pk}.remove-backup",
        )
        if library_path else None
    )
    if active_backup.exists() or (library_backup and os.path.exists(library_backup)):
        raise RuntimeError("A previous model removal needs recovery")

    had_active_file = ACTIVE_PATH.exists()
    had_library_file = bool(library_path and os.path.exists(library_path))
    engine.stop()
    try:
        if had_active_file:
            os.replace(ACTIVE_PATH, active_backup)
        if had_library_file:
            os.replace(library_path, library_backup)
        model.delete()
    except Exception:
        if library_backup and os.path.exists(library_backup):
            os.replace(library_backup, library_path)
        if active_backup.exists():
            os.replace(active_backup, ACTIVE_PATH)
        if had_active_file and ACTIVE_PATH.exists():
            engine.start(ACTIVE_PATH)
        raise

    for backup in (active_backup, library_backup):
        if backup and os.path.exists(backup):
            try:
                os.remove(backup)
            except OSError:
                logger.warning("Could not remove model-removal backup at %s", backup)

    logger.info("Removed active model %s", model.display_name)
    return model