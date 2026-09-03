# features/knowledge_base/indexer.py
# Run once (or whenever you update docs) to load all YAML files into MongoDB.
# Creates the knowledge_chunks collection + a $text index for RAG retrieval.
#
# Usage:  python -m features.knowledge_base.indexer

import asyncio
import os
import sys
import yaml
from pathlib import Path
from datetime import datetime, timezone

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.database import mongo


DOCS_DIR = Path(__file__).resolve().parents[2] / "docs"
COLLECTION = "knowledge_chunks"
CHUNK_SIZE = 600   # characters per chunk — keeps each chunk within ~150 tokens


def _flatten_yaml(obj, prefix: str = "") -> list[str]:
    """
    Recursively flatten a nested YAML dict/list into readable plain-text sentences.
    Each returned string is one logical fact/rule.
    """
    lines: list[str] = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            label = f"{prefix}.{key}" if prefix else key
            if isinstance(value, (dict, list)):
                lines.extend(_flatten_yaml(value, label))
            else:
                lines.append(f"{label}: {value}")

    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                lines.extend(_flatten_yaml(item, prefix))
            else:
                lines.append(f"{prefix}: {item}")
    else:
        lines.append(f"{prefix}: {obj}")

    return lines


def _chunk_text(sentences: list[str], chunk_size: int = CHUNK_SIZE) -> list[str]:
    """Group sentences into chunks that stay under chunk_size characters."""
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) + 2 > chunk_size and current:
            chunks.append(current.strip())
            current = sentence
        else:
            current = (current + "\n" + sentence).strip()
    if current:
        chunks.append(current.strip())
    return chunks


async def index_docs() -> None:
    await mongo.connect()

    if mongo.db is None:
        print("[Indexer] MongoDB not connected — aborting.")
        return

    col = mongo.db[COLLECTION]

    # 1. Ensure a text index exists on 'text' field
    try:
        await col.create_index([("text", "text")], name="knowledge_text_index")
        print("[Indexer] Text index ensured on 'knowledge_chunks.text'")
    except Exception as e:
        print(f"[Indexer] Index creation warning (may already exist): {e}")

    yaml_files = sorted(DOCS_DIR.glob("*.yaml"))
    if not yaml_files:
        print(f"[Indexer] No YAML files found in {DOCS_DIR}")
        return

    total_chunks = 0

    for yaml_file in yaml_files:
        module_name = yaml_file.stem  # e.g. "03_Wyckoff_Method"

        try:
            with open(yaml_file, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception as exc:
            print(f"[Indexer] Skipping {yaml_file.name}: {exc}")
            continue

        if data is None:
            continue

        # Flatten YAML into sentences
        sentences = _flatten_yaml(data)

        # Group into chunks
        chunks = _chunk_text(sentences, CHUNK_SIZE)

        # Remove old chunks for this module (clean re-index)
        await col.delete_many({"source": module_name})

        # Insert new chunks
        docs = [
            {
                "source": module_name,
                "chunk_index": i,
                "text": chunk,
                "indexed_at": datetime.now(timezone.utc),
            }
            for i, chunk in enumerate(chunks)
        ]

        if docs:
            await col.insert_many(docs)
            total_chunks += len(docs)
            print(f"  {yaml_file.name}: {len(chunks)} chunks")

    print(f"\n[Indexer] Done. {total_chunks} total chunks indexed into '{COLLECTION}'.")
    await mongo.close()


if __name__ == "__main__":
    asyncio.run(index_docs())
