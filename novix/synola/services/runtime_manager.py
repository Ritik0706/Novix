from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
import shutil
import subprocess
import sys

from django.conf import settings


@dataclass(frozen=True)
class RuntimeDescriptor:
    name: str
    directory: Path
    executable: Path
    supports_gpu: bool

    @property
    def available(self):
        return self.executable.exists() and not self.missing_dependency_names()

    def missing_dependency_names(self):
        return find_missing_dependencies(self)


@dataclass(frozen=True)
class LaunchPlan:
    runtime: RuntimeDescriptor
    command: list[str]
    environment: dict[str, str]
    reason: str


class RuntimeValidationError(RuntimeError):
    """Raised when a llama.cpp runtime cannot be used."""


class RuntimeExecutableError(RuntimeValidationError):
    """Raised when llama-server cannot be executed."""


class RuntimeDependencyError(RuntimeValidationError):
    """Raised when llama-server has missing native dependencies."""


class RuntimeProbeError(RuntimeValidationError):
    """Raised when llama-server --help fails."""


def _runtime_label(runtime: RuntimeDescriptor) -> str:
    return (
        f"runtime={runtime.name!r}, "
        f"directory={str(runtime.directory)!r}, "
        f"executable={str(runtime.executable)!r}"
    )


def _probe_executable(runtime: RuntimeDescriptor) -> None:
    if not runtime.executable.is_file():
        raise RuntimeExecutableError(
            f"llama-server executable missing; "
            f"{_runtime_label(runtime)}"
        )

    try:
        result = subprocess.run(
            [str(runtime.executable), "--help"],
            cwd=str(runtime.directory),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except OSError as exc:
        raise RuntimeExecutableError(
            f"failed to execute llama-server; "
            f"{_runtime_label(runtime)}; "
            f"error={exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeProbeError(
            f"llama-server --help timed out; "
            f"{_runtime_label(runtime)}"
        ) from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        details = stderr or stdout or "<no process output>"
        raise RuntimeProbeError(
            f"llama-server --help failed; "
            f"{_runtime_label(runtime)}; "
            f"exit_code={result.returncode}; "
            f"output={details}"
        )


def find_missing_dependencies(runtime: RuntimeDescriptor) -> list[str]:
    if not runtime.executable.is_file():
        return [runtime.executable.name]

    try:
        import pefile
    except ImportError:
        return []

    try:
        binary = pefile.PE(str(runtime.executable), fast_load=True)
        binary.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]]
        )
    except (OSError, pefile.PEFormatError):
        return []

    missing = []
    for entry in getattr(binary, "DIRECTORY_ENTRY_IMPORT", []):
        name = entry.dll.decode(errors="replace")
        local_path = runtime.directory / name
        if not local_path.exists() and not shutil.which(name):
            missing.append(name)
    return missing


def probe_runtime(runtime, timeout=10):
    try:
        _probe_executable(runtime)
    except RuntimeValidationError as error:
        return str(error), None
    return None, 0


def resolve_runtime_root() -> Path:
    candidates = []

    if hasattr(sys, "_MEIPASS"):
        meipass = Path(sys._MEIPASS).resolve()
        for base in [meipass, *meipass.parents]:
            candidates.extend([
                base / "resources" / "llama",
                base / "llama",
            ])

    candidates.extend([
        settings.BASE_DIR / "resources" / "llama",
        settings.BASE_DIR / "llama",
    ])

    seen = set()
    for candidate in candidates:
        candidate = candidate.resolve(strict=False)
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_dir():
            return candidate

    return settings.BASE_DIR / "resources" / "llama"


def runtime_descriptors():
    root = resolve_runtime_root()
    return {
        "cpu": RuntimeDescriptor(
            "cpu",
            root / "cpu" / "llama-b10818-bin-win-cpu-x64",
            root / "cpu" / "llama-b10818-bin-win-cpu-x64" / "llama-server.exe",
            False,
        ),
        "cuda": RuntimeDescriptor(
            "cuda",
            root / "cuda",
            root / "cuda" / "llama-server.exe",
            True,
        ),
        "vulkan": RuntimeDescriptor(
            "vulkan",
            root / "vulkan",
            root / "vulkan" / "llama-server.exe",
            True,
        ),
    }


def runtime_status(hardware):
    return {
        name: validate_runtime_pack(runtime)
        for name, runtime in runtime_descriptors().items()
    }


@lru_cache(maxsize=None)
def validate_runtime(runtime: RuntimeDescriptor) -> dict:
    result = {
        "name": runtime.name,
        "directory": str(runtime.directory),
        "executable": str(runtime.executable),
        "supports_gpu": runtime.supports_gpu,
        "valid": False,
        "missing_dependencies": [],
        "probe_ok": False,
        "error": None,
    }

    if not runtime.directory.is_dir():
        result["error"] = (
            f"runtime directory does not exist: {runtime.directory}; "
            f"expected executable: {runtime.executable}"
        )
        return result

    if not runtime.executable.is_file():
        result["error"] = f"llama-server.exe does not exist: {runtime.executable}"
        return result

    missing = find_missing_dependencies(runtime)
    if missing:
        result["missing_dependencies"] = sorted(missing)
        result["error"] = f"missing runtime DLLs: {', '.join(sorted(missing))}"
        return result

    try:
        _probe_executable(runtime)
    except RuntimeValidationError as exc:
        result["error"] = str(exc)
        return result

    result["probe_ok"] = True
    result["valid"] = True
    return result


def validate_runtime_pack(runtime):
    validation = validate_runtime(runtime)
    return {
        "available": validation["valid"],
        "directory": validation["directory"],
        "executable": validation["executable"],
        "supports_gpu": validation["supports_gpu"],
        "missing_dependencies": validation["missing_dependencies"],
        "probe_error": None if validation["probe_ok"] else validation["error"],
        "probe_exit_code": None,
    }


def preflight_runtime(runtime: RuntimeDescriptor) -> RuntimeDescriptor:
    validation = validate_runtime(runtime)
    if not validation["valid"]:
        raise _runtime_validation_error(runtime, validation)
    return runtime


def _runtime_validation_error(runtime, validation):
    if not runtime.executable.exists():
        return RuntimeExecutableError(
            f"The {runtime.name} runtime executable was not found: "
            f"{runtime.executable}"
        )

    if validation["missing_dependencies"]:
        return RuntimeDependencyError(
            f"The {runtime.name} runtime is missing native dependencies: "
            f"{', '.join(validation['missing_dependencies'])}"
        )

    if validation["probe_error"]:
        return RuntimeProbeError(
            f"The {runtime.name} runtime failed its --help probe at "
            f"{runtime.directory}: {validation['probe_error']}"
        )

    return RuntimeValidationError(
        f"The {runtime.name} runtime is not usable at {runtime.directory}"
    )


def select_runtime(hardware, requested=None):
    runtimes = runtime_descriptors()
    requested = requested or os.environ.get("NOVIX_RUNTIME")

    if requested:
        requested = requested.lower()

        if requested == "auto":
            requested = None
        else:
            if requested not in runtimes:
                raise RuntimeError(f"unknown runtime requested: {requested}")

            runtime = runtimes[requested]
            validation = validate_runtime(runtime)
            if not validation["valid"]:
                raise _runtime_validation_error(runtime, validation)

            if requested == "cuda" and not hardware.get("cuda_available", False):
                raise RuntimeValidationError(
                    "CUDA runtime was explicitly requested, "
                    "but CUDA hardware/driver support is unavailable"
                )

            if requested == "vulkan" and not hardware.get("vulkan_available", False):
                raise RuntimeValidationError(
                    "Vulkan runtime was explicitly requested, "
                    "but Vulkan hardware/driver support is unavailable"
                )

            return runtime, f"forced runtime: {requested}"

    cuda = runtimes["cuda"]
    if hardware.get("cuda_available"):
        validation = validate_runtime(cuda)
        if validation["valid"]:
            return cuda, "CUDA runtime selected"

    vulkan = runtimes["vulkan"]
    if hardware.get("vulkan_available"):
        validation = validate_runtime(vulkan)
        if validation["valid"]:
            return vulkan, "Vulkan runtime selected"

    cpu = runtimes["cpu"]
    validation = validate_runtime(cpu)
    if validation["valid"]:
        return cpu, "CPU fallback selected"

    raise RuntimeValidationError(
        "no usable llama.cpp runtime found; "
        f"CPU error: {validation['error']}"
    )


def build_launch_plan(model_path, port, config, hardware, requested=None):
    runtime, reason = select_runtime(hardware, requested)
    command = [
        str(runtime.executable),
        "-m", str(model_path),
        "--host", "127.0.0.1",
        "--port", str(port),
        "-t", str(config["threads"]),
        "-c", str(config["ctx_size"]),
    ]
    if config.get("chat_template"):
        command.extend([
            "--chat-template",
            config["chat_template"],
        ])

    if runtime.supports_gpu:
        gpu_layers = int(config.get("gpu_layers", 0))

        if gpu_layers != 0:
            command.extend([
                "-ngl",
                str(gpu_layers),
            ])

    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join(
        [str(runtime.directory), environment.get("PATH", "")]
    )
    return LaunchPlan(runtime, command, environment, reason)