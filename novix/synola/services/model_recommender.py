# synola/services/model_recommender.py
import json
import re
import logging
from pathlib import Path
from urllib.parse import urlparse
from synola.services.gguf_inspector import inspect_gguf
from synola.services.model_downloader import download_model
from synola.services.model_manager import STAGING_PATH, swap_model

logger = logging.getLogger(__name__)
CATALOG_PATH = Path(__file__).resolve().parent / "models.json"


def install_recommended_model(suggestion: dict, progress_cb=None):
    m = suggestion["model"]
    if not m.get("download_url") or not m.get("size_bytes"):
        raise RuntimeError("The recommended model has no verified download metadata")
    download_model(m["download_url"], STAGING_PATH, expected_sha_256=m.get("sha_256") or None,
                    expected_size=m["size_bytes"], progress_cb=progress_cb, max_retries=3)
    if progress_cb:
        progress_cb(m["size_bytes"], m["size_bytes"], "finishing")
    inspected = inspect_gguf(STAGING_PATH)
    if progress_cb:
        progress_cb(m["size_bytes"], m["size_bytes"], "finishing")
    metadata = {
        "display_name": m["model"],
        "source_url": m["download_url"],
        "sha_256": m.get("sha_256", ""),
        "size_bytes": m["size_bytes"],
        "context_length": int(re.findall(r"\d+", m["context_length"])[0]) if m.get("context_length") else inspected.get("context_length", 4096),
        "chat_template": m.get("chat_template") or inspected.get("chat_template", ""),
        "architecture": m.get("architecture") or inspected.get("architecture", ""),
        "quantization": m.get("quantization") or inspected.get("quantization", ""),
        "capabilities": {"vision": bool(m.get("vision_support") or inspected.get("capabilities", {}).get("vision", False))},
    }
    swap_model(STAGING_PATH, metadata)

def _load_catalog():
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    device_classes = catalog.get("device_classes") or []
    if not isinstance(device_classes, list) or not device_classes:
        raise RuntimeError("Model recommendation catalog is empty or missing device classes")
    for device_class in device_classes:
        for model in device_class.get("recommended_models", []):
            _validate_model_metadata(model)
    return catalog


def _validate_model_metadata(model: dict):
    url = model.get("download_url", "")
    parsed = urlparse(url)
    if parsed.netloc != "huggingface.co" or not parsed.path.lower().endswith(".gguf"):
        raise ValueError(f"Model {model.get('model', 'unknown')} has no direct GGUF URL")
    if not isinstance(model.get("size_bytes"), int) or model["size_bytes"] <= 0:
        raise ValueError(f"Model {model.get('model', 'unknown')} has an invalid size")
    checksum = model.get("sha_256", "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
        raise ValueError(f"Model {model.get('model', 'unknown')} has an invalid SHA-256")


def _parse_gb(value: str) -> float:
    numbers = re.findall(r"[\d.]+", value or "")
    return float(numbers[-1]) if numbers else 0.0


def _fit_mode(model: dict, profile: dict) -> str | None:
    ram_limit = profile["ram_gb"] * 0.7
    ram_required = _parse_gb(model.get("ram_footprint"))
    if not ram_required or ram_required > ram_limit:
        return None

    vram_gb = profile.get("vram_gb", 0) or 0
    if not vram_gb:
        return "cpu"

    vram_required = _parse_gb(model.get("vram_footprint"))
    if vram_required and vram_required <= vram_gb * 0.9:
        return "gpu"
    return "hybrid"


def _classify_device(profile: dict, device_classes: list) -> dict:
    desktop_classes = [
        dc for dc in device_classes
        if any(k in dc["device_class"].lower() for k in ("laptop", "desktop", "workstation"))
    ]
    if not desktop_classes:
        raise RuntimeError("No desktop device classes are configured")
    best = desktop_classes[0]
    for dc in desktop_classes:
        if profile["ram_gb"] >= dc["ram_gb"]:
            best = dc
    return best

def recommend_model(profile: dict) -> dict:
    catalog = _load_catalog()
    device_classes = catalog.get("device_classes") or []
    if not device_classes:
        raise RuntimeError("No device classes configured in model catalog")
    device_class = _classify_device(profile, device_classes)

    all_candidates = [
        model for dc in device_classes for model in dc["recommended_models"]
    ]
    fitting = [model for model in all_candidates if _fit_mode(model, profile)]

    if not fitting:
        logger.warning("No installable model fit profile %s", profile)
        raise RuntimeError("No models available in catalog")

    chosen = max(fitting, key=lambda m: _parse_gb(m["ram_footprint"]))
    fit_mode = _fit_mode(chosen, profile)
    return {
        "device_class": device_class["device_class"],
        "model": chosen,
        "installable": _is_installable(chosen),
        "fit_mode": fit_mode,
        "reason": _format_fit_reason(profile, fit_mode),
    }


def get_model_options(profile: dict, free_bytes: int) -> list[dict]:
    """Return runnable catalog models for this device, ordered best to smallest."""
    catalog = _load_catalog()
    device_class = _classify_device(profile, catalog.get("device_classes") or [])
    recommendation = recommend_model(profile)
    recommended_id = recommendation["model"].get("sha_256")
    options = []

    catalog_models = [
        model
        for dc in (device_class, *[item for item in catalog["device_classes"] if item is not device_class])
        for model in dc["recommended_models"]
    ]
    seen_ids = set()
    for model in catalog_models:
        model_id = model["sha_256"]
        if model_id in seen_ids:
            continue

        fit_mode = _fit_mode(model, profile)
        if not fit_mode:
            continue
        seen_ids.add(model_id)
        size_bytes = model["size_bytes"]
        options.append({
            "id": model_id,
            "display_name": model["model"],
            "description": model.get("rationale", ""),
            "size_bytes": size_bytes,
            "quantization": model.get("quantization", ""),
            "context_length": model.get("context_length", ""),
            "speed": model.get("tokens_per_second", ""),
            "fit_mode": fit_mode,
            "vision_support": bool(model.get("vision_support", False)),
            "recommended": False,
            "installable": _is_installable(model) and size_bytes * 2 <= free_bytes,
        })

    recommended = next(
        (option for option in options
         if option["id"] == recommended_id and option["installable"]),
        None,
    )
    if recommended is None:
        recommended = next(
            (option for option in options if option["installable"]),
            next((option for option in options if option["id"] == recommended_id), None),
        )
    if recommended:
        recommended["recommended"] = True
    options.sort(
        key=lambda option: (
            not option["installable"],
            not option["recommended"],
            -option["size_bytes"],
        )
    )
    return options


def get_installable_model(model_id: str, profile: dict, free_bytes: int) -> dict | None:
    """Resolve a catalog ID only when its model still fits and has enough disk space."""
    catalog = _load_catalog()
    device_class = _classify_device(profile, catalog.get("device_classes") or [])
    catalog_models = [
        model
        for dc in (device_class, *[item for item in catalog["device_classes"] if item is not device_class])
        for model in dc["recommended_models"]
    ]
    for model in catalog_models:
        if model.get("sha_256") == model_id:
            if not _fit_mode(model, profile):
                continue
            if not _is_installable(model) or model["size_bytes"] * 2 > free_bytes:
                return None
            return model
    return None


def _format_fit_reason(profile: dict, fit_mode: str) -> str:
    if fit_mode == "gpu":
        return f"Fits on GPU within {profile['vram_gb']}GB VRAM ({profile.get('gpu_name') or 'detected GPU'})"
    if fit_mode == "hybrid":
        return f"Fits with CPU+GPU hybrid offload using {profile['vram_gb']}GB VRAM"
    return f"Fits within {profile['ram_gb']}GB RAM (CPU-only)"


def _is_installable(model: dict) -> bool:
    return bool(model.get("download_url") and model.get("size_bytes"))