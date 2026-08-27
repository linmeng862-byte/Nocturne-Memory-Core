"""Move deliberate keeps out of bottles/ and into the memory system, once.

`hold_this` and `throw_bottle` wrote JSON files into `buckets/continuity/bottles/`.
Nothing read that directory. A memory there was not decayed, not recalled, not
traced — not because it was protected, but because it did not exist as far as
the rest of the system was concerned.

This carries those files into real buckets, pinned. The files are left on disk:
they are the evidence that these memories were once written, and deleting them
would destroy the only record of a `throw_bottle` that predates this migration.

hold_this / throw_bottle 曾把 JSON 写进 bottles/,而没有任何东西读那个目录。
这里把它们搬进真正的桶(pinned)。原文件不删——它们是「这些记忆曾被写下」的证据。
"""

import hashlib
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

MARKER_NAME = ".bottles_migrated"

# hold-*.json came from hold_this, bottle-*.json from throw_bottle. The old
# endpoint globbed only "hold-*", so no thrown bottle was ever migrated.
# 旧迁移只 glob 了 hold-*,所以 throw_bottle 写的一条都没搬过。
PATTERNS = ("hold-*.json", "bottle-*.json")


def fingerprint(text: str) -> str:
    """Content identity. Two memories are the same one iff they say the same thing.

    The endpoint this replaces compared `content[:100]`, which makes two
    memories that open alike a single memory — and silently drops the second.
    被它替换掉的实现比的是 content[:100]:开头像的两条会被当成一条,后一条静默丢失。
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render(entry: dict) -> str:
    """The bucket body for one bottle. Stable — it feeds the fingerprint.

    The hold_this shape is NOT chosen here. Since the double-write was added,
    the `hold_this` tool has been creating pinned buckets whose body is exactly
    `f"hold_this: {memory}\\n\\n为什么记: {why}"`. Reproducing that byte for byte is
    what lets the fingerprint recognise a bottle that already landed. Change
    this string and every already-migrated memory comes back as a duplicate.
    hold_this 的格式不是这里定的——双写早就在按这个格式建桶。
    逐字节复现它,指纹才认得出「已经进去过的」。改这个字符串 = 全部重复一遍。
    """
    if entry.get("type") == "throw_bottle" or "message" in entry:
        body = (entry.get("message") or "").strip()
        return f"漂流瓶：{body}" if body else ""
    memory = (entry.get("memory") or "").strip()
    if not memory:
        return ""
    why = (entry.get("why") or "").strip()
    return f"hold_this: {memory}\n\n为什么记: {why}"


def _read(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as e:
        logger.warning(f"unreadable bottle {path.name}: {e}")
        return {}


def pending(bottles_dir, existing_contents=()) -> list[dict]:
    """Bottles not yet in the memory system, oldest first.

    Pure: reads the directory, writes nothing. Callable from a test or a
    dry run without a bucket manager.
    """
    bd = Path(bottles_dir)
    if not bd.exists():
        return []
    seen = {fingerprint(c) for c in existing_contents if c}
    out = []
    files = []
    for pat in PATTERNS:
        files.extend(bd.glob(pat))
    for path in sorted(set(files), key=lambda p: p.name):
        entry = _read(path)
        content = render(entry)
        if not content:
            continue
        fp = fingerprint(content)
        if fp in seen:
            continue
        seen.add(fp)
        out.append({
            "source_file": path.name,
            "content": content,
            "timestamp": entry.get("timestamp", ""),
            "kind": entry.get("type") or "hold_this",
        })
    return out


async def run(buckets_dir, bottles_dir, bucket_mgr) -> dict:
    """Migrate once. Idempotent: the marker is written only after a full pass."""
    marker = os.path.join(buckets_dir, MARKER_NAME)
    if os.path.exists(marker):
        return {"skipped": "already migrated", "migrated": 0}

    try:
        buckets = await bucket_mgr.list_all(include_archive=True)
    except Exception as e:
        logger.warning(f"bottle migration skipped / 漂流瓶迁移跳过: {e}")
        return {"error": str(e), "migrated": 0}

    existing = [b.get("content") or "" for b in buckets]
    todo = pending(bottles_dir, existing)

    migrated, failed = 0, 0
    for item in todo:
        tags = ["刻意留下", "throw-bottle" if item["kind"] == "throw_bottle" else "hold-this"]
        try:
            bid = await bucket_mgr.create(
                content=item["content"],
                tags=tags,
                importance=10,
                pinned=True,
                domain=["记忆"],
            )
            if bid:
                migrated += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1
            logger.warning(f"could not migrate {item['source_file']}: {e}")

    # A partial pass leaves no marker, so the next boot retries the rest.
    # Re-running is safe: the fingerprint check finds what already landed.
    # 中途失败不写标记,下次启动重试剩下的。重跑安全:指纹会认出已经落地的。
    if failed == 0:
        try:
            with open(marker, "w", encoding="utf-8") as f:
                f.write(f"{migrated}\n")
        except OSError as e:
            logger.warning(f"could not write migration marker: {e}")

    if migrated:
        logger.info(f"Migrated {migrated} bottles into pinned buckets / 搬了 {migrated} 条")
    return {"migrated": migrated, "failed": failed, "found": len(todo)}
