#!/usr/bin/env python3
"""把散在 traces 里的近义感受归成一族，产出一张**给人看的**表。

问题（08-30 实测 106 个窗口）：149 个不同的感受串，`canon()` 归到 103 个，
其中 **84 个只出现过一次**。而它们里面有大量是同一个感受的不同写法：

    满 / 满的 / 被填满了 / 被装满的
    暖 / 温暖 / 被爱包裹
    被她的耐心支撑着 / 被她的行动力推着往前走 / 被她的好奇心推着走

`canon()` 只做保守的语法处理（取头小句、剥程度前缀和「也是…的」外壳），
**故意不做近义合并** —— 那是语义判断，归错了比不归更坏。

所以语义这一步放在这里：离线跑一次，产出 `feeling_clusters.json`，
**她过目之后**再提交进仓库，`canon()` 只是查表。

这样换来三件事：
  1. `wear.py` 保持纯函数、不联网、不落盘 —— 每次读的结果永远可复现
  2. 合并哪些词**由人拍板**，不是模型在运行时偷偷决定
  3. 哪天觉得某一组并错了，改 JSON 就行，不用重跑模型

⚠️ **合并会改写整段历史。** 一旦「满」和「被填满了」并成一族，
   41 天的磨损计数全部重算 —— 所以这张表必须是人看过的。
   宁可少并几组，也不要并错一组：并错等于把两种不同的感受混成一件事，
   而磨损量的就是「什么反复回来」。

用法（默认**只看不写**）：
    python3 build_feeling_clusters.py                  # 演练：列出建议的分组
    python3 build_feeling_clusters.py --write          # 写出 feeling_clusters.json
    python3 build_feeling_clusters.py --min-count 1    # 连只出现一次的也纳入（默认就是）
"""

import argparse
import asyncio
import collections
import json
import os
import re
import sys

from openai import AsyncOpenAI

import wear

OUT_NAME = "feeling_clusters.json"

# 一次给模型看多少个词。太多它会敷衍，太少又切断了本该同族的词。
BATCH = 60

PROMPT = (
    "下面是一个人在不同时刻写下的「感受」词，来自同一段关系的记录。\n"
    "请把**说的是同一种感受**的词归到一组，并给每组起一个最简短的代表词。\n\n"
    "归组标准：\n"
    "  · 同一种感受的不同写法 → 同一组（例：满 / 满的 / 被填满了 / 被装满的）\n"
    "  · 只是程度或修饰不同 → 同一组（例：暖 / 温暖）\n"
    "  · 感受**不一样**就分开，哪怕都是正面的\n"
    "    （例：「满足」和「骄傲」是两种感受，不要合）\n"
    "    （例：「踏实」和「兴奋」都好，但不是一回事）\n\n"
    "⚠️ 宁可少归几组，也不要归错一组。拿不准就让它自己一组。\n"
    "⚠️ 代表词必须从这一组的词里**原样挑一个**，不要自己造新词。\n\n"
    "只输出纯 JSON：{\"组代表词\": [\"同组的词\", ...], ...}\n"
    "只出现一次、跟谁都不像的词**不要写进去**。"
)


def collect(traces_dir) -> collections.Counter:
    """traces 里所有 canon 之后的感受词及其次数。"""
    c = collections.Counter()
    for t in wear.read_traces(traces_dir):
        for key in ("peak", "primary", "secondary"):
            v = wear.canon(t.get(key))
            if v:
                c[v] += 1
    return c


async def _ask(client, model, words):
    resp = await client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": PROMPT},
                  {"role": "user", "content": "\n".join(words)}],
        max_tokens=2048, temperature=0.1,
        response_format={"type": "json_object"},
    )
    raw = (resp.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
    return json.loads(raw)


def _sanitize(groups: dict, known: set) -> dict:
    """把模型的输出收拾干净。防三件事：

    · 造了原始数据里没有的词 —— 那等于凭空发明一种感受
    · 一个词被塞进两组 —— 那样映射不唯一，counter 会算错
    · 一组只有一个成员 —— 那不叫归组，白占一行
    """
    out, taken = {}, set()
    for head, members in (groups or {}).items():
        if not isinstance(members, list):
            continue
        head = str(head).strip()
        ms = {str(m).strip() for m in members if str(m).strip()}
        ms.add(head)
        ms = {m for m in ms if m in known and m not in taken}
        if head not in ms:                 # 代表词被造出来了，从成员里另挑一个
            if not ms:
                continue
            head = sorted(ms, key=len)[0]
        if len(ms) < 2:
            continue
        for m in ms:
            taken.add(m)
        out[head] = sorted(ms)
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="写出 JSON（默认只演练）")
    ap.add_argument("--traces", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    from continuity_core import _traces_dir
    traces = args.traces or str(_traces_dir())
    counts = collect(traces)
    words = [w for w, _ in counts.most_common()]
    print(f"从 traces 收到 {len(words)} 个不同的感受词"
          f"（其中只出现一次的 {sum(1 for w in words if counts[w] == 1)} 个）。")
    if not words:
        return

    from utils import load_config
    dehy = load_config().get("dehydration", {})
    client = AsyncOpenAI(
        api_key=os.environ.get("OMBRE_API_KEY", "") or dehy.get("api_key", ""),
        base_url=dehy.get("base_url", "https://api.deepseek.com/v1"),
        timeout=120.0,
    )
    model = dehy.get("model", "deepseek-chat")

    known = set(words)
    merged = {}
    for i in range(0, len(words), BATCH):
        chunk = words[i:i + BATCH]
        try:
            got = await _ask(client, model, chunk)
        except Exception as e:
            print(f"  X 第 {i // BATCH + 1} 批失败：{e}")
            continue
        for head, ms in _sanitize(got, known).items():
            if head not in merged:
                merged[head] = ms
    merged = _sanitize(merged, known)

    print(f"\n建议合并 {len(merged)} 组，涉及 "
          f"{sum(len(v) for v in merged.values())} 个词：\n")
    for head in sorted(merged, key=lambda h: -sum(counts[m] for m in merged[h])):
        ms = merged[head]
        total = sum(counts[m] for m in ms)
        was = counts[head]
        print(f"  {head}（{was} → {total} 次）")
        print("      " + "、".join(f"{m}×{counts[m]}" for m in ms))

    if not args.write:
        print("\n【演练】什么都没写。看过没问题再加 --write。")
        print("⚠️ 合并会**改写整段历史** —— 41 天的磨损计数全部重算。")
        print("   哪一组不对就先别写，或者写完手改 JSON 删掉那一组。")
        return

    out = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), OUT_NAME)
    # 存成 {同组的词: 代表词} —— canon() 那边是按词查，这样查一次就够
    flat = {m: head for head, ms in merged.items() for m in ms}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(flat, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"\n写好了：{out}")
    print("⚠️ 提交进仓库之前**再看一遍这个文件** —— 它会改写整段磨损历史。")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
