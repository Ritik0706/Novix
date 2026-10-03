# Handles the checking and managing of the Ollama server that runs the AI model.

import logging
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


engine = InferenceEngine()