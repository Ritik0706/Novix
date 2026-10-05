from gguf import GGUFReader


def inspect_gguf(path):
    reader = GGUFReader(str(path))

    meta = {
        key: field.parts[-1]
        for key, field in reader.fields.items()
    }

    arch = meta.get("general.architecture", "")

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

    return {
        "architecture": arch,
        "quantization": quant_map.get(
            meta.get("general.file_type"),
            str(meta.get("general.file_type", "")),
        ),
        "context_length": meta.get(
            f"{arch}.context_length",
            4096,
        ),
        "chat_template": meta.get(
            "tokenizer.chat_template",
            "",
        ),
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
