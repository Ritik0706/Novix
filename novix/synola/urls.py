from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path("chat/", views.chat, name='chat'),
    path("conversations/", views.conversations, name="conversations"),
    path("conversations/<int:conversation_id>/rename/", views.rename_conversation, name="rename_conversation"),
    path("conversations/<int:conversation_id>/", views.conversation_detail, name="conversation_detail"),
    path("models/swap/", views.model_swap, name="model_swap"),
    path("models/recommendation/", views.model_recommendation, name="model_recommendation"),
    path("runtime/diagnostics/", views.runtime_diagnostics, name="runtime_diagnostics"),
    path("models/installed/", views.installed_models, name="installed_models"),
    path("models/installed/<int:model_id>/", views.installed_model_detail, name="installed_model_detail"),
    path("models/storage/", views.model_storage, name="model_storage"),
]