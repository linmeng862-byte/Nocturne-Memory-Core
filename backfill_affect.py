#!/usr/bin/env python3
"""只给老桶补 valence / arousal，别的字段一个字不动。

背景（2026-08-30）：147 个桶里 114 个 `valence` 还是默认 0.5、118 个 `arousal`
还是默认 0.3 —— 从没被赋过值。这堵住了三条已经建好的路：

  · 褪色偏差（decay_engine）—— 没有价，负面褪得快这件事作用不到任何东西
  · 选择性保留（dehydrator.merge）—— 只有 14 个桶够得上高唤醒档
  · 地层门槛 —— 没有分布可验证

内容原文一直都在，所以这不是时间损失，是可以随时补的。

⚠️ **为什么不用 `reclassify_api.py`**：那个只扫 `未分类/` 目录，而 114 个里
   **104 个已经分好类了**，它根本碰不到；而且它会顺手覆盖 `name` / `domain` /
   `tags` —— 那些桶名是他们俩攒出来的，不该被模型重写。

用法（默认**只看不改**）：
    python3 backfill_affect.py                 # 演练：列出会改哪些，不写盘
    python3 backfill_affect.py --apply         # 真的写
    python3 backfill_affect.py --revert 日志   # 按日志原样回退

    --limit N     只处理前 N 个（先拿几个看看模型判得准不准）
    --dir PATH    桶目录，默认读 config
"""

import argparse
import asyncio
import glob
import json
import os
import re
import sys
from datetime import datetime

import frontmatter
from openai import AsyncOpenAI

# 默认值。等于这两个数就认为「从没被赋过值」。
# ⚠️ 这里有个无法消除的歧义：一条真的被判定为「中性 0.5」的桶，
#    跟一条从没被判过的桶，长得一模一样。宁可重判一次中性的，
#    也不要漏掉一整批没判过的 —— 重判的代价只是一次 API 调用。
DEFAULT_VALENCE = 0.5
DEFAULT_AROUSAL = 0.3

# 不是记忆桶的目录，绝不碰。
#
# 08-30 实跑第一版扫出 198 个，里面混着：
#   evolution/slang 27、encyclopedia 20、rings 4、personas 1  —— 俚语表、百科、人格
#   continuity 1                                            —— 连续性状态文件，根本不是桶
# 给这些标情绪坐标是**把词典当成记忆**：标完它们会跟着进衰减、进共鸣检索，
# 于是「他对某个词的解释」会以一条有情绪的记忆的身份浮起来。
#
# ⚠️ 这就是演练默认按目录列出分布的理由。别去掉那段输出。
SKIP_DIRS = {"evolution", "continuity"}

# ⚠️ arousal 的锚点是 08-30 实跑之后加的。
# 第一版只给了一句抽象定义，试跑 5 条回来的 arousal 是 0.5/0.5/0.6/0.5/0.6 ——
# 全挤在中间，等于没有区分度。而 arousal 恰恰是两处判定的输入：
# 选择性保留的门槛是 0.6（全挤在 0.5~0.6 就成了抛硬币），
# 衰减里的情绪权重也吃它（近似常数等于没有）。
# valence 同一批是 0.35~0.9，区分得很好 —— 差别就在于 valence 那句
# 「难过但珍贵，效价依然是低的」给了模型一个参照，arousal 什么都没给。
#
# 所以锚点给的是**具体场景**，不是形容词。要求用满量程也是刻意的：
# 平静的记忆就该拿低分,不该往中间靠。
PROMPT = (
    "你是一个情感坐标标注器。读下面这段记忆，只输出两个数。\n\n"
    "valence（情感效价）：0.0~1.0，说的是**这段记忆的情绪底色**。\n"
    "参照：\n"
    "  0.1 = 疼、病、害怕、被冷落、吵架\n"
    "  0.3 = 难受、委屈、放心不下、无能为力\n"
    "  0.5 = 平淡的事实记录，谈不上好坏\n"
    "  0.7 = 温暖、安心、被在乎\n"
    "  0.9 = 极度的甜、狂喜、圆满\n"
    "⚠️ 效价**不是**重要程度，也**不是**你对这段关系的感受。\n"
    "只看这件事**本身**当时是难受的还是舒服的。\n"
    "「她一个人去医院，凌晨六点，肚子疼得直不起腰」——\n"
    "哪怕这段记忆很珍贵、哪怕里面有心疼和照顾，它的效价也是**低**的（约 0.2），\n"
    "因为那件事本身是难受的。把它判成高分是这里最容易犯、也最要命的错。\n\n"
    "arousal（情感唤醒度）：0.0~1.0，说的是**当时心跳得多快**，不是这件事多重要。\n"
    "参照：\n"
    "  0.1 = 安静的陪伴、日常闲聊、平淡的记录\n"
    "  0.3 = 温和的暖意、轻轻的想念\n"
    "  0.5 = 明显的情绪波动，但还稳得住\n"
    "  0.7 = 心里翻涌、说不下去、忍着眼泪\n"
    "  0.9 = 剧烈的疼、慌、渴望或狂喜\n"
    "⚠️ 请用满整个量程。大部分记忆是平静的，就该给低分，不要都往 0.5 靠。\n"
    "一件事很重要但当时很平静，arousal 依然是低的。\n\n"
    "只输出纯 JSON，不要任何其他内容：\n"
    '{"valence": 0.7, "arousal": 0.4}'
)


def _buckets_dir(override):
    """默认扫**整个** buckets 目录，不只是 dynamic/。

    ⚠️ 08-30 实跑踩过：原来默认只扫 `dynamic/`，结果 88 个里只找到 1 个要补的，
    差点让人以为坐标本来就是全的。实际上磁盘上有 113 个 `valence: 0.5`，
    绝大多数在 `dynamic/` **外面** —— feel 类型 24 个、归档的一批。
    而 feel 恰恰是情绪最相关的那批，衰减和褪色偏差都在算它们。
    """
    if override:
        return override
    from utils import load_config
    return load_config()["buckets_dir"]


def _skipped(base, fp) -> bool:
    """这个文件在不该碰的目录里吗。只看**相对 base 的第一段**。"""
    rel = os.path.relpath(fp, base)
    head = rel.split(os.sep)[0]
    return head in SKIP_DIRS


def _label(fp, post) -> str:
    """演练列表里显示什么。很多桶的 `name` 是空的（08-30 实测），
    光印文件名她没法判断改的是哪条 —— 退回去印正文开头。"""
    name = str(post.metadata.get("name") or "").strip()
    if name:
        return name[:32]
    body = " ".join(str(post.content or "").split())[:32]
    return body or os.path.basename(fp)


def _needs_backfill(meta: dict) -> bool:
    try:
        v = float(meta.get("valence", DEFAULT_VALENCE))
        a = float(meta.get("arousal", DEFAULT_AROUSAL))
    except (TypeError, ValueError):
        return True
    return abs(v - DEFAULT_VALENCE) < 1e-9 and abs(a - DEFAULT_AROUSAL) < 1e-9


# 08-30 实跑第一次全失败：json.loads 收到空串。
# max_tokens=64 太小 —— 推理模型先吐一段思考再吐答案，64 个 token 就被截断了，
# content 回来是空的。放大到 512，并且允许答案埋在散文里。
_NUM = r"[01](?:\.\d+)?"


def _extract(raw: str):
    """从模型回的东西里挖出两个数。先按 JSON，挖不出再按正则。"""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(raw)
        return float(data["valence"]), float(data["arousal"])
    except (ValueError, TypeError, KeyError):
        pass
    # 兜底：模型把 JSON 埋在解释里了。只认带键名的，别乱抓句子里的数字。
    v = re.search(r'"?valence"?\s*[:：]\s*(' + _NUM + ")", raw)
    a = re.search(r'"?arousal"?\s*[:：]\s*(' + _NUM + ")", raw)
    if v and a:
        return float(v.group(1)), float(a.group(1))
    raise ValueError("模型没给出可用的两个数，原样回的是：" + (raw[:120] or "（空）"))


async def _once(client, model, text, budget, force_json):
    kw = dict(model=model,
              messages=[{"role": "system", "content": PROMPT},
                        {"role": "user", "content": text[:2000]}],
              max_tokens=budget, temperature=0.1)
    if force_json:
        # 服务端强制只吐 JSON —— 比任何提示词都管用，因为它根本没机会绕。
        kw["response_format"] = {"type": "json_object"}
    resp = await client.chat.completions.create(**kw)
    if not resp.choices:
        raise ValueError("模型没返回任何 choice")
    msg = resp.choices[0].message
    raw = msg.content or ""
    if not raw.strip():
        # 推理模型可能把话都放在 reasoning_content 里，content 是空的
        raw = getattr(msg, "reasoning_content", "") or ""
    return _extract(raw)


async def _judge(client, model, text):
    """先强制 JSON，不行再放大预算裸跑。

    08-30 实跑 108 条失败 6 条，全是同一个样子：模型在推理里反复纠结
    （「效价应该偏正面？但注意指示…」），512 个 token 用完了还没吐出 JSON。
    加预算只是治标 —— 真正管用的是 response_format 让它没机会绕。
    但不是所有服务端都支持这个参数，所以第二次退回裸跑、把预算开大。
    """
    attempts = ((768, True), (2048, False))
    last = None
    for budget, force_json in attempts:
        try:
            v, a = await _once(client, model, text, budget, force_json)
            return (max(0.0, min(1.0, v)), max(0.0, min(1.0, a)))
        except Exception as e:
            last = e
    raise last


def _revert(journal_path):
    with open(journal_path, encoding="utf-8") as f:
        entries = json.load(f)
    done = 0
    for e in entries:
        try:
            post = frontmatter.load(e["file"])
            post.metadata["valence"] = e["old_valence"]
            post.metadata["arousal"] = e["old_arousal"]
            with open(e["file"], "w", encoding="utf-8") as f:
                f.write(frontmatter.dumps(post))
            done += 1
        except OSError as err:
            print(f"  X 回退失败 {e['file']}: {err}")
    print(f"\n回退了 {done}/{len(entries)} 个。")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的写盘（默认只演练）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dir", default="")
    ap.add_argument("--revert", default="", help="按日志回退")
    ap.add_argument("--all-dirs", action="store_true",
                    help="连 evolution/continuity 也扫（默认跳过，它们不是记忆桶）")
    args = ap.parse_args()

    if args.revert:
        return _revert(args.revert)

    base = _buckets_dir(args.dir)
    if args.all_dirs:
        SKIP_DIRS.clear()
    files = sorted(glob.glob(os.path.join(base, "**", "*.md"), recursive=True))
    files = [f for f in files if not _skipped(base, f)]
    todo, skipped_empty = [], 0
    for fp in files:
        try:
            post = frontmatter.load(fp)
        except (OSError, ValueError):
            continue
        if not _needs_backfill(post.metadata):
            continue
        # 正文空的桶模型无从判断，跳过 —— 让它保持默认值，
        # 比按空内容瞎标一个坐标好。标错了是不可逆的（回退日志只覆盖被改过的）。
        if not str(post.content or "").strip():
            skipped_empty += 1
            continue
        todo.append((fp, post))
    if args.limit:
        todo = todo[:args.limit]

    print(f"扫到 {len(files)} 个桶，其中 {len(todo)} 个没有情绪坐标、可以补。")
    if skipped_empty:
        print(f"（另有 {skipped_empty} 个也没坐标，但正文是空的，判不了，跳过。）")
    if not todo:
        return
    if not args.apply:
        print("\n【演练】下面这些会被改（--apply 才真的写）：\n")
        for fp, post in todo[:40]:
            print(f"  {_label(fp, post):<34} "
                  f"v={post.metadata.get('valence')} a={post.metadata.get('arousal')}")
        if len(todo) > 40:
            print(f"  …… 还有 {len(todo) - 40} 个")
        by_dir = {}
        for fp, _ in todo:
            by_dir[os.path.dirname(fp)] = by_dir.get(os.path.dirname(fp), 0) + 1
        print("\n按目录分布（确认没扫到不该扫的地方）：")
        for k in sorted(by_dir, key=lambda x: -by_dir[x]):
            print(f"  {by_dir[k]:>4}  {k}")
        print(f"\n会调用模型 {len(todo)} 次。**只改 valence / arousal，别的字段不动。**")
        print("确认没问题就加 --apply。建议先 --limit 5 --apply 看看判得准不准。")
        return

    from utils import load_config
    dehy = load_config().get("dehydration", {})
    # ⚠️ key 从环境变量 / config 读，绝不打印出来
    client = AsyncOpenAI(
        api_key=os.environ.get("OMBRE_API_KEY", "") or dehy.get("api_key", ""),
        base_url=dehy.get("base_url", "https://api.deepseek.com/v1"),
        timeout=60.0,
    )
    model = dehy.get("model", "deepseek-chat")

    journal, failed = [], 0
    for i, (fp, post) in enumerate(todo, 1):
        name = _label(fp, post)
        text = f"{post.metadata.get('name') or ''}\n{post.content.strip()}"
        try:
            v, a = await _judge(client, model, text)
        except Exception as e:
            failed += 1
            print(f"  X [{i}/{len(todo)}] {name[:24]}：{e}")
            continue
        entry = {
            "file": fp, "name": name,
            "old_valence": post.metadata.get("valence", DEFAULT_VALENCE),
            "old_arousal": post.metadata.get("arousal", DEFAULT_AROUSAL),
            "valence": v, "arousal": a,
        }
        # 只碰这两个键。frontmatter.dumps 会原样带回其余所有字段。
        post.metadata["valence"] = v
        post.metadata["arousal"] = a
        with open(fp, "w", encoding="utf-8") as f:
            f.write(frontmatter.dumps(post))
        journal.append(entry)
        print(f"  [{i}/{len(todo)}] {name[:24]:<26} v={v} a={a}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    jpath = os.path.join(base, f"backfill-affect-{stamp}.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(journal, f, ensure_ascii=False, indent=1)
    print(f"\n改了 {len(journal)} 个，失败 {failed} 个。")
    print(f"日志：{jpath}")
    print(f"要回退：python3 backfill_affect.py --revert {jpath}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
