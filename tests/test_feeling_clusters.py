"""语义合并表：离线生成、人过目、提交进仓库，canon() 只查表。

⚠️ 合并会改写整段历史 —— 一旦「满」和「被填满了」并成一族，
41 天的磨损计数全部重算。所以这张表必须是人看过的，
而且**表不在时行为必须跟没有这个功能时一模一样**。
"""
import importlib
import json
import os

import wear


def _with_table(tmp_path, data):
    """临时把表放进 wear 旁边，返回一个恢复函数。"""
    p = os.path.join(os.path.dirname(os.path.abspath(wear.__file__)),
                     wear._CLUSTER_FILE)
    existed = os.path.exists(p)
    backup = open(p, encoding="utf-8").read() if existed else None
    with open(p, "w", encoding="utf-8") as f:
        f.write(data)
    wear._clusters_cache = None

    def restore():
        if backup is None:
            os.remove(p)
        else:
            open(p, "w", encoding="utf-8").write(backup)
        wear._clusters_cache = None
    return restore


def test_no_table_means_no_change():
    """表不存在 = 跟没有这个功能时一模一样。她审过之前它一动不动。"""
    wear._clusters_cache = None
    p = os.path.join(os.path.dirname(os.path.abspath(wear.__file__)),
                     wear._CLUSTER_FILE)
    if not os.path.exists(p):
        assert wear.canon("被填满") == "被填满"


def test_the_table_merges():
    r = _with_table(None, json.dumps({"被填满": "满", "被装满": "满"},
                                     ensure_ascii=False))
    try:
        assert wear.canon("被填满") == "满"
        assert wear.canon("被装满了") == "满"      # 先剥「了」再查表
        assert wear.canon("满") == "满"
    finally:
        r()


def test_a_broken_table_is_ignored_whole():
    """半张表比没有表更糟：一部分历史被合并、一部分没有，计数就不可比了。"""
    for bad in ("不是json", "[1,2,3]", '"字符串"', "null"):
        r = _with_table(None, bad)
        try:
            assert wear.canon("被填满") == "被填满"
        finally:
            r()


def test_grammar_is_stripped_before_the_lookup():
    """先做完语法处理再查表——反过来的话表里得为每种写法各存一行。"""
    r = _with_table(None, json.dumps({"骄傲": "满"}, ensure_ascii=False))
    try:
        assert wear.canon("也是骄傲的") == "满"
    finally:
        r()
