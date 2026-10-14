from gguf import GGUFReader


def _normalize_metadata_value(value):
    if hasattr(value, "item") and callable(value.item):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if hasattr(value, "tolist"):
        try:
            value = value.tolist()
        except TypeError:
            pass
    if isinstance(value, (list, tuple)):
        if len(value) == 1:
            return _normalize_metadata_value(value[0])
        return [
            _normalize_metadata_value(item)
            for item in value
        ]
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


def inspect_gguf(path):
    reader = GGUFReader(str(path))

    meta = {}
    for key, field in reader.fields.items():
        try:
            value = field.contents()
        except Exception:
            value = getattr(field, "parts", [-1])[-1]
        meta[key] = _normalize_metadata_value(value)

    arch = str(meta.get("general.architecture", ""))

    quant_map = {
        0: "F32",
        1: "F16",
        2: "Q4_0",
        3: "Q4_1",
        6: "Q5_0",
        7: "Q5_1",
        8: "Q8_0",
        10: "Q2_K",
        11: "Q3_K_S",
        12: "Q3_K_M",
        13: "Q3_K_L",
        14: "Q4_K_S",
        15: "Q4_K_M",
        16: "Q5_K_S",
        17: "Q5_K_M",
        18: "Q6_K",
    }
    file_type = meta.get("general.file_type")
    try:
        quant_value = int(file_type)
    except (TypeError, ValueError):
        quant_value = None

    context_length = meta.get(f"{arch}.context_length", 4096)
    try:
        context_length = int(context_length)
    except (TypeError, ValueError):
        context_length = 4096

    return {
        "architecture": arch,
        "quantization": quant_map.get(
            quant_value,
            str(file_type if file_type is not None else ""),
        ),
        "context_length": context_length,
        "chat_template": meta.get("tokenizer.chat_template", ""),
        "capabilities": {
            "vision": arch.lower() in {
                "llava",
                "bakllava",
                "minicpmv",
                "qwen2vl",
                "qwen2_5_vl",
                "qwen3vl",
                "gemma3",
                "pixtral",
            }
        },
    }
