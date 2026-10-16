import json
import logging
import os
import shutil
from threading import Condition

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt

from synola.models import Conversation, InstalledModel, Message
from synola.services.ai import generate, generate_summary
from synola.services.context_manager import build_context, create_or_update_summary
from synola.services.model_downloader import DownloadCancelled, download_model
from synola.services.model_manager import (
    STAGING_PATH,
    activate_installed_model,
    remove_active_model,
    swap_model,
)
from synola.services.hardware import get_hardware_profile
from synola.services.model_recommender import (
    get_installable_model,
    get_model_options,
    install_recommended_model,
    recommend_model,
)
from synola.services.runtime_manager import runtime_status

logger = logging.getLogger(__name__)
_download_progress_lock = Condition()
_download_progress = {
    "status": "idle",
    "downloaded": 0,
    "total": 0,
    "error": "",
}


def _set_download_progress(**updates):
    with _download_progress_lock:
        if (
            _download_progress["status"] in {"paused", "cancelling"}
            and updates.get("status") in {"preparing", "downloading", "finishing"}
        ):
            updates.pop("status")
        _download_progress.update(updates)
        _download_progress_lock.notify_all()


def model_install_progress(request):
    if request.method != "GET":
        return json_response({"error": "Use GET request"}, status=405)
    with _download_progress_lock:
        progress = dict(_download_progress)
    return json_response(progress)


@csrf_exempt
def model_install_control(request):
    if request.method != "POST":
        return json_response({"error": "Use POST request"}, status=405)

    try:
        body = json.loads(request.body.decode("utf-8"))
        action = body["action"]
        if action not in {"pause", "resume", "cancel"}:
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return json_response({"error": "A valid download action is required"}, status=400)

    active_states = {"preparing", "downloading", "paused", "finishing", "cancelling"}
    with _download_progress_lock:
        status = _download_progress["status"]
        if status not in active_states:
            return json_response({"error": "There is no active model download"}, status=409)

        if action == "pause":
            if status != "paused":
                _download_progress["status"] = "paused"
        elif action == "resume":
            if status == "paused":
                _download_progress["status"] = "downloading"
        else:
            _download_progress["status"] = "cancelling"
        _download_progress_lock.notify_all()
        progress = dict(_download_progress)

    return json_response(progress)


def json_response(data, status=200):
    if status >= 400:
        logger.warning("Returning %s response payload: %s", status, data)
    response = JsonResponse(data, status=status)
    response["Access-Control-Allow-Origin"] = "*"
    return response

def home(request):
    logger.info("Serving home page")
    return json_response({"status": "ok", "service": "novix-backend"})

@csrf_exempt
def conversations(request):

    if request.method == "GET":
        logger.info("Listing conversations")
        data = Conversation.objects.order_by("-updated_at")
        return json_response({"conversations": [{"id": c.id, "title": c.title, "created_at": c.created_at, "updated_at": c.updated_at,} for c in data]})

    elif request.method == "POST":
        logger.info("Creating a new conversation")
        conversation = Conversation.objects.create()
        return json_response({"id": conversation.id, "title": conversation.title})

    logger.warning("Rejected unsupported method %s for conversations endpoint", request.method)
    return json_response({"error": "Method not allowed"}, status=405)

@csrf_exempt
def conversation_detail(request, conversation_id):
    try:
        conversation = Conversation.objects.get(id=conversation_id)

    except Conversation.DoesNotExist:
        logger.warning("Conversation %s not found", conversation_id)
        return json_response({"error": "Conversation not found"},status=404)

    if request.method == "GET":
        logger.info("Fetching conversation %s details", conversation_id)
        messages = conversation.messages.order_by("timestamp")
        return json_response({"id": conversation.id,"title": conversation.title,"messages": [{"role": m.role,"content": m.content,"timestamp": m.timestamp,} for m in messages]})
    
    elif request.method == "DELETE":
        logger.info("Deleting conversation %s", conversation_id)
        conversation.delete()
        return json_response({"status": "deleted"})

    logger.warning("Rejected unsupported method %s for conversation %s", request.method, conversation_id)
    return json_response({"error": "Method not allowed"}, status=405)

@csrf_exempt
def rename_conversation(request, conversation_id):

    conversation = get_object_or_404(Conversation, id=conversation_id)

    try:
        body = json.loads(request.body)
    except (TypeError, ValueError):
        logger.warning("Invalid JSON payload while renaming conversation %s", conversation_id)
        return json_response({"error": "Invalid JSON"}, status=400)

    new_name = body.get("name", "").strip()
    if not new_name:
        logger.warning("Empty conversation name supplied for %s", conversation_id)
        return json_response({"error": "Name is required"}, status=400)

    conversation.title = new_name
    conversation.save()
    logger.info("Renamed conversation %s to %s", conversation_id, new_name)

    return json_response({"id": conversation.id, "title": conversation.title}, status=200)

@csrf_exempt
def chat(request):
    if request.method == "OPTIONS":
        response = JsonResponse({"ok": True})
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    if request.method != "POST":
        logger.warning("Rejected method %s for chat endpoint", request.method)
        return json_response({"error": "Use POST request"}, status=405)

    try:
        body = json.loads(request.body.decode("utf-8")) #input message comes here as json
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Invalid JSON payload received in chat request")
        return json_response({"error": "Invalid JSON"}, status=400)

    conversation_id = body.get("conversation_id")
    message = body.get("message", "").strip()
    image = body.get("image")

    if not conversation_id:
        logger.warning("Chat request missing conversation_id")
        return json_response({"error": "conversation_id is required"}, status=400)

    if not message:
        logger.warning("Chat request for conversation %s missing message", conversation_id)
        return json_response({"error": "Message is required"}, status=400)

    if image:
        active_model = InstalledModel.objects.filter(is_active=True).first()
        if not active_model or not active_model.capabilities.get("vision", False):
            return json_response(
                {"error": "The active model does not support image input"},
                status=400,
            )

    try:
        conversation = Conversation.objects.get(id=conversation_id)
    except Conversation.DoesNotExist:
        logger.warning("Chat request referenced missing conversation %s", conversation_id)
        return json_response({"error": "Conversation not found"}, status=404)

    logger.info("Processing chat message for conversation %s", conversation_id)

    try:
        Message.objects.create(conversation=conversation, role="user", content=message)
        messages = build_context(conversation)
        reply = generate(messages, image=image)
        Message.objects.create(conversation=conversation, role="assistant", content=reply)
        create_or_update_summary(conversation, generate_summary)
    except Exception:
        logger.exception("Error generating assistant reply for conversation %s", conversation_id)
        return json_response({"error": "Unable to generate response"}, status=500)

    return json_response({"reply": reply})


@csrf_exempt
def model_swap(request):
    if request.method != "POST":
        return json_response({"error": "Use POST request"}, status=405)

    try:
        body = json.loads(request.body.decode("utf-8"))
        url = body["url"]
        metadata = {
            "display_name": body["display_name"],
            "source_url": url,
            "sha_256": body.get("sha_256", ""),
            "size_bytes": int(body["size_bytes"]),
            "context_length": int(body.get("context_length", 4096)),
            "architecture": body.get("architecture", ""),
            "quantization": body.get("quantization", ""),
            "chat_template": body.get("chat_template", ""),
            "capabilities": body.get("capabilities", {}),
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return json_response({"error": "url, display_name, and size_bytes are required"}, status=400)

    try:
        download_model(
            url,
            STAGING_PATH,
            expected_sha_256=metadata["sha_256"] or None,
            expected_size=metadata["size_bytes"],
            max_retries=3,
        )
        swap_model(STAGING_PATH, metadata)
    except Exception:
        logger.exception("Model replacement failed")
        return json_response({"error": "Model replacement failed"}, status=500)

    return json_response({"status": "active", "display_name": metadata["display_name"]})


@csrf_exempt
def model_recommendation(request):
    if request.method == "GET":
        try:
            profile = get_hardware_profile()
            suggestion = recommend_model(profile)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            logger.exception("Could not create model recommendation")
            return json_response({"error": "Unable to create a model recommendation"}, status=500)
        return json_response({"hardware": profile, "recommendation": suggestion})

    if request.method == "POST":
        try:
            profile = get_hardware_profile()
            suggestion = recommend_model(profile)
            if not suggestion["installable"]:
                return json_response({"error": "The recommendation is not available for download yet"}, status=409)
            free_bytes = shutil.disk_usage(settings.DATA_DIR).free
            if suggestion["model"]["size_bytes"] * 2 > free_bytes:
                return json_response({"error": "Not enough free storage to install the recommendation"}, status=409)
            install_recommended_model(suggestion)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            logger.exception("Could not install recommended model")
            return json_response({"error": "Unable to install recommended model"}, status=500)
        return json_response({"status": "active", "recommendation": suggestion})

    return json_response({"error": "Use GET or POST request"}, status=405)


@csrf_exempt
def model_settings(request):
    if request.method != "GET":
        return json_response({"error": "Use GET request"}, status=405)

    try:
        hardware = get_hardware_profile()
        disk = shutil.disk_usage(settings.DATA_DIR)
        recommendation = recommend_model(hardware)
        options = get_model_options(hardware, disk.free)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        logger.exception("Could not load model settings")
        return json_response({"error": "Unable to load model settings"}, status=500)

    installed_bytes = sum(
        model.size_bytes for model in InstalledModel.objects.only("size_bytes")
    )
    installed_models = [
        {
            "id": model.id,
            "catalog_id": model.sha_256,
            "display_name": model.display_name,
            "size_bytes": model.size_bytes,
            "context_length": model.context_length,
            "quantization": model.quantization,
            "is_active": model.is_active,
        }
        for model in InstalledModel.objects.order_by("-is_active", "-installed_at")
    ]
    return json_response({
        "hardware": hardware,
        "device_class": recommendation["device_class"],
        "storage": {
            "installed_bytes": installed_bytes,
            "free_bytes": disk.free,
            "total_bytes": disk.total,
        },
        "models": options,
        "installed_models": installed_models,
    })


@csrf_exempt
def install_settings_model(request):
    if request.method != "POST":
        return json_response({"error": "Use POST request"}, status=405)

    try:
        body = json.loads(request.body.decode("utf-8"))
        model_id = body["id"]
        if not isinstance(model_id, str) or not model_id:
            raise ValueError("A model id is required")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return json_response({"error": "A model id is required"}, status=400)

    with _download_progress_lock:
        if _download_progress["status"] in {
            "preparing", "downloading", "paused", "finishing", "cancelling",
        }:
            return json_response({"error": "A model download is already in progress"}, status=409)
        _download_progress.update(
            status="preparing",
            downloaded=0,
            total=0,
            error="",
        )
        _download_progress_lock.notify_all()

    try:
        installed = InstalledModel.objects.filter(sha_256=model_id).first()
        if installed:
            if not installed.is_active:
                activate_installed_model(installed.id)
            _set_download_progress(status="completed", error="")
            return json_response({"status": "active", "id": model_id, "downloaded": True})

        hardware = get_hardware_profile()
        free_bytes = shutil.disk_usage(settings.DATA_DIR).free
        model = get_installable_model(model_id, hardware, free_bytes)
        if model is None:
            message = "This model is unavailable for this device or lacks free storage"
            _set_download_progress(status="error", error=message)
            return json_response(
                {"error": message},
                status=409,
            )

        _set_download_progress(
            status="preparing",
            downloaded=0,
            total=model["size_bytes"],
            error="",
        )

        def report_progress(downloaded, total, status="downloading"):
            with _download_progress_lock:
                while _download_progress["status"] == "paused":
                    _download_progress_lock.wait()
                if _download_progress["status"] in {"cancelling", "cancelled"}:
                    raise DownloadCancelled("Model download cancelled")
                _download_progress.update(
                    status=status,
                    downloaded=downloaded,
                    total=total or model["size_bytes"],
                )
                _download_progress_lock.notify_all()

        install_recommended_model({"model": model}, progress_cb=report_progress)
        _set_download_progress(
            status="completed",
            downloaded=model["size_bytes"],
            total=model["size_bytes"],
            error="",
        )
    except DownloadCancelled:
        _set_download_progress(status="cancelled", error="")
        return json_response({"status": "cancelled"}, status=409)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        logger.exception("Could not install selected settings model %s", model_id)
        message = str(error) or "Unable to install selected model"
        _set_download_progress(status="error", error=message)
        return json_response({"error": message}, status=500)

    return json_response({"status": "active", "id": model_id})


@csrf_exempt
def current_model(request):
    if request.method != "DELETE":
        return json_response({"error": "Use DELETE request"}, status=405)

    try:
        removed = remove_active_model()
    except Exception:
        logger.exception("Could not remove active model")
        return json_response({"error": "Unable to remove active model"}, status=500)
    if removed is None:
        return json_response({"error": "There is no active model"}, status=404)
    return json_response({"status": "removed"})


@csrf_exempt
def runtime_diagnostics(request):
    if request.method != "GET":
        return json_response({"error": "Use GET request"}, status=405)
    hardware = get_hardware_profile()
    return json_response({"hardware": hardware, "runtimes": runtime_status(hardware)})


@csrf_exempt
def installed_models(request):
    if request.method == "POST":
        try:
            model_id = json.loads(request.body.decode("utf-8"))["id"]
            activate_installed_model(int(model_id))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return json_response({"error": "A model id is required"}, status=400)
        except InstalledModel.DoesNotExist:
            return json_response({"error": "Installed model not found"}, status=404)
        except Exception:
            logger.exception("Could not activate installed model")
            return json_response({"error": "Unable to activate installed model"}, status=500)
        return json_response({"status": "active", "id": int(model_id)})

    if request.method != "GET":
        return json_response({"error": "Use GET or POST request"}, status=405)
    models = InstalledModel.objects.order_by("-is_active", "-installed_at")
    return json_response({
        "models": [
            {
                "id": model.id,
                "display_name": model.display_name,
                "local_path": model.local_path,
                "context_length": model.context_length,
                "architecture": model.architecture,
                "quantization": model.quantization,
                "chat_template": model.chat_template,
                "capabilities": model.capabilities,
                "is_active": model.is_active,
            }
            for model in models
        ]
    })


@csrf_exempt
def installed_model_detail(request, model_id):
    if request.method != "DELETE":
        return json_response({"error": "Use DELETE request"}, status=405)

    try:
        model = InstalledModel.objects.get(pk=model_id)
    except InstalledModel.DoesNotExist:
        return json_response({"error": "Installed model not found"}, status=404)

    if model.is_active:
        return json_response({"error": "The active model cannot be deleted"}, status=409)

    local_path = model.local_path
    if local_path:
        try:
            os.remove(local_path)
        except FileNotFoundError:
            logger.warning("Installed model file was already missing: %s", local_path)
        except OSError:
            logger.exception("Could not delete installed model file %s", local_path)
            return json_response({"error": "Unable to delete installed model"}, status=500)

    model.delete()
    return json_response({"status": "deleted", "id": model_id})


@csrf_exempt
def model_storage(request):
    if request.method != "GET":
        return json_response({"error": "Use GET request"}, status=405)

    usage = shutil.disk_usage(settings.DATA_DIR)
    installed_bytes = sum(
        model.size_bytes
        for model in InstalledModel.objects.only("size_bytes")
    )
    return json_response({
        "installed_bytes": installed_bytes,
        "free_bytes": usage.free,
        "total_bytes": usage.total,
    })