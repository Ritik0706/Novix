# Handles the checking and managing of the Ollama server that runs the AI model.

import logging
<<<<<<< HEAD
import os
import shutil
import subprocess
import time

import requests

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://127.0.0.1:11434"
MODEL = "qwen3:1.7b"
START_TIMEOUT = 45


def find_ollama_executable():
    """Locate the Ollama command-line executable across common install paths."""
    configured = os.environ.get("NOVIX_OLLAMA_EXECUTABLE")
    if configured and os.path.isfile(configured):
        return configured

    candidates = []
    for env_var in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(env_var)
        if base:
            candidates.append(os.path.join(base, "Ollama", "ollama.exe"))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate

    portable = os.environ.get("NOVIX_OLLAMA_HOME")
    if portable:
        portable_exe = os.path.join(portable, "ollama.exe")
        if os.path.isfile(portable_exe):
            return portable_exe

    return shutil.which("ollama")


class InferenceEngine:
    def __init__(self):
        self.ollama_process = None

    def start(self, model_path=None):
        if not self.is_healthy():
            logger.info("Ollama server is not running; attempting to start it")
            self._start_ollama_server()

        if not self.model_available():
            logger.info("Model %s is missing; downloading it", MODEL)
            self._pull_model()

        if not self.is_healthy():
            raise RuntimeError("Ollama server is not running")
        if not self.model_available():
            raise RuntimeError(f"Model '{MODEL}' is not installed in Ollama")

        logger.info("Ollama server is ready with model %s", MODEL)

    def stop(self):
        if self.ollama_process is not None and self.ollama_process.poll() is None:
            logger.info("Stopping the Ollama server started by this application")
            self.ollama_process.terminate()
            try:
                self.ollama_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.ollama_process.kill()
        self.ollama_process = None

    def is_healthy(self, timeout=2):
        try:
            return requests.get(
                f"{OLLAMA_URL}/api/tags", timeout=timeout
            ).status_code == 200
        except requests.RequestException:
            return False

    def model_available(self):
        try:
            response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
            if response.status_code != 200:
                return False
            names = {model.get("name") for model in response.json().get("models", [])}
            return MODEL in names
        except (requests.RequestException, ValueError):
            logger.error("Failed to query Ollama model list")
            return False

    def _start_ollama_server(self):
        ollama_path = find_ollama_executable()
        if not ollama_path:
            raise RuntimeError(
                "Ollama is not installed. Install Ollama from https://ollama.com "
                "and try again."
            )
        logger.info("Starting Ollama server from %s", ollama_path)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.ollama_process = subprocess.Popen(
            [ollama_path, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        if not self._wait_healthy():
            raise RuntimeError("Ollama server did not start in time")

    def _pull_model(self):
        ollama_path = find_ollama_executable()
        if not ollama_path:
            raise RuntimeError(
                "Ollama executable not found; cannot download the required model"
            )
        logger.info("Downloading the required model %s", MODEL)
        try:
            result = subprocess.run(
                [ollama_path, "pull", MODEL],
                capture_output=True,
                text=True,
                timeout=1200,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Downloading model {MODEL} timed out")
        if result.returncode != 0:
            logger.error("ollama pull failed: %s", result.stderr.strip())
            raise RuntimeError(f"Failed to download model {MODEL}")

    def _wait_healthy(self, timeout=START_TIMEOUT):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_healthy():
                return True
            time.sleep(1)
        return False
=======
import subprocess, time, requests
from django.conf import settings
from synola.models import InstalledModel
from .hardware import get_hardware_profile, pick_runtime_config
from .runtime_manager import build_launch_plan

logger = logging.getLogger(__name__)

ACTIVE_PATH = settings.DATA_DIR / "models" / "active_model.gguf"
PORT = 8090

class InferenceEngine:
    def __init__(self):
        self.process = None
        self._log_file = None

    @staticmethod
    def _chat_template_for_model(model_path):
        model = InstalledModel.objects.filter(is_active=True).first()
        if model and model.chat_template:
            return model.chat_template
        return ""

    def start(self, model_path=None):
        model_path = model_path or ACTIVE_PATH
        logger.info("Starting inference engine with model %s", model_path)
        if self.process and self.process.poll() is None:
            logger.info("Inference engine is already running")
            return
        if not model_path.exists():
            logger.error("Model was not found at %s", model_path)
            raise RuntimeError("No model installed, run first-time setup")
        hardware = get_hardware_profile()
        cfg = pick_runtime_config(
            model_path=model_path,
            hardware=hardware,
        )
        cfg["chat_template"] = self._chat_template_for_model(model_path)
        plan = build_launch_plan(
            model_path,
            PORT,
            cfg,
            hardware,
        )
        logger.info("%s", plan.reason)
        logger.info("Starting %s runtime from %s", plan.runtime.name, plan.runtime.directory)
        log_path = settings.LOGS_DIR / "llama-server.log"
        self._log_file = log_path.open("a", encoding="utf-8")
        self.process = subprocess.Popen(
            plan.command,
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            env=plan.environment,
            text=True,
        )
        logger.info("llama-server process started with PID %s", self.process.pid)
        try:
            self._wait_healthy(plan.runtime.name)
        except Exception:
            self.stop()
            raise

    def stop(self):
        if self.process:
            logger.info("Stopping llama-server process")
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("llama-server did not stop gracefully; terminating it")
                self.process.kill()
                self.process.wait()
            self.process = None
            if self._log_file:
                self._log_file.close()
                self._log_file = None
            logger.info("Inference engine stopped")

    def _wait_healthy(self, runtime_name, timeout=120):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process and self.process.poll() is not None:
                log_path = settings.LOGS_DIR / "llama-server.log"
                raise RuntimeError(
                    f"llama-server exited with code {self.process.returncode}; "
                    f"see {log_path}. Runtime: {runtime_name}"
                )
            try:
                response = requests.get(f"http://127.0.0.1:{PORT}/health", timeout=1)
                if response.status_code == 200:
                    logger.info("Inference engine is healthy")
                    return
                logger.debug("Inference engine health returned HTTP %s", response.status_code)
            except requests.RequestException:
                pass
            time.sleep(0.5)

        logger.error("Inference engine did not become healthy within %s seconds", timeout)
        raise RuntimeError(
            f"llama server did not go healthy in {timeout} seconds; "
            f"see {settings.LOGS_DIR / 'llama-server.log'}"
        )

>>>>>>> 6288dcb1db8e91970a586d3ff54fe3ec1d64ca3b


engine = InferenceEngine()