from pathlib import Path
import os
import subprocess
import sys
from tempfile import TemporaryDirectory

from django.test import Client, TestCase
from unittest.mock import Mock, patch
from types import SimpleNamespace

from synola.models import Conversation, ConversationSummary, InstalledModel, Message
from synola.services import ai
from synola.services.context_manager import build_context
from synola.services.inference_engine import InferenceEngine
from synola.services.model_recommender import _fit_mode, _load_catalog
from synola.services.runtime_manager import (
    RuntimeDependencyError,
    RuntimeExecutableError,
    RuntimeProbeError,
    RuntimeDescriptor,
    _probe_executable,
    build_launch_plan,
    preflight_runtime,
    select_runtime,
    validate_runtime,
    runtime_descriptors,
)
from synola.services.hardware import (
    _detect_gpu_vram,
    _estimate_gpu_layers,
    pick_runtime_config,
)


class BuildContextTests(TestCase):
    def test_build_context_includes_summary_without_calling_relation(self):
        conversation = Conversation.objects.create(title="Test chat")
        Message.objects.create(conversation=conversation, role="user", content="hello")
        Message.objects.create(conversation=conversation, role="assistant", content="hi there")
        ConversationSummary.objects.create(
            conversation=conversation,
            content="Remember the user likes cats.",
        )

        context = build_context(conversation)

        self.assertEqual(context[0]["role"], "system")
        self.assertIn("Remember the user likes cats.", context[0]["content"])
        self.assertEqual(context[1]["role"], "user")
        self.assertEqual(context[2]["role"], "assistant")


class ChatCompletionTests(TestCase):
    @patch("synola.services.ai.requests.post")
    def test_generate_uses_chat_completions_endpoint(self, post):
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "choices": [{"message": {"content": "A coherent reply."}}]
        }
        post.return_value = response

        reply = ai.generate([{"role": "user", "content": "Hello"}])

        self.assertEqual(reply, "A coherent reply.")
        post.assert_called_once()
        endpoint, kwargs = post.call_args
        self.assertEqual(endpoint[0], "http://127.0.0.1:11434/v1/chat/completions")
        self.assertEqual(kwargs["json"]["model"], "llama3.2:1b-instruct-q4_0")
        self.assertFalse(kwargs["json"]["stream"])
        self.assertEqual(kwargs["json"]["messages"][-1], {"role": "user", "content": "Hello"})
        self.assertEqual(kwargs["timeout"], 120)


class InferenceEngineTests(TestCase):
    @patch("synola.services.runtime_manager.probe_runtime")
    @patch("synola.services.inference_engine.get_hardware_profile")
    @patch("synola.services.inference_engine.requests.get")
    @patch("synola.services.inference_engine.subprocess.Popen")
    @patch("synola.services.inference_engine.pick_runtime_config")
    @patch("synola.services.inference_engine.ACTIVE_PATH")
    @patch("synola.services.inference_engine.build_launch_plan")
    def test_start_waits_for_health_and_stop_terminates_process(
        self,
        build_plan,
        active_path,
        runtime_config,
        popen,
        get,
        hardware_profile,
        probe_runtime,
    ):
        process = Mock()
        process.poll.return_value = None
        popen.return_value = process
        runtime_config.return_value = {"threads": 2, "ctx_size": 2048}
        hardware_profile.return_value = {"cuda_available": False, "vulkan_available": False}
        probe_runtime.return_value = (None, 0)
        active_path.exists.return_value = True
        get.return_value.status_code = 200
        build_plan.return_value = SimpleNamespace(
            command=["llama-server"],
            environment={},
            runtime=SimpleNamespace(name="cpu", directory="runtime"),
            reason="CPU fallback",
        )
        engine = InferenceEngine()

        engine.start()

        popen.assert_called_once()
        get.assert_called_once_with("http://127.0.0.1:8090/health", timeout=1)
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=10)

    @patch("synola.services.inference_engine.build_launch_plan")
    @patch("synola.services.inference_engine.requests.get")
    @patch("synola.services.inference_engine.subprocess.Popen")
    @patch("synola.services.inference_engine.pick_runtime_config")
    @patch("synola.services.inference_engine.get_hardware_profile")
    @patch("synola.services.inference_engine.ACTIVE_PATH")
    def test_start_uses_active_model_chat_template(
        self, active_path, hardware_profile, runtime_config, popen, get, build_plan
    ):
        InstalledModel.objects.create(
            display_name="Template model",
            source_url="https://huggingface.co/test/model.gguf",
            size_bytes=10,
            chat_template="chatml",
            is_active=True,
        )
        process = Mock()
        process.poll.return_value = None
        popen.return_value = process
        active_path.exists.return_value = True
        runtime_config.return_value = {"threads": 2, "ctx_size": 2048}
        hardware_profile.return_value = {"cuda_available": False, "vulkan_available": False}
        get.return_value.status_code = 200
        build_plan.return_value = SimpleNamespace(
            command=["llama-server"],
            environment={},
            runtime=SimpleNamespace(name="cpu", directory="runtime"),
            reason="Selected CPU fallback runtime",
        )

        InferenceEngine().start()

        runtime_config.assert_called_once_with(
            model_path=active_path,
            hardware=hardware_profile.return_value,
        )
        self.assertEqual(build_plan.call_args.args[1], 8090)
        self.assertEqual(build_plan.call_args.args[2]["chat_template"], "chatml")

    def test_wait_healthy_reports_runtime_when_process_exits(self):
        process = Mock()
        process.poll.return_value = 1
        process.returncode = 3221225781
        engine = InferenceEngine()
        engine.process = process

        with self.assertRaisesRegex(RuntimeError, "Runtime: cpu"):
            engine._wait_healthy("cpu")


class GGUFInspectorTests(TestCase):
    @patch("synola.services.gguf_inspector.GGUFReader")
    def test_inspect_gguf_extracts_metadata(self, reader_cls):
        class FakeField:
            def __init__(self, value):
                self.parts = [value]

        fake_reader = Mock()
        fake_reader.fields = {
            "general.architecture": FakeField("llama"),
            "general.file_type": FakeField(SimpleNamespace(item=lambda: 1)),
            "llama.context_length": FakeField(8192),
            "tokenizer.chat_template": FakeField("{{bos}}"),
        }
        reader_cls.return_value = fake_reader

        from synola.services.gguf_inspector import inspect_gguf

        result = inspect_gguf("/tmp/model.gguf")

        self.assertEqual(result["architecture"], "llama")
        self.assertEqual(result["quantization"], "F16")
        self.assertEqual(result["context_length"], 8192)
        self.assertEqual(result["chat_template"], "{{bos}}")
        self.assertFalse(result["capabilities"]["vision"])


class RuntimeSelectionTests(TestCase):
    def _probe_runtime(self):
        temporary_directory = TemporaryDirectory()
        directory = Path(temporary_directory.name)
        executable = directory / "llama-server.exe"
        executable.touch()
        self.addCleanup(temporary_directory.cleanup)
        return RuntimeDescriptor("cuda", directory, executable, True)

    def test_probe_reports_missing_executable(self):
        runtime = RuntimeDescriptor(
            "cuda",
            Path("missing-runtime"),
            Path("missing-runtime/llama-server.exe"),
            True,
        )

        with self.assertRaisesRegex(RuntimeExecutableError, "runtime='cuda'"):
            _probe_executable(runtime)

    @patch("synola.services.runtime_manager.subprocess.run")
    def test_probe_reports_process_output(self, run):
        runtime = self._probe_runtime()
        run.return_value = SimpleNamespace(
            returncode=3221225781,
            stderr="missing DLL",
            stdout="",
        )

        with self.assertRaisesRegex(RuntimeProbeError, "missing DLL"):
            _probe_executable(runtime)

    @patch("synola.services.runtime_manager.subprocess.run")
    def test_probe_reports_timeout(self, run):
        runtime = self._probe_runtime()
        run.side_effect = subprocess.TimeoutExpired("llama-server.exe", 15)

        with self.assertRaisesRegex(RuntimeProbeError, "timed out"):
            _probe_executable(runtime)

    @patch.object(RuntimeDescriptor, "missing_dependency_names", return_value=["z.dll", "a.dll"])
    def test_preflight_reports_sorted_missing_dlls(self, missing_dependencies):
        runtime = self._probe_runtime()

        with self.assertRaisesRegex(
            RuntimeDependencyError,
            r"runtime has missing DLL dependencies.*missing=a.dll, z.dll",
        ):
            preflight_runtime(runtime)

    def test_validate_runtime_reports_missing_directory(self):
        runtime = RuntimeDescriptor(
            "vulkan",
            Path("missing-runtime"),
            Path("missing-runtime/llama-server.exe"),
            True,
        )

        result = validate_runtime(runtime)

        self.assertFalse(result["valid"])
        self.assertFalse(result["probe_ok"])
        self.assertIn("runtime directory does not exist", result["error"])

    def test_cpu_runtime_does_not_receive_ngl(self):
        runtime = RuntimeDescriptor(
            "cpu",
            Path("cpu"),
            Path("cpu/llama-server.exe"),
            False,
        )

        with patch(
            "synola.services.runtime_manager.select_runtime",
            return_value=(runtime, "CPU"),
        ):
            plan = build_launch_plan(
                Path("model.gguf"),
                8081,
                {"threads": 4, "ctx_size": 2048, "gpu_layers": -1},
                {},
            )

        self.assertNotIn("-ngl", plan.command)

    def test_gpu_runtime_receives_full_offload(self):
        runtime = RuntimeDescriptor(
            "cuda",
            Path("cuda"),
            Path("cuda/llama-server.exe"),
            True,
        )

        with patch(
            "synola.services.runtime_manager.select_runtime",
            return_value=(runtime, "CUDA"),
        ):
            plan = build_launch_plan(
                Path("model.gguf"),
                8081,
                {"threads": 4, "ctx_size": 4096, "gpu_layers": -1},
                {},
            )

        self.assertIn("-ngl", plan.command)
        self.assertEqual(plan.command[plan.command.index("-ngl") + 1], "-1")

    def test_gpu_runtime_receives_hybrid_layers(self):
        runtime = RuntimeDescriptor(
            "cuda",
            Path("cuda"),
            Path("cuda/llama-server.exe"),
            True,
        )

        with patch(
            "synola.services.runtime_manager.select_runtime",
            return_value=(runtime, "CUDA"),
        ):
            plan = build_launch_plan(
                Path("model.gguf"),
                8081,
                {"threads": 4, "ctx_size": 4096, "gpu_layers": 16},
                {},
            )

        index = plan.command.index("-ngl")
        self.assertEqual(plan.command[index + 1], "16")

    def test_gpu_runtime_can_run_cpu_only(self):
        runtime = RuntimeDescriptor(
            "cuda",
            Path("cuda"),
            Path("cuda/llama-server.exe"),
            True,
        )

        with patch(
            "synola.services.runtime_manager.select_runtime",
            return_value=(runtime, "CUDA"),
        ):
            plan = build_launch_plan(
                Path("model.gguf"),
                8081,
                {"threads": 4, "ctx_size": 4096, "gpu_layers": 0},
                {},
            )

        self.assertNotIn("-ngl", plan.command)

    def test_auto_selection_falls_back_to_cpu_runtime(self):
        runtime, reason = select_runtime({"cuda_available": False, "vulkan_available": False})

        self.assertEqual(runtime.name, "cpu")
        self.assertIn("CPU fallback", reason)

    def test_packaged_runtime_root_is_resolved_from_meipass(self):
        with TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir) / "resources" / "llama"
            cpu_dir = runtime_root / "cpu" / "llama-b10818-bin-win-cpu-x64"
            cpu_dir.mkdir(parents=True)
            (cpu_dir / "llama-server.exe").touch()

            with patch("synola.services.runtime_manager.settings.BASE_DIR", Path("C:/missing/base")), \
                 patch.object(sys, "_MEIPASS", temp_dir, create=True):
                runtime = runtime_descriptors()["cpu"]

            self.assertEqual(runtime.directory, cpu_dir)
            self.assertTrue(runtime.executable.exists())

    def test_forced_unavailable_runtime_reports_install_path(self):
        cuda = runtime_descriptors()["cuda"]
        if cuda.available:
            self.skipTest("CUDA runtime is installed in this workspace")

        with self.assertRaises((RuntimeDependencyError, RuntimeExecutableError, RuntimeProbeError)):
            select_runtime({}, requested="cuda")


class RuntimeFallbackTests(TestCase):
    @patch("synola.services.runtime_manager.validate_runtime")
    def test_vulkan_fallback_when_cuda_invalid(self, validate_runtime):
        runtimes = runtime_descriptors()

        def validation(runtime):
            if runtime.name == "cuda":
                return {"valid": False, "error": "missing DLL"}
            if runtime.name == "vulkan":
                return {"valid": True, "error": None}
            return {"valid": True, "error": None}

        validate_runtime.side_effect = validation

        runtime, reason = select_runtime(
            {"cuda_available": False, "vulkan_available": True}
        )

        self.assertEqual(runtime.name, "vulkan")

    @patch("synola.services.runtime_manager.validate_runtime")
    def test_cpu_fallback_when_gpu_runtimes_invalid(self, validate_runtime):
        def validation(runtime):
            return {
                "valid": runtime.name == "cpu",
                "error": None if runtime.name == "cpu" else "runtime unavailable",
            }

        validate_runtime.side_effect = validation

        runtime, reason = select_runtime(
            {"cuda_available": False, "vulkan_available": False}
        )

        self.assertEqual(runtime.name, "cpu")


class RuntimeDiagnosticsTests(TestCase):
    def test_missing_executable_is_reported(self):
        runtime = RuntimeDescriptor(
            "cuda",
            Path("missing/cuda"),
            Path("missing/cuda/llama-server.exe"),
            True,
        )

        result = validate_runtime(runtime)

        self.assertFalse(result["valid"])
        self.assertIn("llama-server.exe", result["error"])

    @patch("synola.services.runtime_manager.subprocess.run")
    def test_failed_help_probe_is_reported(self, run):
        runtime_dir = Path("cuda")
        runtime = RuntimeDescriptor(
            "cuda",
            runtime_dir,
            runtime_dir / "llama-server.exe",
            True,
        )
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="DLL initialization failed",
        )

        with patch.object(Path, "is_file", return_value=True):
            with self.assertRaises(RuntimeProbeError) as context:
                _probe_executable(runtime)

        message = str(context.exception)
        self.assertIn("cuda", message)
        self.assertIn("exit_code=1", message)
        self.assertIn("DLL initialization failed", message)


class ModelFitTests(TestCase):
    def setUp(self):
        self.model = {
            "ram_footprint": "approx 6 GB",
            "vram_footprint": "approx 5 GB",
        }

    def test_cpu_profile_uses_cpu_mode(self):
        self.assertEqual(_fit_mode(self.model, {"ram_gb": 12, "vram_gb": 0}), "cpu")

    def test_gpu_profile_uses_gpu_mode_when_model_fits(self):
        self.assertEqual(_fit_mode(self.model, {"ram_gb": 12, "vram_gb": 8}), "gpu")

    def test_gpu_profile_uses_hybrid_mode_when_vram_is_limited(self):
        self.assertEqual(_fit_mode(self.model, {"ram_gb": 12, "vram_gb": 2}), "hybrid")

    def test_catalog_contains_verified_direct_gguf_artifacts(self):
        catalog = _load_catalog()
        models = [
            model
            for device_class in catalog["device_classes"]
            for model in device_class["recommended_models"]
        ]

        self.assertTrue(models)
        self.assertTrue(all(model["download_url"].lower().endswith(".gguf") for model in models))


class InstalledModelApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.model = InstalledModel.objects.create(
            display_name="Test model",
            source_url="https://huggingface.co/test/model.gguf",
            size_bytes=10,
            local_path="C:/models/test.gguf",
        )

    def test_lists_installed_models(self):
        response = self.client.get("/models/installed/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["models"][0]["display_name"], "Test model")

    @patch("synola.views.activate_installed_model")
    def test_activates_selected_model(self, activate):
        response = self.client.post(
            "/models/installed/",
            data={"id": self.model.id},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        activate.assert_called_once_with(self.model.id)

    def test_active_model_cannot_be_deleted(self):
        self.model.is_active = True
        self.model.save(update_fields=["is_active"])

        response = self.client.delete(f"/models/installed/{self.model.id}/")

        self.assertEqual(response.status_code, 409)
        self.assertTrue(InstalledModel.objects.filter(pk=self.model.id).exists())

    @patch("synola.views.os.remove")
    def test_deleting_inactive_model_removes_file_and_record(self, remove):
        response = self.client.delete(f"/models/installed/{self.model.id}/")

        self.assertEqual(response.status_code, 200)
        remove.assert_called_once_with("C:/models/test.gguf")
        self.assertFalse(InstalledModel.objects.filter(pk=self.model.id).exists())

    def test_reports_model_storage(self):
        response = self.client.get("/models/storage/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["installed_bytes"], 10)
        self.assertGreater(response.json()["free_bytes"], 0)
        self.assertGreater(response.json()["total_bytes"], 0)


class HardwareProbeTests(TestCase):
    @patch("synola.services.hardware.shutil.which", return_value=None)
    def test_missing_gpu_tools_return_cpu_capabilities(self, which):
        self.assertEqual(_detect_gpu_vram(), (0, None))

    @patch("synola.services.hardware.shutil.which", return_value="nvidia-smi")
    @patch("synola.services.hardware.subprocess.run")
    def test_nvidia_probe_uses_first_gpu_line(self, run, which):
        run.return_value.returncode = 0
        run.return_value.stdout = "GPU One, 8192\nGPU Two, 4096\n"

        self.assertEqual(_detect_gpu_vram(), (8.0, "GPU One"))

    @patch("synola.services.hardware._estimate_model_ram_gb", return_value=1.0)
    def test_small_model_uses_full_gpu_offload_when_vram_is_available(self, estimate):
        layers, fit_mode = _estimate_gpu_layers("model.gguf", 4.0)

        self.assertEqual((layers, fit_mode), (-1, "gpu"))

    @patch("synola.services.hardware._estimate_model_ram_gb", return_value=10.0)
    def test_large_model_uses_hybrid_offload_when_some_vram_is_available(self, estimate):
        layers, fit_mode = _estimate_gpu_layers("model.gguf", 8.0)

        self.assertEqual((layers, fit_mode), (24, "hybrid"))

    @patch("synola.services.hardware._estimate_model_ram_gb", return_value=10.0)
    def test_model_uses_cpu_when_vram_ratio_is_too_low(self, estimate):
        layers, fit_mode = _estimate_gpu_layers("model.gguf", 1.0)

        self.assertEqual((layers, fit_mode), (0, "cpu"))


class GPUPlanningTests(TestCase):
    def _model(self, size_gb):
        temporary_directory = TemporaryDirectory()
        path = Path(temporary_directory.name) / "model.gguf"

        with path.open("wb") as model_file:
            model_file.truncate(int(size_gb * 1024 ** 3))

        self.addCleanup(temporary_directory.cleanup)
        return path

    def test_no_vram_selects_cpu(self):
        config = pick_runtime_config(
            model_path=self._model(4),
            hardware={
                "vram_gb": 0,
                "cuda_available": False,
                "vulkan_available": False,
            },
        )

        self.assertEqual(config["gpu_layers"], 0)
        self.assertEqual(config["fit_mode"], "cpu")

    def test_model_fitting_in_vram_gets_full_offload(self):
        config = pick_runtime_config(
            model_path=self._model(3),
            hardware={
                "vram_gb": 8,
                "cuda_available": True,
                "vulkan_available": False,
            },
        )

        self.assertEqual(config["gpu_layers"], -1)
        self.assertEqual(config["fit_mode"], "gpu")

    def test_large_model_gets_hybrid_or_cpu(self):
        config = pick_runtime_config(
            model_path=self._model(12),
            hardware={
                "vram_gb": 8,
                "cuda_available": True,
                "vulkan_available": False,
            },
        )

        self.assertNotEqual(config["gpu_layers"], -1)
        self.assertIn(config["fit_mode"], ("cpu", "hybrid"))

    def test_very_large_model_stays_cpu(self):
        config = pick_runtime_config(
            model_path=self._model(20),
            hardware={
                "vram_gb": 4,
                "cuda_available": True,
                "vulkan_available": False,
            },
        )

        self.assertEqual(config["gpu_layers"], 0)
        self.assertEqual(config["fit_mode"], "cpu")


class LaunchArgumentTests(TestCase):
    def setUp(self):
        self.cpu_runtime = RuntimeDescriptor(
            name="cpu",
            directory=Path("resources/llama/cpu"),
            executable=Path("resources/llama/cpu/llama-server.exe"),
            supports_gpu=False,
        )
        self.gpu_runtime = RuntimeDescriptor(
            name="cuda",
            directory=Path("resources/llama/cuda"),
            executable=Path("resources/llama/cuda/llama-server.exe"),
            supports_gpu=True,
        )
        self.model = Path("model.gguf")

    @patch("synola.services.runtime_manager.select_runtime")
    def test_cpu_does_not_receive_gpu_layers(self, select_runtime):
        select_runtime.return_value = self.cpu_runtime, "CPU fallback"
        plan = build_launch_plan(
            self.model,
            8081,
            {"threads": 4, "ctx_size": 4096, "chat_template": "", "gpu_layers": -1},
            {},
        )

        self.assertNotIn("-ngl", plan.command)

    @patch("synola.services.runtime_manager.select_runtime")
    def test_full_gpu_offload(self, select_runtime):
        select_runtime.return_value = self.gpu_runtime, "CUDA runtime selected"
        plan = build_launch_plan(
            self.model,
            8081,
            {"threads": 4, "ctx_size": 4096, "chat_template": "", "gpu_layers": -1},
            {},
        )

        self.assertEqual(plan.command[-2:], ["-ngl", "-1"])

    @patch("synola.services.runtime_manager.select_runtime")
    def test_hybrid_gpu_offload(self, select_runtime):
        select_runtime.return_value = self.gpu_runtime, "CUDA runtime selected"
        plan = build_launch_plan(
            self.model,
            8081,
            {"threads": 4, "ctx_size": 4096, "chat_template": "", "gpu_layers": 16},
            {},
        )

        ngl_index = plan.command.index("-ngl")
        self.assertEqual(plan.command[ngl_index + 1], "16")

    @patch("synola.services.runtime_manager.select_runtime")
    def test_gpu_runtime_can_explicitly_run_cpu_only(self, select_runtime):
        select_runtime.return_value = self.gpu_runtime, "CUDA runtime selected"
        plan = build_launch_plan(
            self.model,
            8081,
            {"threads": 4, "ctx_size": 4096, "chat_template": "", "gpu_layers": 0},
            {},
        )

        self.assertNotIn("-ngl", plan.command)

    @patch("synola.services.runtime_manager.select_runtime")
    def test_launch_environment_prioritizes_selected_runtime_directory(self, select_runtime):
        select_runtime.return_value = self.gpu_runtime, "CUDA runtime selected"
        plan = build_launch_plan(
            self.model,
            8081,
            {"threads": 4, "ctx_size": 4096, "chat_template": "", "gpu_layers": 0},
            {},
        )

        self.assertEqual(plan.environment["PATH"].split(os.pathsep)[0], str(self.gpu_runtime.directory))
