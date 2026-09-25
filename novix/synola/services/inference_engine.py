#it handles the checking, and running of llama related stuff.

import logging
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



engine = InferenceEngine()