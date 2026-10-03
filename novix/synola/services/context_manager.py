# import os
# import sys
# import django
# from pathlib import Path

# # 1. Add project root to Python path
# SETTING_DIR = Path(__file__).resolve().parent.parent.parent # Points to 'novix'
# sys.path.append(str(SETTING_DIR))

# # 2. Tell Django where your settings file is
# os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'novix.settings') # Replace 'novix' with your actual project config folder name if different
# django.setup()

# # 3. Now you can use absolute imports safely
import logging

from synola.models import ConversationSummary

logger = logging.getLogger(__name__)

SUMMARY_THRESHOLD = 20

def get_unsummarized_messages(conversation):
    try:
        summary = conversation.summary
    except ConversationSummary.DoesNotExist:
        summary = None

    messages = conversation.messages.order_by("timestamp")

    if summary and summary.last_summarized_message:
        messages = messages.filter(timestamp__gt = summary.last_summarized_message.timestamp)

    messages = list(messages)
    logger.debug("Found %d unsummarized messages for conversation %s", len(messages), conversation.pk)
    return messages

def should_summarize(conversation):
    messages = get_unsummarized_messages(conversation)
    should_create_summary = len(messages) >= SUMMARY_THRESHOLD
    logger.debug("Conversation %s summary required: %s", conversation.pk, should_create_summary)
    return should_create_summary

def create_or_update_summary(conversation, summarize_function):
    try:
        summary = conversation.summary
    except ConversationSummary.DoesNotExist:
        summary = None

    unsummarized_messages = get_unsummarized_messages(conversation)

    if len(unsummarized_messages) < SUMMARY_THRESHOLD:
        logger.debug("Skipping summary for conversation %s: %d messages available", conversation.pk, len(unsummarized_messages))
        return False
    
    messages_for_summary = [{"role":message.role, "content":message.content} for message in unsummarized_messages[:SUMMARY_THRESHOLD]]

    existing_summary = summary.content if summary else None

    new_summary = summarize_function(messages_for_summary, existing_summary)

    last_message = unsummarized_messages[SUMMARY_THRESHOLD-1]

    if summary:
        summary.content = new_summary
        summary.last_summarized_message = last_message
        summary.save()
        logger.info("Updated summary for conversation %s", conversation.pk)
    
    else:
        ConversationSummary.objects.create(conversation=conversation, content=new_summary, last_summarized_message=last_message)
        logger.info("Created summary for conversation %s", conversation.pk)
    return True

def build_context(conversation):
    try:
        summary = conversation.summary
    except ConversationSummary.DoesNotExist:
        summary = None

    recent_messages = conversation.messages.order_by("-timestamp", "-id")[:20]
    recent_messages = reversed(list(recent_messages))

    context = []

    if summary:
        context.append({"role":"system", "content":("Conversation summary:\n" + summary.content)})
    
    context.extend([{"role":message.role, "content": message.content, } for message in recent_messages])

    logger.debug("Built context with %d messages for conversation %s", len(context), conversation.pk)
    return context