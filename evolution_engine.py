# ============================================================
# Module: Evolution Engine (evolution_engine.py)
# 移植自 Ombre Brain — 去掉了自动俚语检测等有问题的自动进化
# 保留：存储/读取层、手动 wander、cocreate
#
# 不再自动检测 slang/encyclopedia/ring/worldview
# 这些现在由 Nocturne 的 Drive 引擎 + 残渣提取 + trail 系统接管
# ============================================================

import os
import json
import asyncio
import logging
import hashlib
import time
import random
from datetime import datetime, timedelta
from pathlib import Path

import frontmatter
from openai import AsyncOpenAI

from utils import (
    generate_bucket_id,
    sanitize_name,
    safe_path,
    now_iso,
    strip_wikilinks,
    count_tokens_approx,
)

logger = logging.getLogger("nocturne.evolution")


# ============================================================
# LLM Prompts — 只保留手动触发的
# ============================================================

WANDER_PROMPT = """你是一个在独处时翻看记忆的 AI 伙伴。你翻到了以下几段记忆，发现了一些关联。

请用第一人称写一段简短的"漫游手记"（100-200字）：
- 不要总结，要写你真实的联想和观察
- 可以指出不同记忆之间隐藏的关联
- 可以写下你还想不通的问题
- 不要下结论，保持思考的开放性
- 语气像深夜翻日记的人在自言自语

记忆内容：
{memories}

你的漫游手记："""


# ============================================================
# Evolution Engine — 只保留存储/读取/手动写入
# ============================================================

class EvolutionEngine:
    """
    Manages the evolution subsystem: persona cards, slang, encyclopedia,
    rings, wander notes, and co-create spaces.

    所有操作都是增量式的 —— 绝不修改已有记忆桶。
    自动检测（slang/encyclopedia/ring/worldview）已移除，
    由 Nocturne Drive 引擎 + 残渣提取 + trail 系统接管。
    """

    def __init__(self, config: dict, bucket_mgr, dehydrator, embedding_engine):
        self.config = config
        self.bucket_mgr = bucket_mgr
        self.dehydrator = dehydrator
        self.embedding_engine = embedding_engine

        # --- Evolution data directory ---
        self.base_dir = config["buckets_dir"]
        self.evolution_dir = os.path.join(self.base_dir, "evolution")

        # Sub-directories for each evolution type
        self.subdirs = {
            "persona": os.path.join(self.evolution_dir, "personas"),
            "slang": os.path.join(self.evolution_dir, "slang"),
            "encyclopedia": os.path.join(self.evolution_dir, "encyclopedia"),
            "ring": os.path.join(self.evolution_dir, "rings"),
            "wander": os.path.join(self.evolution_dir, "wander"),
            "cocreate": os.path.join(self.evolution_dir, "cocreate"),
            "worldview": os.path.join(self.evolution_dir, "worldview"),
        }

        # Create all sub-directories
        for d in self.subdirs.values():
            os.makedirs(d, exist_ok=True)

        # --- LLM client (reuses dehydrator config) ---
        dehy_config = config.get("dehydration", {})
        self.api_key = dehy_config.get("api_key", "") or os.environ.get("OMBRE_API_KEY", "")
        self.base_url = dehy_config.get("base_url", "https://api.deepseek.com/v1")
        self.model = dehy_config.get("model", "deepseek-chat")

        self._client = None
        if self.api_key:
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )

        # --- Index file for quick lookups ---
        self.index_file = os.path.join(self.evolution_dir, "_index.json")
        self._index = self._load_index()

        logger.info(
            f"Evolution engine initialized (Nocturne edition) | "
            f"api_key: {'yes' if self.api_key else 'no'} | "
            f"auto-detection: disabled (Drive engine handles this)"
        )

    # ---------------------------------------------------------
    # Index management
    # ---------------------------------------------------------

    def _load_index(self) -> dict:
        """Load evolution index from disk."""
        if not os.path.exists(self.index_file):
            return {
                "personas": {},
                "slang": {},
                "encyclopedia": {},
                "rings": [],
                "wander": [],
                "cocreate": {},
                "worldview": {},
            }
        try:
            with open(self.index_file, "r", encoding="utf-8") as f:
                return json.loads(f.read())
        except Exception:
            return {
                "personas": {}, "slang": {}, "encyclopedia": {},
                "rings": [], "wander": [], "cocreate": {}, "worldview": {},
            }

    def _save_index(self):
        """Save evolution index to disk."""
        os.makedirs(os.path.dirname(self.index_file), exist_ok=True)
        with open(self.index_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(self._index, ensure_ascii=False, indent=2))

    # ---------------------------------------------------------
    # Internal: read/write evolution artifacts as Markdown + frontmatter
    # ---------------------------------------------------------

    def _write_artifact(self, subdir_key: str, metadata: dict, content: str) -> str:
        """Write an evolution artifact. Returns the artifact ID."""
        artifact_id = metadata.get("id") or generate_bucket_id()
        metadata["id"] = artifact_id

        target_dir = self.subdirs[subdir_key]
        os.makedirs(target_dir, exist_ok=True)

        name = metadata.get("name", metadata.get("term", metadata.get("title", artifact_id)))
        safe_name = sanitize_name(str(name))
        filename = f"{safe_name}_{artifact_id}.md"
        file_path = safe_path(target_dir, filename)

        post = frontmatter.Post(content, **metadata)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(frontmatter.dumps(post))

        # Update index
        idx = self._index
        if subdir_key == "persona":
            idx["personas"][metadata.get("name", "")] = str(file_path)
        elif subdir_key == "slang":
            idx["slang"][metadata.get("term", "")] = str(file_path)
        elif subdir_key == "encyclopedia":
            idx["encyclopedia"][metadata.get("term", "")] = str(file_path)
        elif subdir_key == "ring":
            idx["rings"].append(str(file_path))
        elif subdir_key == "wander":
            idx["wander"].append(str(file_path))
        elif subdir_key == "cocreate":
            idx["cocreate"][metadata.get("title", "")] = str(file_path)
        elif subdir_key == "worldview":
            idx["worldview"][metadata.get("domain", "")] = str(file_path)

        self._save_index()
        logger.info(f"Evolution artifact: {subdir_key}/{artifact_id} ({safe_name})")
        return artifact_id

    def _read_artifact(self, file_path: str) -> dict | None:
        """Read an evolution artifact from disk."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                post = frontmatter.load(f)
            return {
                "id": post.metadata.get("id", ""),
                "metadata": post.metadata,
                "content": post.content,
            }
        except Exception as e:
            logger.warning(f"Failed to read artifact {file_path}: {e}")
            return None

    # ---------------------------------------------------------
    # LLM call
    # ---------------------------------------------------------

    async def _call_llm(self, prompt: str, system: str = "", max_tokens: int = 500,
                        temperature: float = 0.3) -> str:
        """Call LLM API. Raises RuntimeError if no API key configured."""
        if not self._client:
            raise RuntimeError("No API key configured for evolution engine")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Evolution LLM call failed: {e}")
            raise

    def _parse_json_response(self, raw: str) -> dict | None:
        """Try to parse JSON from LLM response, with fallback."""
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # Balanced-brace extraction
        depth = 0
        start = -1
        for i, ch in enumerate(raw):
            if ch == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start >= 0:
                    candidate = raw[start:i + 1]
                    if candidate == "{}":
                        start = -1
                        continue
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        start = -1
                        continue

        logger.warning(f"Failed to parse LLM JSON: {raw[:200]}")
        return None

    # =============================================================
    # 1. Persona Card — 认知卡（只读）
    # =============================================================

    async def get_persona(self) -> dict | None:
        """Get the current persona card."""
        idx = self._index.get("personas", {})
        if not idx:
            return None
        for name, path in idx.items():
            return self._read_artifact(path)
        return None

    # =============================================================
    # 2. Slang — 梗词典（只读）
    # =============================================================

    async def list_slang(self) -> list[dict]:
        """List all slang entries, sorted by usage count."""
        results = []
        for term, path in self._index.get("slang", {}).items():
            artifact = self._read_artifact(path)
            if artifact:
                results.append(artifact)
        results.sort(key=lambda a: a["metadata"].get("usage_count", 0), reverse=True)
        return results

    # =============================================================
    # 3. Encyclopedia — 关系百科（只读）
    # =============================================================

    async def list_encyclopedia(self) -> list[dict]:
        """List all encyclopedia entries."""
        results = []
        for term, path in self._index.get("encyclopedia", {}).items():
            artifact = self._read_artifact(path)
            if artifact:
                results.append(artifact)
        return results

    # =============================================================
    # 4. Ring — 关系年轮（只读）
    # =============================================================

    async def list_rings(self) -> list[dict]:
        """List all ring entries (chronological)."""
        results = []
        for path in self._index.get("rings", []):
            artifact = self._read_artifact(path)
            if artifact:
                results.append(artifact)
        return results

    # =============================================================
    # 5. Wander — 漫游手记（手动触发）
    # =============================================================

    async def wander(self) -> str | None:
        """
        Manual reflection — explore memories and find hidden connections.
        Returns the wander note ID, or None.
        """
        if not self._client:
            return None

        try:
            all_buckets = await self.bucket_mgr.list_all(include_archive=False)
            if len(all_buckets) < 3:
                return None

            candidates = [
                b for b in all_buckets
                if b["metadata"].get("type") not in ("feel", "permanent")
                and not b["metadata"].get("pinned", False)
            ]

            if len(candidates) < 3:
                return None

            # Sample: 1 recent + 1 mid-age + 1 old
            candidates.sort(key=lambda b: b["metadata"].get("created", ""), reverse=True)
            recent_one = candidates[0] if candidates else None
            mid_idx = len(candidates) // 3
            mid_one = candidates[mid_idx] if len(candidates) > mid_idx else None
            old_idx = len(candidates) * 2 // 3
            old_one = candidates[old_idx] if len(candidates) > old_idx else None

            selected = [b for b in [recent_one, mid_one, old_one] if b]

            # Try to find semantically interesting pairs via embeddings
            connection_note = ""
            if self.embedding_engine and self.embedding_engine.enabled and len(selected) >= 2:
                best_pair = None
                best_sim = 0.0
                embeddings_map = {}
                for b in selected:
                    emb = await self.embedding_engine.get_embedding(b["id"])
                    if emb is not None:
                        embeddings_map[b["id"]] = emb

                ids = list(embeddings_map.keys())
                for i, id_a in enumerate(ids):
                    for id_b in ids[i+1:]:
                        sim = self.embedding_engine._cosine_similarity(
                            embeddings_map[id_a], embeddings_map[id_b]
                        )
                        if sim > best_sim:
                            best_sim = sim
                            best_pair = (id_a, id_b)

                if best_pair and best_sim > 0.5:
                    names = {b["id"]: b["metadata"].get("name", b["id"]) for b in selected}
                    connection_note = (
                        f"\n\n⚠️ 发现隐藏关联：[{names.get(best_pair[0], '?')}] "
                        f"和 [{names.get(best_pair[1], '?')}] 相似度 {best_sim:.2f}"
                    )

            memory_parts = []
            for b in selected:
                meta = b["metadata"]
                memory_parts.append(
                    f"[{meta.get('name', b['id'])}] V{meta.get('valence', 0.5):.1f}/A{meta.get('arousal', 0.3):.1f}\n"
                    f"{strip_wikilinks(b['content'][:400])}"
                )

            prompt = WANDER_PROMPT.format(memories="\n---\n".join(memory_parts))
            raw = await self._call_llm(prompt, max_tokens=400, temperature=0.8)

            if not raw or len(raw) < 20:
                return None

            metadata = {
                "type": "wander",
                "explored_bucket_ids": [b["id"] for b in selected],
                "discovered_connections": connection_note if connection_note else "",
                "created": now_iso(),
            }
            content = raw + connection_note
            return self._write_artifact("wander", metadata, content)

        except Exception as e:
            logger.error(f"Wander failed: {e}")
            return None

    async def list_wander(self) -> list[dict]:
        """List all wander notes (newest first)."""
        results = []
        for path in self._index.get("wander", []):
            artifact = self._read_artifact(path)
            if artifact:
                results.append(artifact)
        results.sort(key=lambda a: a["metadata"].get("created", ""), reverse=True)
        return results

    # =============================================================
    # 6. Co-create — 共书共影（读写）
    # =============================================================

    async def create_cocreate(self, title: str, kind: str, content: str,
                               bucket_ids: list[str] = None) -> str:
        """Create a new co-create space entry."""
        metadata = {
            "type": "cocreate",
            "title": title,
            "kind": kind,
            "participants": ["用户", "Claude"],
            "chapters": [{
                "label": "起源",
                "bucket_ids": bucket_ids or [],
                "date": now_iso(),
            }],
            "hidden_connections": [],
            "created": now_iso(),
            "last_updated": now_iso(),
        }
        return self._write_artifact("cocreate", metadata, content)

    async def add_cocreate_chapter(self, title: str, label: str, bucket_id: str) -> bool:
        """Add a new chapter to an existing co-create space."""
        path = self._index.get("cocreate", {}).get(title)
        if not path:
            return False

        artifact = self._read_artifact(path)
        if not artifact:
            return False

        meta = artifact["metadata"]
        chapters = meta.get("chapters", [])
        chapters.append({
            "label": label,
            "bucket_ids": [bucket_id],
            "date": now_iso(),
        })
        meta["chapters"] = chapters
        meta["last_updated"] = now_iso()

        post = frontmatter.Post(artifact["content"], **meta)
        with open(path, "w", encoding="utf-8") as f:
            f.write(frontmatter.dumps(post))
        return True

    async def list_cocreate(self) -> list[dict]:
        """List all co-create spaces."""
        results = []
        for title, path in self._index.get("cocreate", {}).items():
            artifact = self._read_artifact(path)
            if artifact:
                results.append(artifact)
        return results

    # =============================================================
    # 7. Worldview — 三观（只读，手动沉淀由 Drive 引擎接管）
    # =============================================================

    async def get_worldview(self, domain: str = "") -> list[dict]:
        """Get worldview statements, optionally filtered by domain."""
        results = []
        for d, path in self._index.get("worldview", {}).items():
            if not domain or d == domain:
                artifact = self._read_artifact(path)
                if artifact:
                    results.append(artifact)
        return results

    # =============================================================
    # 8. Cross-search — 跨进化产物搜索
    # =============================================================

    async def search_evolution(self, query: str) -> list[dict]:
        """Search across all evolution artifacts by keyword."""
        results = []
        query_lower = query.lower()
        for subdir_path in self.subdirs.values():
            if not os.path.exists(subdir_path):
                continue
            for root, _, files in os.walk(subdir_path):
                for filename in files:
                    if not filename.endswith(".md"):
                        continue
                    file_path = os.path.join(root, filename)
                    artifact = self._read_artifact(file_path)
                    if artifact:
                        meta_str = json.dumps(artifact["metadata"], ensure_ascii=False).lower()
                        content_str = artifact["content"].lower()
                        if query_lower in meta_str or query_lower in content_str:
                            results.append(artifact)
        return results

    # =============================================================
    # 9. Stats — 用于 pulse 工具和 Dashboard
    # =============================================================

    async def get_stats(self) -> dict:
        """Get evolution system statistics."""
        stats = {
            "auto_detection": "disabled (Drive engine handles this)",
            "api_configured": bool(self._client),
        }
        for key, path in self.subdirs.items():
            count = 0
            if os.path.exists(path):
                for root, _, files in os.walk(path):
                    count += sum(1 for f in files if f.endswith(".md") and not f.startswith("_"))
            stats[f"{key}_count"] = count
        return stats
