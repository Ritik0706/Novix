"""
We will manage llm interfce here

Ollama provides several HTTP endpoints. The most common are:
Endpoint	Purpose
/api/generate	Generate text from a prompt
/api/chat	Chat with message history (recommended for chat apps)
/api/tags	List installed models
/api/show	Show model information
"""

import logging

import requests

logger = logging.getLogger(__name__)

# REST API endpoint exposed by the Ollama server
OLLAMA_URL = "http://127.0.0.1:8090/v1/chat/completions"
MODEL = "qwen3:1.7b"


def _prepare_chat_messages(messages, system_prompt=None):
    instructions = [system_prompt] if system_prompt else []
    turns = []

    for message in messages:
        role = message.get("role")
        content = message.get("content", "")

        if role == "system":
            instructions.append(content)
            continue
        if role not in {"user", "assistant"}:
            raise ValueError(f"Unsupported chat message role: {role!r}")

        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] = f"{turns[-1]['content']}\n\n{content}"
        else:
            turns.append({"role": role, "content": content})

    if not turns:
        turns.append({"role": "user", "content": ""})
    elif turns[0]["role"] == "assistant":
        turns.insert(0, {
            "role": "user",
            "content": "Continue the conversation using the preceding assistant context.",
        })

    if instructions:
        first_user = next(turn for turn in turns if turn["role"] == "user")
        instruction_text = "\n\n".join(instructions)
        if first_user["content"]:
            first_user["content"] = f"{instruction_text}\n\n{first_user['content']}"
        else:
            first_user["content"] = instruction_text

    return turns


def generate_summary(messages, existing_summary=None):
    logger.info("Generating conversation summary for %d messages", len(messages))
    summary_prompt = """
        Your are a conversation summarization system.

        Your task is to maintain a concise yet information rich summary of a conversation.

        Preserve:
        - important facts,
        - decisions, 
        - user preferences,
        - goals,
        - ongoing tasks,
        - important technical details
        - unresolved questions,
        - relevant context needed for future replies

        remove:
        - greetings,
        - repetitions
        - unnecessary wordings
        - conversation filler

        the summary must repeesent the conversation accurately.
        Do not invent any information.

        return only the updated summary."""

    if existing_summary:
        summary_prompt += f"Existing summary: {existing_summary}"

    summary_prompt += "New conversation messages: "
    for message in messages:
        summary_prompt += f"\n {message['role'].upper()}: {message['content']}"

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": summary_prompt}],
            "stream": False,
        },
        timeout=120,
    )
    logger.info("Summary generation returned HTTP %s", response.status_code)

    if not response.ok:
        logger.error("Ollama summary generation failed: %s", response.text)

    response.raise_for_status()

    return response.json()["choices"][0]["message"]["content"].strip()


def generate(messages, image=None):
    logger.info("Generating assistant response from %d messages", len(messages))
    messages = _prepare_chat_messages(
        messages,
        system_prompt=(
            "You are Synola.\n"
            "Always reply in English.\n"
            "Never make up names or facts.\n"
            "Only use information present in the conversation.\n"
            "If the answer is unknown, say you don't know."
        ),
    )

    if image and messages:
        last_message = messages[-1]
        messages = messages[:-1] + [{
            "role": last_message["role"],
            "content": [
                {"type": "text", "text": last_message["content"]},
                {"type": "image_url", "image_url": {"url": image}},
            ],
        }]

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "messages": messages,
            "stream": False,
        },
        timeout=120,
    )
    logger.info("Assistant generation returned HTTP %s", response.status_code)
    if not response.ok:
        logger.error("Ollama generation failed: %s", response.text)

    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()