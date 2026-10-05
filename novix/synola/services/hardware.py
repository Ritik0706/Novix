"""Detect hardware and choose conservative inference settings."""

import logging
import shutil
import subprocess

import psutil

logger = logging.getLogger(__name__)

# Conservative estimates for model and runtime overhead beyond GGUF weights.
GPU_MEMORY_RESERVE_GB = 0.75
CPU_MEMORY_RESERVE_GB = 1.5
GPU_FIT_MARGIN = 0.85


def _available_system_ram_gb() -> float:
    return psutil.virtual_memory().available / (1024 ** 3)


def _estimate_model_ram_gb(model_path) -> float:
    try:
        size_bytes = model_path.stat().st_size
    except (OSError, AttributeError):
        return 0.0
    return size_bytes / (1024 ** 3)


def _estimate_gpu_layers(model_path, vram_gb: float, *, context_size: int = 4096) -> tuple[int, str]:
    if vram_gb <= 0:
        return 0, "cpu"

    model_gb = _estimate_model_ram_gb(model_path)
    if model_gb <= 0:
        return 0, "cpu"

    usable_vram = max(vram_gb - GPU_MEMORY_RESERVE_GB, 0.0)
    if model_gb <= usable_vram * GPU_FIT_MARGIN:
        return -1, "gpu"

    ratio = usable_vram / model_gb
    if ratio < 0.20:
        return 0, "cpu"
    if ratio < 0.40:
        return 8, "hybrid"
    if ratio < 0.60:
        return 16, "hybrid"
    if ratio < 0.80:
        return 24, "hybrid"
    return 32, "hybrid"


def pick_runtime_config(model_path=None, hardware=None):
    physical_cores = psutil.cpu_count(logical=False) or 4
    total_ram_gb = psutil.virtual_memory().total / (1024 ** 3)

    threads = max(physical_cores - 1, 1)
    ctx_size = 4096 if total_ram_gb >= 8 else 2048

    config = {
        "threads": threads,
        "ctx_size": ctx_size,
        "chat_template": "",
        "gpu_layers": 0,
        "fit_mode": "cpu",
    }

    if model_path is None or hardware is None:
        return config

    vram_gb = float(hardware.get("vram_gb") or 0)
    gpu_available = vram_gb > 0 and (
        hardware.get("cuda_available", False)
        or hardware.get("vulkan_available", False)
    )
    if not gpu_available:
        return config

    gpu_layers, fit_mode = _estimate_gpu_layers(
        model_path,
        vram_gb,
        context_size=ctx_size,
    )
    config["gpu_layers"] = gpu_layers
    config["fit_mode"] = fit_mode
    logger.info("Selected inference configuration: %s threads, %s context tokens", threads, ctx_size)
    return config

def get_hardware_profile() -> dict:
    cpu_cores = psutil.cpu_count(logical=False) or psutil.cpu_count() or 4
    ram_gb = round(psutil.virtual_memory().total / (1024**3), 1)
    vram_gb, gpu_name = _detect_gpu_vram()
    vulkan_available = bool(shutil.which("vulkaninfo"))
    return {
        "cpu_cores": cpu_cores,
        "ram_gb": ram_gb,
        "vram_gb": vram_gb,
        "gpu_name": gpu_name,
        "cuda_available": bool(gpu_name and shutil.which("nvidia-smi")),
        "vulkan_available": vulkan_available,
    }

def _detect_gpu_vram():
    if not shutil.which("nvidia-smi"):
        return 0, None
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3)
        if result.returncode == 0 and result.stdout.strip():
            name, mem_mb = result.stdout.strip().splitlines()[0].split(",", 1)
            return round(int(mem_mb.strip())/1024, 1), name.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, TypeError):
        pass
    return 0, None
