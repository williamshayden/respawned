#Generic JSON/JSONL file reader.
import json


def read_json_records(path):
    """
    Read a file of JSON records, auto-detecting the format:
      - JSON array file:  [{...}, {...}]
      - JSONL file:       {...}\n{...}\n
    """
    with open(path, "r") as f:
        content = f.read()

    stripped = content.strip()
    if not stripped:
        return []

    if stripped[0] == "[":
        data = json.loads(stripped)
        if not isinstance(data, list):
            raise ValueError(f"{path}: expected a top-level JSON array, got {type(data).__name__}")
        return data

    return [json.loads(line) for line in stripped.splitlines() if line.strip()]