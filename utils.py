# ============================================================
# Module: Common Utilities (utils.py)
# 模块：通用工具函数
#
# Provides config loading, logging init, path safety, ID generation, etc.
# 提供配置加载、日志初始化、路径安全校验、ID 生成等基础能力
#
# Depended on by: server.py, bucket_manager.py, dehydrator.py, decay_engine.py
# 被谁依赖：server.py, bucket_manager.py, dehydrator.py, decay_engine.py
# ============================================================

import os
import re
import json
import uuid
import yaml
import hashlib
import logging
from pathlib import Path
from datetime import datetime


def load_config(config_path: str = None) -> dict:
    """
    Load configuration file.
    加载配置文件。

    Priority: environment variables > config.yaml > built-in defaults.
    优先级：环境变量 > config.yaml > 内置默认值。
    """
    # --- Built-in defaults (fallback so it runs even without config.yaml) ---
    # --- 内置默认配置（兜底，保证即使没有 config.yaml 也能跑）---
    defaults = {
        "transport": "stdio",
        "log_level": "INFO",
        "buckets_dir": os.path.join(os.path.dirname(os.path.abspath(__file__)), "buckets"),
        "merge_threshold": 75,
        "dehydration": {
            "model": "deepseek-v4-flash",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "",
            "max_tokens": 1024,
            "temperature": 0.1,
        },
        "decay": {
            "lambda": 0.05,
            "threshold": 0.3,
            "check_interval_hours": 24,
            "emotion_weights": {
                "base": 1.0,
                "arousal_boost": 0.8,
            },
        },
        "matching": {
            "fuzzy_threshold": 50,
            "max_results": 5,
        },
    }

    # --- Load user config from YAML file ---
    # --- 从 YAML 文件加载用户自定义配置 ---
    if config_path is None:
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config.yaml"
        )

    config = defaults.copy()
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                file_config = yaml.safe_load(f) or {}
            if isinstance(file_config, dict):
                config = _deep_merge(defaults, file_config)
            else:
                logging.warning(
                    f"Config file is not a valid YAML dict, using defaults / "
                    f"配置文件不是有效的 YAML 字典，使用默认配置: {config_path}"
                )
        except yaml.YAMLError as e:
            logging.warning(
                f"Failed to parse config file, using defaults / "
                f"配置文件解析失败，使用默认配置: {e}"
            )

    # --- Environment variable overrides (highest priority) ---
    # --- 环境变量覆盖敏感/运行时配置（优先级最高）---
    env_api_key = os.environ.get("OMBRE_API_KEY", "")
    if env_api_key:
        config.setdefault("dehydration", {})["api_key"] = env_api_key

    env_base_url = os.environ.get("OMBRE_BASE_URL", "")
    if env_base_url:
        config.setdefault("dehydration", {})["base_url"] = env_base_url

    env_transport = os.environ.get("OMBRE_TRANSPORT", "")
    if env_transport:
        config["transport"] = env_transport

    env_buckets_dir = os.environ.get("OMBRE_BUCKETS_DIR", "")
    if env_buckets_dir:
        config["buckets_dir"] = env_buckets_dir

    # OMBRE_DEHYDRATION_MODEL (with OMBRE_MODEL alias) overrides dehydration.model
    env_dehy_model = os.environ.get("OMBRE_DEHYDRATION_MODEL", "") or os.environ.get("OMBRE_MODEL", "")
    if env_dehy_model:
        config.setdefault("dehydration", {})["model"] = env_dehy_model

    # OMBRE_DEHYDRATION_BASE_URL overrides dehydration.base_url
    env_dehy_base_url = os.environ.get("OMBRE_DEHYDRATION_BASE_URL", "")
    if env_dehy_base_url:
        config.setdefault("dehydration", {})["base_url"] = env_dehy_base_url

    # OMBRE_EMBEDDING_MODEL overrides embedding.model
    env_embed_model = os.environ.get("OMBRE_EMBEDDING_MODEL", "")
    if env_embed_model:
        config.setdefault("embedding", {})["model"] = env_embed_model

    # OMBRE_EMBEDDING_BASE_URL overrides embedding.base_url
    env_embed_base_url = os.environ.get("OMBRE_EMBEDDING_BASE_URL", "")
    if env_embed_base_url:
        config.setdefault("embedding", {})["base_url"] = env_embed_base_url

    # --- Ensure bucket storage directories exist ---
    # --- 确保记忆桶存储目录存在 ---
    buckets_dir = config["buckets_dir"]
    for subdir in ["permanent", "dynamic", "archive"]:
        os.makedirs(os.path.join(buckets_dir, subdir), exist_ok=True)

    return config


def _deep_merge(base: dict, override: dict) -> dict:
    """
    Deep-merge two dicts; override values take precedence.
    深度合并两个字典，override 的值覆盖 base。
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def setup_logging(level: str = "INFO") -> None:
    """
    Initialize logging system.
    初始化日志系统。

    Note: In MCP stdio mode, stdout is occupied by the protocol;
    logs must go to stderr.
    注意：MCP stdio 模式下 stdout 被协议占用，日志只能走 stderr。
    """
    log_level = getattr(logging, level.upper(), None)
    if not isinstance(log_level, int):
        log_level = logging.INFO

    logging.basicConfig(
        level=log_level,
        format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler()],  # StreamHandler defaults to stderr
    )


def generate_bucket_id() -> str:
    """
    Generate a unique bucket ID (12-char short UUID for readability).
    生成唯一的记忆桶 ID（12 位短 UUID，方便人类阅读）。
    """
    return uuid.uuid4().hex[:12]


def strip_wikilinks(text: str) -> str:
    """
    Remove Obsidian wikilink brackets: [[word]] → word
    去除 Obsidian 双链括号
    """
    return re.sub(r"\[\[([^\]]+)\]\]", r"\1", text) if text else text


def sanitize_name(name: str) -> str:
    """
    Sanitize bucket name, keeping only safe characters.
    Prevents path traversal attacks (e.g. ../../etc/passwd).
    清洗桶名称，只保留安全字符。防止路径遍历攻击。
    """
    if not isinstance(name, str):
        return "unnamed"
    cleaned = re.sub(r"[^\w\s\u4e00-\u9fff-]", "", name, flags=re.UNICODE)
    cleaned = cleaned.strip()[:80]
    return cleaned if cleaned else "unnamed"


def safe_path(base_dir: str, filename: str) -> Path:
    """
    Construct a safe file path, ensuring it stays within base_dir.
    Prevents directory traversal.
    构造安全的文件路径，确保最终路径始终在 base_dir 内部。
    """
    base = Path(base_dir).resolve()
    target = (base / filename).resolve()
    if not str(target).startswith(str(base)):
        raise ValueError(
            f"Path safety check failed / 路径安全检查失败: "
            f"{target} is not inside / 不在 {base} 内"
        )
    return target


def count_tokens_approx(text: str) -> int:
    """
    Rough token count estimate.
    粗略估算 token 数。

    Chinese ≈ 1 char = 1.5 tokens, English ≈ 1 word = 1.3 tokens.
    Used to decide whether dehydration is needed; precision not required.
    中文 ≈ 1字=1.5token，英文 ≈ 1词=1.3token。
    用于判断是否需要脱水压缩，不追求精确。
    """
    if not text:
        return 0
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    english_words = len(re.findall(r"[a-zA-Z]+", text))
    return int(chinese_chars * 1.5 + english_words * 1.3 + len(text) * 0.05)


def now_iso() -> str:
    """
    Return current time as ISO format string.
    返回当前时间的 ISO 格式字符串。
    """
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------
# Content identity (NOT similarity)
# 正文同一性判断（不是相似度）
#
# Used by the write paths to tell "the same write happened twice" apart from
# "a new, similar experience". Only the former may be deduplicated; the latter
# is history and must be kept. Keyed on the bytes, never on an embedding.
# 用来区分「同一次写入重试」和「一段新的相似经历」。
# 只有前者可以去重，后者是历史，必须留着。认字节，不认向量。
# ---------------------------------------------------------
def content_fingerprint(text: str) -> str:
    """
    Stable hash of a memory body, ignoring surrounding and trailing whitespace.
    正文指纹：忽略首尾空白和行尾空格后取 sha256。
    """
    normalized = "\n".join(
        line.rstrip() for line in str(text or "").strip().splitlines()
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def find_exact_duplicate(candidates: list, content: str):
    """
    Return the candidate bucket whose body is identical to `content`, else None.
    返回正文与 content 完全相同的桶；没有就返回 None。

    `candidates` are search results shaped like {"id", "content", "metadata"}.
    """
    target = content_fingerprint(content)
    for bucket in candidates or []:
        if not isinstance(bucket, dict):
            continue
        if content_fingerprint(bucket.get("content", "")) == target:
            return bucket
    return None


# ============================================================
# Cross-process serialization / 跨进程串行化
#
# Once this is a URL, "one memory" means several bodies writing to the same
# files at the same time. Every append-only guarantee in this codebase is a
# read-modify-write underneath, and a read-modify-write without a lock is a
# lost update waiting to happen — including the revision journal, whose whole
# job is to make sure a previous wording is recoverable.
# 一旦挂成网址，「一份记忆」就意味着好几个身体同时往同一批文件里写。
# 这个代码库里每一条 append-only 保证，底下都是一次读-改-写；
# 没有锁的读-改-写迟早丢一次更新 —— 包括那本流水账，
# 而它存在的全部意义就是保证旧说法换得回来。
#
# flock is advisory and POSIX-only. That is the right trade here: the data
# lives in plain Markdown files that the user also edits by hand in Obsidian,
# so the lock must never make a file unreadable to anything that ignores it.
# flock 是建议锁，且只在 POSIX 上有。这里这个取舍是对的：
# 数据就是一堆纯 Markdown，她自己也会在 Obsidian 里手动编辑，
# 所以锁绝不能让不理会它的程序读不了文件。
# ============================================================

import fcntl
import errno
import time as _time
from contextlib import contextmanager

LOCK_TIMEOUT_SECONDS = 10.0


class LockTimeout(TimeoutError):
    """Another body held this for too long. / 另一个身体占用太久。"""


@contextmanager
def exclusive(lock_target: str, timeout: float = LOCK_TIMEOUT_SECONDS):
    """Serialize access to `lock_target` across processes.

    Locks a sidecar `<path>.lock` rather than the file itself, so the payload
    can still be truncated, replaced, or renamed while held — and so a reader
    that knows nothing about locking is never blocked.
    锁的是旁边的 `<path>.lock` 而不是文件本身，这样持锁期间仍然可以
    截断/替换/重命名正文文件；也让完全不懂锁的读者永远不会被挡住。

    Times out instead of hanging forever: a wedged endpoint must not be able
    to freeze every other endpoint.
    会超时而不是永远挂着：一个卡死的端不该冻住其余所有端。
    """
    lock_path = str(lock_target) + ".lock"
    parent = os.path.dirname(lock_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    deadline = _time.monotonic() + max(0.0, timeout)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if _time.monotonic() >= deadline:
                    raise LockTimeout(
                        f"Could not acquire {lock_path} within {timeout}s / "
                        f"{timeout} 秒内没拿到锁"
                    )
                _time.sleep(0.02)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def append_jsonl(path: str, row: dict) -> None:
    """Append one journal line durably, serialized against other writers.
    追加一行流水账，落盘且与其他写入者串行。"""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with exclusive(path):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())


def write_atomic(path: str, text: str) -> None:
    """Replace a file's contents all-at-once, or not at all.

    `open(path, "w")` truncates first. Losing power between the truncate and
    the write does not corrupt the memory — it deletes it, leaving a zero-byte
    file where a memory used to be.
    `open(path, "w")` 先截断。截断和写入之间掉电不是把记忆写坏，是把它写没了 ——
    原地留下一个 0 字节文件。
    """
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = os.path.join(parent, f".{os.path.basename(path)}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
