from django.db import models
from django.utils import timezone


class Conversation(models.Model):
    title = models.CharField(max_length=100, default="New Chat")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title


class Message(models.Model):
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages"
    )
    role = models.CharField(max_length=20)
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.role}: {self.content[:30]}"
    

class ConversationSummary(models.Model):
    conversation = models.OneToOneField(Conversation, on_delete=models.CASCADE, related_name="summary")
    content = models.TextField()
    last_summarized_message = models.ForeignKey(Message, on_delete = models.SET_NULL, null=True, blank=True, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"summary: {self.conversation.title}"
    

class InstalledModel(models.Model):
    display_name = models.CharField(max_length=255)
    local_path = models.CharField(max_length=500, blank=True, default="")
    source_url = models.URLField()
    sha_256 = models.CharField(max_length=64, blank=True)
    size_bytes = models.BigIntegerField()
    context_length = models.IntegerField(default=4096)
    architecture = models.CharField(max_length=100, blank=True, default="")
    quantization = models.CharField(max_length=100, blank=True, default="")
    chat_template = models.CharField(max_length=100, blank=True, default="")
    capabilities = models.JSONField(default=dict)
    is_active = models.BooleanField(default=False)
    installed_at = models.DateTimeField(auto_now=True)