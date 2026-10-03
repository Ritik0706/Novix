from django.test import TestCase
from unittest.mock import Mock, patch

from synola.models import Conversation, ConversationSummary, Message
from synola.services import ai
from synola.services.context_manager import build_context
from synola.services.inference_engine import InferenceEngine


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
    @patch.object(InferenceEngine, "model_available", return_value=True)
    @patch.object(InferenceEngine, "is_healthy", return_value=True)
    def test_start_succeeds_when_ollama_running_and_model_installed(self, is_healthy, model_available):
        engine = InferenceEngine()

        engine.start()

        is_healthy.assert_called()
        model_available.assert_called()

    @patch.object(InferenceEngine, "model_available", side_effect=[False, True])
    @patch.object(InferenceEngine, "_pull_model")
    @patch.object(InferenceEngine, "is_healthy", return_value=True)
    def test_start_pulls_model_when_missing(self, is_healthy, pull, model_available):
        engine = InferenceEngine()

        engine.start()

        pull.assert_called_once()

    @patch.object(InferenceEngine, "_start_ollama_server", side_effect=RuntimeError(
        "Ollama is not installed. Install Ollama from https://ollama.com"
    ))
    @patch.object(InferenceEngine, "is_healthy", return_value=False)
    def test_start_raises_when_ollama_unreachable(self, is_healthy, start_server):
        engine = InferenceEngine()

        with self.assertRaises(RuntimeError):
            engine.start()
        start_server.assert_called_once()
