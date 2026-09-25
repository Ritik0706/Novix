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


def install_recommended_model(suggestion: dict):
    m = suggestion["model"]
    if not m.get("download_url") or not m.get("size_bytes"):
        raise RuntimeError("The recommended model has no verified download metadata")
    download_model(m["download_url"], STAGING_PATH, expected_sha_256=m.get("sha_256") or None,
                    expected_size=m["size_bytes"], max_retries=3)
    inspected = inspect_gguf(STAGING_PATH)
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
    for device_class in catalog.get("device_classes", []):
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
    device_class = _classify_device(profile, catalog["device_classes"])

    candidates = device_class["recommended_models"]
    fitting = [m for m in candidates if _fit_mode(m, profile)]

    if not fitting:
        logger.warning("No installable model fit profile %s", profile)
        all_candidates = [
            m for dc in catalog["device_classes"] for m in dc["recommended_models"]
        ]
        fitting = sorted(
            (m for m in all_candidates if _fit_mode(m, profile)),
            key=lambda m: _parse_gb(m.get("ram_footprint")),
        )[:1]

    if not fitting:
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


def _format_fit_reason(profile: dict, fit_mode: str) -> str:
    if fit_mode == "gpu":
        return f"Fits on GPU within {profile['vram_gb']}GB VRAM ({profile.get('gpu_name') or 'detected GPU'})"
    if fit_mode == "hybrid":
        return f"Fits with CPU+GPU hybrid offload using {profile['vram_gb']}GB VRAM"
    return f"Fits within {profile['ram_gb']}GB RAM (CPU-only)"


def _is_installable(model: dict) -> bool:
    return bool(model.get("download_url") and model.get("size_bytes"))