# ============================================================
# Module: Memory Bucket Manager (bucket_manager.py)
# 模块：记忆桶管理器
#
# CRUD operations, multi-dimensional index search, activation updates
# for memory buckets.
# 记忆桶的增删改查、多维索引搜索、激活更新。
#
# Core design:
# 核心逻辑：
#   - Each bucket = one Markdown file (YAML frontmatter + body)
#     每个记忆桶 = 一个 Markdown 文件
#   - Storage by type: permanent / dynamic / archive
#     存储按类型分目录
#   - Multi-dimensional soft index: domain + valence/arousal + fuzzy text
#     多维软索引：主题域 + 情感坐标 + 文本模糊匹配
#   - Search strategy: domain pre-filter → weighted multi-dim ranking
#     搜索策略：主题域预筛 → 多维加权精排
#   - Emotion coordinates based on Russell circumplex model:
#     情感坐标基于环形情感模型（Russell circumplex）：
#       valence (0~1): 0=negative → 1=positive
#       arousal (0~1): 0=calm → 1=excited
#
# Depended on by: server.py, decay_engine.py
# 被谁依赖：server.py, decay_engine.py
# ============================================================

import os
import io
import json
import math
import uuid
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

import frontmatter
from rapidfuzz import fuzz

from utils import (generate_bucket_id, sanitize_name, safe_path, now_iso,
                   content_fingerprint, exclusive, append_jsonl, write_atomic,
                   LockTimeout)

logger = logging.getLogger("ombre_brain.bucket")


# ============================================================
# Mutation authority
# 改写权限
#
# Normal runtime has no permission to rewrite a memory body. Bodies are
# history: once written they are what happened, and an agent deciding it
# would phrase things differently today is not a reason to edit the past.
# 普通运行时没有改写记忆正文的权限。正文就是历史 ——
# 写下来就是发生过的样子，agent 今天想换个说法不是修改过去的理由。
#
# An explicit human edit is still allowed, but it can never be silent: the
# previous body is journalled first, so every rewrite is recoverable and
# every rewrite can be asked "who did this, when, and why".
# 人显式修改仍然允许，但绝不允许静默：旧正文先入流水账，
# 每一次改写都可回滚、都能回答「谁改的、什么时候、为什么」。
# ============================================================

class StaleWrite(RuntimeError):
    """The body changed under you between reading it and editing it.
    读到它和改它之间，正文被别人动过了。"""


class HistoryProtected(PermissionError):
    """Raised when something tries to rewrite a memory body without authority."""


class BucketRevision:
    """One auditable body edit. Everything here is required — a revision that
    cannot say who and why is not an audit record, it is just an overwrite.
    一次可审计的正文修改。字段都是必填的：
    说不出谁改的、为什么改的，那不是审计记录，那只是覆盖。"""

    def __init__(self, actor: str, reason: str, expected_hash: str = ""):
        self.actor = str(actor or "").strip()
        self.reason = str(reason or "").strip()
        # Optional compare-and-swap. A caller that read the body first can pin
        # the wording it actually saw; if someone else edited in between the
        # edit is refused, instead of silently overwriting a body this caller
        # never read. The lock keeps the journal honest about what *was*
        # overwritten; this keeps the editor honest about what they meant to.
        # 可选的 compare-and-swap。先读过正文的调用方可以钉住自己真正看到的那份；
        # 中间被别人改过就拒绝，而不是默默覆盖一份它从没读过的正文。
        # 锁保证流水账诚实地记下「被覆盖的是什么」；
        # 这条保证「改的人」诚实地知道自己覆盖的是什么。
        self.expected_hash = str(expected_hash or "").strip()
        if not self.actor or not self.reason:
            raise HistoryProtected(
                "A body edit needs both actor and reason / 改正文必须给出 actor 和 reason"
            )


class BucketManager:
    """
    Memory bucket manager — entry point for all bucket CRUD operations.
    Buckets are stored as Markdown files with YAML frontmatter for metadata
    and body for content. Natively compatible with Obsidian browsing/editing.
    记忆桶管理器 —— 所有桶的 CRUD 操作入口。
    桶以 Markdown 文件存储，YAML frontmatter 存元数据，正文存内容。
    天然兼容 Obsidian 直接浏览和编辑。
    """

    def __init__(self, config: dict, embedding_engine=None):
        # --- Read storage paths from config / 从配置中读取存储路径 ---
        self.base_dir = config["buckets_dir"]
        self.permanent_dir = os.path.join(self.base_dir, "permanent")
        self.dynamic_dir = os.path.join(self.base_dir, "dynamic")
        self.archive_dir = os.path.join(self.base_dir, "archive")
        self.feel_dir = os.path.join(self.base_dir, "feel")
        self.fuzzy_threshold = config.get("matching", {}).get("fuzzy_threshold", 50)
        self.max_results = config.get("matching", {}).get("max_results", 5)

        # --- Wikilink config / 双链配置 ---
        wikilink_cfg = config.get("wikilink", {})
        self.wikilink_enabled = wikilink_cfg.get("enabled", True)
        self.wikilink_use_tags = wikilink_cfg.get("use_tags", False)
        self.wikilink_use_domain = wikilink_cfg.get("use_domain", True)
        self.wikilink_use_auto_keywords = wikilink_cfg.get("use_auto_keywords", True)
        self.wikilink_auto_top_k = wikilink_cfg.get("auto_top_k", 8)
        self.wikilink_min_len = wikilink_cfg.get("min_keyword_len", 2)
        self.wikilink_exclude_keywords = set(wikilink_cfg.get("exclude_keywords", []))
        self.wikilink_stopwords = {
            "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
            "都", "一个", "上", "也", "很", "到", "说", "要", "去",
            "你", "会", "着", "没有", "看", "好", "自己", "这", "他", "她",
            "我们", "你们", "他们", "然后", "今天", "昨天", "明天", "一下",
            "the", "and", "for", "are", "but", "not", "you", "all", "can",
            "had", "her", "was", "one", "our", "out", "has", "have", "with",
            "this", "that", "from", "they", "been", "said", "will", "each",
        }
        self.wikilink_stopwords |= {w.lower() for w in self.wikilink_exclude_keywords}

        # --- Search scoring weights / 检索权重配置 ---
        scoring = config.get("scoring_weights", {})
        self.w_topic = scoring.get("topic_relevance", 4.0)
        self.w_emotion = scoring.get("emotion_resonance", 2.0)
        self.w_time = scoring.get("time_proximity", 1.5)
        self.w_importance = scoring.get("importance", 1.0)
        self.content_weight = scoring.get("content_weight", 1.0)  # body×1, per spec

        # --- Optional embedding engine for pre-filtering / 可选 embedding 引擎，用于预筛候选集 ---
        self.embedding_engine = embedding_engine

    # ---------------------------------------------------------
    # Create a new bucket
    # 创建新桶
    # Write content and metadata into a .md file
    # 将内容和元数据写入一个 .md 文件
    # ---------------------------------------------------------
    async def create(
        self,
        content: str,
        tags: list[str] = None,
        importance: int = 5,
        domain: list[str] = None,
        valence: float = 0.5,
        arousal: float = 0.3,
        bucket_type: str = "dynamic",
        name: str = None,
        pinned: bool = False,
        protected: bool = False,
        created_at: str = "",
        chord: str = "",
        signal: str = "",
        signal_hints: dict = None,
        drive_tags: dict = None,
    ) -> str:
        """
        Create a new memory bucket, return bucket ID.
        创建一个新的记忆桶，返回桶 ID。

        pinned/protected=True: bucket won't be merged, decayed, or have importance changed.
        Importance is locked to 10 for pinned/protected buckets.
        pinned/protected 桶不参与合并与衰减，importance 强制锁定为 10。
        """
        bucket_id = generate_bucket_id()
        bucket_name = sanitize_name(name) if name else None
        # feel buckets are allowed to have empty domain; others default to ["未分类"]
        if bucket_type == "feel":
            domain = domain if domain is not None else []
        else:
            domain = domain or ["未分类"]
        tags = tags or []
        linked_content = content  # wikilink injection disabled; LLM adds [[]] via prompt

        # --- Pinned/protected buckets: lock importance to 10 ---
        # --- 钉选/保护桶：importance 强制锁定为 10 ---
        if pinned or protected:
            importance = 10

        # --- Build YAML frontmatter metadata / 构建元数据 ---
        metadata = {
            "id": bucket_id,
            "name": bucket_name,
            "tags": tags,
            "domain": domain,
            "valence": max(0.0, min(1.0, valence)),
            "arousal": max(0.0, min(1.0, arousal)),
            "importance": max(1, min(10, importance)),
            "type": bucket_type,
            "created": created_at if created_at else now_iso(),
            "last_active": now_iso(),
            "activation_count": 0,
        }
        if chord:
            metadata["chord"] = str(chord).strip()
        if signal:
            metadata["signal"] = str(signal).strip()
        if signal_hints:
            metadata["signal_hints"] = signal_hints
        if drive_tags:
            metadata["drive_tags"] = drive_tags
        if pinned:
            metadata["pinned"] = True
        if protected:
            metadata["protected"] = True

        # --- Assemble Markdown file (frontmatter + body) ---
        # --- 组装 Markdown 文件 ---
        post = frontmatter.Post(linked_content, **metadata)

        # --- Choose directory by type + primary domain ---
        # --- 按类型 + 主题域选择存储目录 ---
        if bucket_type == "permanent" or pinned:
            type_dir = self.permanent_dir
            if pinned and bucket_type != "permanent":
                metadata["type"] = "permanent"
        elif bucket_type == "feel":
            type_dir = self.feel_dir
        else:
            type_dir = self.dynamic_dir
        if bucket_type == "feel":
            primary_domain = "沉淀物"  # feel subfolder name
        else:
            primary_domain = sanitize_name(domain[0]) if domain else "未分类"
        target_dir = os.path.join(type_dir, primary_domain)
        os.makedirs(target_dir, exist_ok=True)

        # --- Filename: readable_name_bucketID.md (Obsidian friendly) ---
        # --- 文件名：可读名称_桶ID.md ---
        if bucket_name and bucket_name != bucket_id:
            filename = f"{bucket_name}_{bucket_id}.md"
        else:
            filename = f"{bucket_id}.md"
        file_path = safe_path(target_dir, filename)

        try:
            write_atomic(file_path, frontmatter.dumps(post))
        except OSError as e:
            logger.error(f"Failed to write bucket file / 写入桶文件失败: {file_path}: {e}")
            raise

        logger.info(
            f"Created bucket / 创建记忆桶: {bucket_id} ({bucket_name}) → {primary_domain}/"
            + (" [PINNED]" if pinned else "") + (" [PROTECTED]" if protected else "")
        )
        return bucket_id

    # ---------------------------------------------------------
    # Read bucket content
    # 读取桶内容
    # Returns {"id", "metadata", "content", "path"} or None
    # ---------------------------------------------------------
    async def get(self, bucket_id: str) -> Optional[dict]:
        """
        Read a single bucket by ID.
        根据 ID 读取单个桶。
        """
        if not bucket_id or not isinstance(bucket_id, str):
            return None
        file_path = self._find_bucket_file(bucket_id)
        if not file_path:
            return None
        return self._load_bucket(file_path)

    # ---------------------------------------------------------
    # Move bucket between directories
    # 在目录间移动桶文件
    # ---------------------------------------------------------
    def _move_bucket(self, file_path: str, target_type_dir: str, domain: list[str] = None) -> str:
        """
        Move a bucket file to a new type directory, preserving domain subfolder.
        Returns new file path.
        """
        if os.path.normpath(target_type_dir) == os.path.normpath(self.feel_dir):
            primary_domain = "沉淀物"
        else:
            primary_domain = sanitize_name(domain[0]) if domain else "未分类"
        target_dir = os.path.join(target_type_dir, primary_domain)
        os.makedirs(target_dir, exist_ok=True)
        filename = os.path.basename(file_path)
        new_path = safe_path(target_dir, filename)
        if os.path.normpath(file_path) != os.path.normpath(new_path):
            os.rename(file_path, new_path)
            logger.info(f"Moved bucket / 移动记忆桶: {filename} → {target_dir}/")
        return new_path

    # ---------------------------------------------------------
    # Update bucket
    # 更新桶
    # Supports: content, tags, importance, valence, arousal, name, resolved
    # ---------------------------------------------------------
    # ---------------------------------------------------------
    # Revision journal — append-only, next to the buckets
    # 修改流水账 —— append-only，跟记忆放在一起
    #
    # Stores the FULL previous body, not just its hash. A hash proves that
    # something changed; it does not give the old wording back, and giving the
    # old wording back is the entire point.
    # 存的是**完整旧正文**，不只是 hash。hash 只能证明「变过」，
    # 换不回原来的说法 —— 而换得回来才是这件事的意义。
    # ---------------------------------------------------------
    def _revisions_path(self) -> str:
        return os.path.join(self.base_dir, "revisions.jsonl")

    def _journal_revision(self, bucket_id: str, old_body: str, new_body: str,
                          revision: "BucketRevision") -> str:
        revision_id = uuid.uuid4().hex[:16]
        row = {
            "revision_id": revision_id,
            "bucket_id": bucket_id,
            "edited_at": now_iso(),
            "actor": revision.actor,
            "reason": revision.reason,
            "previous_hash": content_fingerprint(old_body),
            "new_hash": content_fingerprint(new_body),
            "previous_content": old_body,
        }
        # Journal first, edit second. If this raises, the body is not touched.
        # Serialized: two bodies journalling at once must not interleave.
        # 先记账再改。这里抛了，正文就不会被动。串行化：两个身体同时记账不能交错。
        append_jsonl(self._revisions_path(), row)
        logger.info(
            f"Body revision journalled / 正文修改已记账: {bucket_id} "
            f"rev={revision_id} by={revision.actor}"
        )
        return revision_id

    def revisions_for(self, bucket_id: str = "") -> list[dict]:
        """Read back the journal, newest last. Empty when nothing was ever edited.
        回读流水账，最新的在最后。从没改过就是空的。"""
        path = self._revisions_path()
        if not os.path.exists(path):
            return []
        rows = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not bucket_id or row.get("bucket_id") == bucket_id:
                    rows.append(row)
        return rows

    def _lock_target(self, bucket_id: str) -> str:
        """A per-bucket lock path that does not move when the bucket does.

        Buckets get relocated between permanent/dynamic/feel and between domain
        folders, so the file path is not a stable identity to lock on.
        桶会在 permanent/dynamic/feel 和各主题域目录之间搬家，
        所以文件路径不是一个可以拿来上锁的稳定身份。
        """
        return os.path.join(self.base_dir, ".locks", str(bucket_id))

    async def update(self, bucket_id: str, **kwargs) -> bool:
        """
        Update bucket content or metadata fields.
        更新桶的内容或元数据字段。

        The whole read-modify-write is serialized across processes. Without
        this, two endpoints updating the same memory means the later write
        silently discards the earlier one — and worse, the revision journal
        would record a "previous_content" that was never the text actually
        overwritten, quietly making the recoverability guarantee a lie.
        整个读-改-写跨进程串行。没有这个，两个端同时更新同一条记忆时，
        后写的会默默丢掉先写的 —— 更糟的是，流水账里记下的 previous_content
        会不是真正被覆盖掉的那份文本，让「换得回来」这个保证悄悄变成假话。
        """
        try:
            with exclusive(self._lock_target(bucket_id)):
                return await self._update_locked(bucket_id, **kwargs)
        except LockTimeout as e:
            logger.error(f"Update gave up waiting for lock / 等锁超时放弃更新: {bucket_id}: {e}")
            return False

    async def _update_locked(self, bucket_id: str, **kwargs) -> bool:
        preserve_last_active = bool(kwargs.pop("_preserve_last_active", False))
        revision = kwargs.pop("_revision", None)
        file_path = self._find_bucket_file(bucket_id)
        if not file_path:
            return False

        try:
            post = frontmatter.load(file_path)
        except Exception as e:
            logger.warning(f"Failed to load bucket for update / 加载桶失败: {file_path}: {e}")
            return False

        # --- Pinned/protected buckets: lock importance to 10, ignore importance changes ---
        # --- 钉选/保护桶：importance 不可修改，强制保持 10 ---
        is_pinned = post.get("pinned", False) or post.get("protected", False)
        if is_pinned:
            kwargs.pop("importance", None)  # silently ignore importance update

        # --- Body edits need authority, and are journalled before they land ---
        # --- 改正文需要授权，且落盘前先入流水账 ---
        if "content" in kwargs:
            old_body = post.content
            new_body = kwargs["content"]
            if content_fingerprint(old_body) == content_fingerprint(new_body):
                # Same text — nothing to authorize and nothing to journal.
                # 正文没变，不需要授权也不用记账。
                kwargs.pop("content")
            else:
                if not isinstance(revision, BucketRevision):
                    raise HistoryProtected(
                        f"Refusing to rewrite the body of {bucket_id} without a revision. "
                        f"Normal runtime is create-only; an explicit edit must pass "
                        f"_revision=BucketRevision(actor=…, reason=…). / "
                        f"拒绝无审计地改写 {bucket_id} 的正文。"
                    )
                # If the editor pinned the wording they read, honour it.
                # 如果改的人钉住了自己读到的那份说法，就认这个。
                if revision.expected_hash and \
                        revision.expected_hash != content_fingerprint(old_body):
                    raise StaleWrite(
                        f"{bucket_id} changed after you read it; refusing to overwrite "
                        f"a body you never saw. Re-read it and decide again. / "
                        f"{bucket_id} 在你读完之后被改过；拒绝覆盖一份你没看过的正文。"
                        f"重读一遍再决定。"
                    )
                self._journal_revision(bucket_id, old_body, new_body, revision)
                post.content = new_body
        if "tags" in kwargs:
            post["tags"] = kwargs["tags"]
        if "importance" in kwargs:
            post["importance"] = max(1, min(10, int(kwargs["importance"])))
        if "activation_count" in kwargs:
            post["activation_count"] = max(1, min(999, int(kwargs["activation_count"])))
        if "domain" in kwargs:
            post["domain"] = kwargs["domain"]
        if "valence" in kwargs:
            post["valence"] = max(0.0, min(1.0, float(kwargs["valence"])))
        if "arousal" in kwargs:
            post["arousal"] = max(0.0, min(1.0, float(kwargs["arousal"])))
        if "name" in kwargs:
            post["name"] = sanitize_name(kwargs["name"])
        if "resolved" in kwargs:
            post["resolved"] = bool(kwargs["resolved"])
        if "pinned" in kwargs:
            post["pinned"] = bool(kwargs["pinned"])
            if kwargs["pinned"]:
                post["importance"] = 10  # pinned → lock importance to 10
        if "digested" in kwargs:
            post["digested"] = bool(kwargs["digested"])
        if "model_valence" in kwargs:
            post["model_valence"] = max(0.0, min(1.0, float(kwargs["model_valence"])))
        if "signal" in kwargs:
            post["signal"] = str(kwargs["signal"]).strip()
        if "signal_hints" in kwargs:
            post["signal_hints"] = kwargs["signal_hints"]
        if "drive_tags" in kwargs:
            post["drive_tags"] = kwargs["drive_tags"]
        if "chord" in kwargs:
            post["chord"] = str(kwargs["chord"]).strip()
        if "created" in kwargs:
            post["created"] = kwargs["created"]
        if "type" in kwargs:
            bucket_type = str(kwargs["type"]).strip()
            if bucket_type not in {"dynamic", "permanent", "feel"}:
                logger.warning(f"Invalid bucket type / 非法记忆类型: {bucket_type}")
                return False
            post["type"] = bucket_type
            if bucket_type == "feel":
                post["domain"] = []
            elif not post.get("domain"):
                post["domain"] = ["未分类"]

        # --- Auto-refresh activation time / 自动刷新激活时间 ---
        if not preserve_last_active:
            post["last_active"] = now_iso()

        # --- Auto-move when type changes or pinned → permanent/ ---
        # --- 类型变化时同步移动文件；钉选仍进入 permanent/ ---
        domain = post.get("domain", ["未分类"])
        target_dir = None
        if kwargs.get("pinned") and post.get("type") != "permanent":
            post["type"] = "permanent"
            target_dir = self.permanent_dir
        elif "type" in kwargs:
            bucket_type = post.get("type", "dynamic")
            if bucket_type == "permanent":
                target_dir = self.permanent_dir
            elif bucket_type == "feel":
                target_dir = self.feel_dir
            else:
                target_dir = self.dynamic_dir

        try:
            write_atomic(file_path, frontmatter.dumps(post))
        except OSError as e:
            logger.error(f"Failed to write bucket update / 写入桶更新失败: {file_path}: {e}")
            return False

        if target_dir:
            self._move_bucket(file_path, target_dir, domain)

        logger.info(f"Updated bucket / 更新记忆桶: {bucket_id}")
        return True

    # ---------------------------------------------------------
    # Wikilink injection — DISABLED
    # 自动添加 Obsidian 双链 — 已禁用
    # Now handled by LLM prompts (Gemini adds [[]] for proper nouns)
    # 现在由 LLM prompt 处理（Gemini 对人名/地名/专有名词加 [[]]）
    # ---------------------------------------------------------
    # def _apply_wikilinks(self, content, tags, domain, name): ...
    # def _collect_wikilink_keywords(self, content, tags, domain, name): ...
    # def _normalize_keywords(self, keywords): ...
    # def _extract_auto_keywords(self, content): ...

    # ---------------------------------------------------------
    # Delete bucket
    # 删除桶
    # ---------------------------------------------------------
    async def delete(self, bucket_id: str) -> bool:
        """
        Delete a memory bucket file.
        删除指定的记忆桶文件。
        """
        file_path = self._find_bucket_file(bucket_id)
        if not file_path:
            return False

        try:
            os.remove(file_path)
        except OSError as e:
            logger.error(f"Failed to delete bucket file / 删除桶文件失败: {file_path}: {e}")
            return False

        logger.info(f"Deleted bucket / 删除记忆桶: {bucket_id}")
        return True

    # ---------------------------------------------------------
    # Touch bucket (refresh activation time + increment count)
    # 触碰桶（刷新激活时间 + 累加激活次数）
    # Called on every recall hit; affects decay score.
    # 每次检索命中时调用，影响衰减得分。
    # ---------------------------------------------------------
    async def touch(self, bucket_id: str) -> None:
        """
        Update a bucket's last activation time and count.
        Also triggers time ripple: nearby memories get a slight activation boost.
        更新桶的最后激活时间和激活次数。
        同时触发时间涟漪：时间上相邻的记忆轻微唤醒。
        """
        file_path = self._find_bucket_file(bucket_id)
        if not file_path:
            return

        try:
            # activation_count is a read-increment-write, and touch() fires on
            # every recall hit from every endpoint — the single most contended
            # write in the system. Unserialized, concurrent touches silently
            # undercount, and decay then treats well-used memories as neglected.
            # activation_count 是读-加-写，而 touch() 在每个端的每次召回命中时都会跑 ——
            # 全系统竞争最激烈的一次写。不串行的话并发触碰会默默少算，
            # 于是衰减会把用得最多的记忆当成没人理的。
            with exclusive(self._lock_target(bucket_id)):
                post = frontmatter.load(file_path)
                post["last_active"] = now_iso()
                post["activation_count"] = post.get("activation_count", 0) + 1
                write_atomic(file_path, frontmatter.dumps(post))

            # --- Time ripple: boost nearby memories within ±48h ---
            # --- 时间涟漪：±48小时内的记忆轻微唤醒 ---
            current_time = datetime.fromisoformat(str(post.get("created", post.get("last_active", ""))))
            await self._time_ripple(bucket_id, current_time)
        except Exception as e:
            logger.warning(f"Failed to touch bucket / 触碰桶失败: {bucket_id}: {e}")

    async def _time_ripple(self, source_id: str, reference_time: datetime, hours: float = 48.0) -> None:
        """
        Slightly boost activation_count of buckets created/activated near the reference time.
        轻微提升时间相邻桶的激活次数（+0.3），不改 last_active 避免递归唤醒。
        Max 5 buckets rippled per touch to bound I/O.
        """
        try:
            all_buckets = await self.list_all(include_archive=False)
        except Exception:
            return

        rippled = 0
        max_ripple = 5
        for bucket in all_buckets:
            if rippled >= max_ripple:
                break
            if bucket["id"] == source_id:
                continue
            meta = bucket.get("metadata", {})
            # Skip pinned/permanent/feel
            if meta.get("pinned") or meta.get("protected") or meta.get("type") in ("permanent", "feel"):
                continue

            created_str = meta.get("created", meta.get("last_active", ""))
            try:
                created = datetime.fromisoformat(str(created_str))
                delta_hours = abs((reference_time - created).total_seconds()) / 3600
            except (ValueError, TypeError):
                continue

            if delta_hours <= hours:
                # Boost activation_count by 0.3 (fractional), don't change last_active
                file_path = self._find_bucket_file(bucket["id"])
                if not file_path:
                    continue
                try:
                    post = frontmatter.load(file_path)
                    current_count = post.get("activation_count", 1)
                    # Store as float for fractional increments; calculate_score handles it
                    post["activation_count"] = round(current_count + 0.3, 1)
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(frontmatter.dumps(post))
                    rippled += 1
                except Exception:
                    continue

    # ---------------------------------------------------------
    # Multi-dimensional search (core feature)
    # 多维搜索（核心功能）
    #
    # Strategy: domain pre-filter → weighted multi-dim ranking
    # 策略：主题域预筛 → 多维加权精排
    #
    # Ranking formula:
    #   total = topic(×w_topic) + emotion(×w_emotion)
    #           + time(×w_time) + importance(×w_importance)
    #
    # Per-dimension scores (normalized to 0~1):
    #   topic     = rapidfuzz weighted match (name/tags/domain/body)
    #   emotion   = 1 - Euclidean distance (query v/a vs bucket v/a)
    #   time      = e^(-0.02 × days) (recent memories first)
    #   importance = importance / 10
    # ---------------------------------------------------------
    async def search(
        self,
        query: str,
        limit: int = None,
        domain_filter: list[str] = None,
        query_valence: float = None,
        query_arousal: float = None,
    ) -> list[dict]:
        """
        Multi-dimensional indexed search for memory buckets.
        多维索引搜索记忆桶。

        domain_filter: pre-filter by domain (None = search all)
        query_valence/arousal: emotion coordinates for resonance scoring
        """
        if not query or not query.strip():
            return []

        limit = limit or self.max_results
        all_buckets = await self.list_all(include_archive=False)

        if not all_buckets:
            return []

        # --- Layer 1: domain pre-filter (fast scope reduction) ---
        # --- 第一层：主题域预筛（快速缩小范围）---
        if domain_filter:
            filter_set = {d.lower() for d in domain_filter}
            candidates = [
                b for b in all_buckets
                if {d.lower() for d in b["metadata"].get("domain", [])} & filter_set
            ]
            # Fall back to full search if pre-filter yields nothing
            # 预筛为空则回退全量搜索
            if not candidates:
                candidates = all_buckets
        else:
            candidates = all_buckets

        # --- Layer 1.5: embedding pre-filter (optional, reduces multi-dim ranking set) ---
        # --- 第1.5层：embedding 预筛（可选，缩小精排候选集）---
        if self.embedding_engine and self.embedding_engine.enabled:
            try:
                vector_results = await self.embedding_engine.search_similar(query, top_k=50)
                if vector_results:
                    vector_ids = {bid for bid, _ in vector_results}
                    emb_candidates = [b for b in candidates if b["id"] in vector_ids]
                    if emb_candidates:  # only replace if there's non-empty overlap
                        candidates = emb_candidates
                    # else: keep original candidates as fallback
            except Exception as e:
                logger.warning(f"Embedding pre-filter failed, using fuzzy only / embedding 预筛失败: {e}")

        # --- Layer 2: weighted multi-dim ranking ---
        # --- 第二层：多维加权精排 ---
        scored = []
        for bucket in candidates:
            meta = bucket.get("metadata", {})

            try:
                # Dim 1: topic relevance (fuzzy text, 0~1)
                topic_score = self._calc_topic_score(query, bucket)

                # Dim 2: emotion resonance (coordinate distance, 0~1)
                emotion_score = self._calc_emotion_score(
                    query_valence, query_arousal, meta
                )

                # Dim 3: time proximity (exponential decay, 0~1)
                time_score = self._calc_time_score(meta)

                # Dim 4: importance (direct normalization)
                importance_score = max(1, min(10, int(meta.get("importance", 5)))) / 10.0

                # --- Weighted sum / 加权求和 ---
                total = (
                    topic_score * self.w_topic
                    + emotion_score * self.w_emotion
                    + time_score * self.w_time
                    + importance_score * self.w_importance
                )
                # Normalize to 0~100 for readability
                weight_sum = self.w_topic + self.w_emotion + self.w_time + self.w_importance
                normalized = (total / weight_sum) * 100 if weight_sum > 0 else 0

                # Threshold check uses raw (pre-penalty) score so resolved buckets
                # 阈值用原始分数判定，确保 resolved 桶在关键词命中时仍可被搜出
                # remain reachable by keyword (penalty applied only to ranking).
                if normalized >= self.fuzzy_threshold:
                    # Resolved buckets get ranking penalty (but still reachable by keyword)
                    # 已解决的桶仅在排序时降权
                    if meta.get("resolved", False):
                        normalized *= 0.3
                    bucket["score"] = round(normalized, 2)
                    scored.append(bucket)
            except Exception as e:
                logger.warning(
                    f"Scoring failed for bucket {bucket.get('id', '?')} / "
                    f"桶评分失败: {e}"
                )
                continue

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    # ---------------------------------------------------------
    # Topic relevance sub-score:
    # name(×3) + domain(×2.5) + tags(×2) + body(×1)
    # 文本相关性子分：桶名(×3) + 主题域(×2.5) + 标签(×2) + 正文(×1)
    # ---------------------------------------------------------
    def _calc_topic_score(self, query: str, bucket: dict) -> float:
        """
        Calculate text dimension relevance score (0~1).
        计算文本维度的相关性得分。
        """
        meta = bucket.get("metadata", {})

        name_score = fuzz.partial_ratio(query, meta.get("name", "")) * 3
        domain_score = (
            max(
                (fuzz.partial_ratio(query, d) for d in meta.get("domain", [])),
                default=0,
            )
            * 2.5
        )
        tag_score = (
            max(
                (fuzz.partial_ratio(query, tag) for tag in meta.get("tags", [])),
                default=0,
            )
            * 2
        )
        content_score = fuzz.partial_ratio(query, bucket.get("content", "")[:1000]) * self.content_weight

        return (name_score + domain_score + tag_score + content_score) / (100 * (3 + 2.5 + 2 + self.content_weight))

    # ---------------------------------------------------------
    # Emotion resonance sub-score:
    # Based on Russell circumplex Euclidean distance
    # 情感共鸣子分：基于环形情感模型的欧氏距离
    # No emotion in query → neutral 0.5 (doesn't affect ranking)
    # ---------------------------------------------------------
    def _calc_emotion_score(
        self, q_valence: float, q_arousal: float, meta: dict
    ) -> float:
        """
        Calculate emotion resonance score (0~1, closer = higher).
        计算情感共鸣度（0~1，越近越高）。
        """
        if q_valence is None or q_arousal is None:
            return 0.5  # No emotion coordinates → neutral / 无情感坐标时给中性分

        try:
            b_valence = float(meta.get("valence", 0.5))
            b_arousal = float(meta.get("arousal", 0.3))
        except (ValueError, TypeError):
            return 0.5

        # Euclidean distance, max sqrt(2) ≈ 1.414
        dist = math.sqrt((q_valence - b_valence) ** 2 + (q_arousal - b_arousal) ** 2)
        return max(0.0, 1.0 - dist / 1.414)

    # ---------------------------------------------------------
    # Time proximity sub-score:
    # More recent activation → higher score
    # 时间亲近子分：距上次激活越近分越高
    # ---------------------------------------------------------
    def _calc_time_score(self, meta: dict) -> float:
        """
        Calculate time proximity score (0~1, more recent = higher).
        计算时间亲近度。
        """
        last_active_str = meta.get("last_active", meta.get("created", ""))
        try:
            last_active = datetime.fromisoformat(str(last_active_str))
            days = max(0.0, (datetime.now() - last_active).total_seconds() / 86400)
        except (ValueError, TypeError):
            days = 30
        return math.exp(-0.02 * days)

    # ---------------------------------------------------------
    # List all buckets
    # 列出所有桶
    # ---------------------------------------------------------
    async def list_all(self, include_archive: bool = False) -> list[dict]:
        """
        Recursively walk directories (including domain subdirs), list all buckets.
        递归遍历目录（含域子目录），列出所有记忆桶。
        """
        buckets = []

        dirs = [self.permanent_dir, self.dynamic_dir, self.feel_dir]
        if include_archive:
            dirs.append(self.archive_dir)

        for dir_path in dirs:
            if not os.path.exists(dir_path):
                continue
            for root, _, files in os.walk(dir_path):
                for filename in files:
                    if not filename.endswith(".md"):
                        continue
                    file_path = os.path.join(root, filename)
                    bucket = self._load_bucket(file_path)
                    if bucket:
                        buckets.append(bucket)

        return buckets

    # ---------------------------------------------------------
    # Statistics (counts per category + total size)
    # 统计信息（各分类桶数量 + 总体积）
    # ---------------------------------------------------------
    async def get_stats(self) -> dict:
        """
        Return memory bucket statistics (including domain subdirs).
        返回记忆桶的统计数据。
        """
        stats = {
            "permanent_count": 0,
            "dynamic_count": 0,
            "archive_count": 0,
            "feel_count": 0,
            "total_size_kb": 0.0,
            "domains": {},
        }

        for subdir, key in [
            (self.permanent_dir, "permanent_count"),
            (self.dynamic_dir, "dynamic_count"),
            (self.archive_dir, "archive_count"),
            (self.feel_dir, "feel_count"),
        ]:
            if not os.path.exists(subdir):
                continue
            for root, _, files in os.walk(subdir):
                for f in files:
                    if f.endswith(".md"):
                        stats[key] += 1
                        fpath = os.path.join(root, f)
                        try:
                            stats["total_size_kb"] += os.path.getsize(fpath) / 1024
                        except OSError:
                            pass
                        # Per-domain counts / 每个域的桶数量
                        domain_name = os.path.basename(root)
                        if domain_name != os.path.basename(subdir):
                            stats["domains"][domain_name] = stats["domains"].get(domain_name, 0) + 1

        return stats

    # ---------------------------------------------------------
    # Archive bucket (move from permanent/dynamic into archive)
    # 归档桶（从 permanent/dynamic 移入 archive）
    # Called by decay engine to simulate "forgetting"
    # 由衰减引擎调用，模拟"遗忘"
    # ---------------------------------------------------------
    async def archive(self, bucket_id: str) -> bool:
        """
        Move a bucket into the archive directory (preserving domain subdirs).
        将指定桶移入归档目录（保留域子目录结构）。
        """
        file_path = self._find_bucket_file(bucket_id)
        if not file_path:
            return False

        try:
            # Read once, get domain info and update type / 一次性读取
            post = frontmatter.load(file_path)
            domain = post.get("domain", ["未分类"])
            primary_domain = sanitize_name(domain[0]) if domain else "未分类"
            archive_subdir = os.path.join(self.archive_dir, primary_domain)
            os.makedirs(archive_subdir, exist_ok=True)

            dest = safe_path(archive_subdir, os.path.basename(file_path))

            # Update type marker then move file / 更新类型标记后移动文件
            post["type"] = "archived"
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(frontmatter.dumps(post))

            # Use shutil.move for cross-filesystem safety
            # 使用 shutil.move 保证跨文件系统安全
            shutil.move(file_path, str(dest))
        except Exception as e:
            logger.error(
                f"Failed to archive bucket / 归档桶失败: {bucket_id}: {e}"
            )
            return False

        logger.info(f"Archived bucket / 归档记忆桶: {bucket_id} → archive/{primary_domain}/")
        return True

    # ---------------------------------------------------------
    # Internal: find bucket file across all three directories
    # 内部：在三个目录中查找桶文件
    # ---------------------------------------------------------
    def _find_bucket_file(self, bucket_id: str) -> Optional[str]:
        """
        Recursively search permanent/dynamic/archive for a bucket file
        matching the given ID.
        在 permanent/dynamic/archive 中递归查找指定 ID 的桶文件。
        """
        if not bucket_id:
            return None
        for dir_path in [self.permanent_dir, self.dynamic_dir, self.archive_dir, self.feel_dir]:
            if not os.path.exists(dir_path):
                continue
            for root, _, files in os.walk(dir_path):
                for fname in files:
                    if not fname.endswith(".md"):
                        continue
                    # Match by exact ID segment in filename
                    # 通过文件名中的 ID 片段精确匹配
                    name_part = fname[:-3]  # remove .md
                    if name_part == bucket_id or name_part.endswith(f"_{bucket_id}"):
                        return os.path.join(root, fname)
        return None

    # ---------------------------------------------------------
    # Internal: load bucket data from .md file
    # 内部：从 .md 文件加载桶数据
    # ---------------------------------------------------------
    def _load_bucket(self, file_path: str) -> Optional[dict]:
        """
        Parse a Markdown file and return structured bucket data.
        解析 Markdown 文件，返回桶的结构化数据。
        """
        try:
            post = frontmatter.load(file_path)
            return {
                "id": post.get("id", Path(file_path).stem),
                "metadata": dict(post.metadata),
                "content": post.content,
                "path": file_path,
            }
        except Exception as e:
            logger.warning(
                f"Failed to load bucket file / 加载桶文件失败: {file_path}: {e}"
            )
            return None
