"""单元自测：受限语法解析、纯 Python 判定、z3 全量兼容判定、报文编解码。
运行：python3 tests/test_units.py
"""
import itertools
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from codec import CodecError, build_codec  # noqa: E402
from schema_spec import SchemaError, message_valid, parse_schema  # noqa: E402
from solver import check_compatibility, find_counterexample  # noqa: E402

PASS = []


def ok(name, cond):
    assert cond, f"FAILED: {name}"
    PASS.append(name)


def expect_error(name, doc):
    try:
        parse_schema(doc)
    except SchemaError:
        PASS.append(name)
        return
    raise AssertionError(f"FAILED(应拒绝但未拒绝): {name}")


def S(doc):
    return parse_schema(doc)


# ---------- 语法子集：应接受 ----------
ok("平面对象", S({"type": "object", "properties": {"a": {"type": "integer", "minimum": 1, "maximum": 5}},
                  "required": ["a"], "additionalProperties": False}))
ok("顶层anyOf", S({"anyOf": [
    {"type": "object", "properties": {"a": {"type": "integer"}}, "additionalProperties": False},
    {"type": "object", "properties": {"b": {"enum": ["x", None]}}, "required": ["b"],
     "additionalProperties": False}]}))
ok("null类型", S({"type": "object", "properties": {"q": {"type": "null"}}, "additionalProperties": False}))
ok("混合枚举", S({"type": "object", "properties": {"q": {"enum": ["N", 1, None]}}, "additionalProperties": False}))
ok("整数枚举", S({"type": "object", "properties": {"q": {"type": "integer", "enum": [1, 2]}},
                  "additionalProperties": False}))
ok("无required", S({"type": "object", "properties": {"a": {"type": "integer"}}, "additionalProperties": False}))
ok("无界整数", S({"type": "object", "properties": {"a": {"type": "integer"}}, "additionalProperties": False}))

# ---------- 语法子集：应拒绝 ----------
expect_error("嵌套对象", {"type": "object", "properties": {"a": {"type": "object", "properties": {}}},
                          "additionalProperties": False})
expect_error("数组", {"type": "object", "properties": {"a": {"type": "array", "items": {}}},
                     "additionalProperties": False})
expect_error("pattern", {"type": "object", "properties": {"a": {"type": "string", "pattern": "^x"}},
                         "additionalProperties": False})
expect_error("纯string无enum", {"type": "object", "properties": {"a": {"type": "string"}},
                               "additionalProperties": False})
expect_error("number类型", {"type": "object", "properties": {"a": {"type": "number"}},
                            "additionalProperties": False})
expect_error("布尔枚举", {"type": "object", "properties": {"a": {"enum": [True]}},
                          "additionalProperties": False})
expect_error("浮点枚举", {"type": "object", "properties": {"a": {"enum": [1.5]}},
                          "additionalProperties": False})
expect_error("缺additionalProperties", {"type": "object", "properties": {}})
expect_error("additionalProperties为true", {"type": "object", "properties": {}, "additionalProperties": True})
expect_error("min大于max", {"type": "object", "properties": {"a": {"type": "integer", "minimum": 5, "maximum": 1}},
                            "additionalProperties": False})
expect_error("required未声明", {"type": "object", "properties": {}, "required": ["x"],
                                "additionalProperties": False})
expect_error("enum为空", {"type": "object", "properties": {"a": {"enum": []}}, "additionalProperties": False})
expect_error("enum与界并存", {"type": "object", "properties": {"a": {"type": "integer", "minimum": 1, "enum": [1]}},
                              "additionalProperties": False})
expect_error("anyOf混用", {"anyOf": [{"type": "object", "properties": {}, "additionalProperties": False}],
                            "type": "object"})
expect_error("anyOf分支非对象", {"anyOf": [{"type": "string"}]})
expect_error("顶层非对象", [1, 2])
expect_error("oneOf", {"oneOf": [{"type": "object", "properties": {}, "additionalProperties": False}]})
expect_error("type与enum不一致", {"type": "object", "properties": {"a": {"type": "string", "enum": [1]}},
                                  "additionalProperties": False})

# ---------- 纯 Python 判定 ----------
sch = S({"type": "object",
         "properties": {"t": {"type": "integer", "minimum": 0, "maximum": 10},
                        "q": {"enum": ["N", None]}},
         "required": ["t"], "additionalProperties": False})
ok("判定:合法报文", message_valid({"t": 5}, sch))
ok("判定:带null", message_valid({"t": 5, "q": None}, sch))
ok("判定:越界拒绝", not message_valid({"t": 11}, sch))
ok("判定:多字段拒绝", not message_valid({"t": 5, "x": 1}, sch))
ok("判定:缺必填拒绝", not message_valid({"q": "N"}, sch))
ok("判定:布尔不是整数", not message_valid({"t": True}, sch))
ok("判定:字符串枚举", message_valid({"t": 5, "q": "N"}, sch))
ok("判定:非法枚举值", not message_valid({"t": 5, "q": "Z"}, sch))

# ---------- z3 全量判定 ----------
def fw(lo, hi):
    return S({"type": "object", "properties": {"temp": {"type": "integer", "minimum": lo, "maximum": hi}},
              "required": ["temp"], "additionalProperties": False})


ok("z3:相同格式兼容", find_counterexample(fw(-10, 50), fw(-10, 50)) is None)
ok("z3:子集兼容", find_counterexample(fw(-10, 50), fw(-40, 100)) is None)
cex = find_counterexample(fw(-40, 100), fw(-10, 50))
ok("z3:扩范围给出反例", cex is not None and isinstance(cex.get("temp"), int)
   and -40 <= cex["temp"] <= 100 and not (-10 <= cex["temp"] <= 50))
ok("z3:反例确被读方拒收", cex is not None and message_valid(cex, fw(-40, 100))
   and not message_valid(cex, fw(-10, 50)))

# 质控码缺省：写方 qc 可选，读方 qc 必填
w_qc = S({"type": "object",
          "properties": {"temp": {"type": "integer", "minimum": -40, "maximum": 100},
                         "qc": {"enum": ["N", "Q", None]}},
          "required": ["temp"], "additionalProperties": False})
r_qc_require = S({"type": "object",
                  "properties": {"temp": {"type": "integer", "minimum": -40, "maximum": 100},
                                 "qc": {"enum": ["N", "Q"]}},
                  "required": ["temp", "qc"], "additionalProperties": False})
r_qc_optional = S({"type": "object",
                   "properties": {"temp": {"type": "integer", "minimum": -40, "maximum": 100},
                                  "qc": {"enum": ["N", "Q", None]}},
                   "required": ["temp"], "additionalProperties": False})
cex = find_counterexample(w_qc, r_qc_require)
ok("z3:qc必填反例", cex is not None and ("qc" not in cex or cex["qc"] is None))
ok("z3:qc可选兼容", find_counterexample(w_qc, r_qc_optional) is None)

# additionalProperties：写方多出的字段被读方拒收
w_extra = S({"type": "object",
             "properties": {"temp": {"type": "integer"}, "raw": {"type": "integer"}},
             "required": ["temp"], "additionalProperties": False})
r_no_extra = S({"type": "object", "properties": {"temp": {"type": "integer"}},
                "required": ["temp"], "additionalProperties": False})
cex = find_counterexample(w_extra, r_no_extra)
ok("z3:多余字段反例", cex is not None and "raw" in cex)

# anyOf：分支覆盖才兼容
w_any = S({"anyOf": [
    {"type": "object", "properties": {"a": {"type": "integer", "minimum": 0, "maximum": 5}},
     "required": ["a"], "additionalProperties": False},
    {"type": "object", "properties": {"b": {"enum": ["x"]}},
     "required": ["b"], "additionalProperties": False}]})
r_any_full = S({"anyOf": [
    {"type": "object", "properties": {"a": {"type": "integer", "minimum": 0, "maximum": 9}},
     "required": ["a"], "additionalProperties": False},
    {"type": "object", "properties": {"b": {"enum": ["x", "y"]}},
     "required": ["b"], "additionalProperties": False}]})
r_any_half = S({"anyOf": [
    {"type": "object", "properties": {"a": {"type": "integer", "minimum": 0, "maximum": 9}},
     "required": ["a"], "additionalProperties": False}]})
ok("z3:anyOf全覆盖兼容", find_counterexample(w_any, r_any_full) is None)
cex = find_counterexample(w_any, r_any_half)
ok("z3:anyOf漏分支反例", cex is not None and cex.get("b") == "x")

# 整数枚举 vs 范围
w_set = S({"type": "object", "properties": {"lv": {"type": "integer", "enum": [0, 1, 2, 3]}},
           "required": ["lv"], "additionalProperties": False})
r_range = S({"type": "object", "properties": {"lv": {"type": "integer", "minimum": 0, "maximum": 3}},
             "required": ["lv"], "additionalProperties": False})
r_range2 = S({"type": "object", "properties": {"lv": {"type": "integer", "minimum": 0, "maximum": 2}},
              "required": ["lv"], "additionalProperties": False})
ok("z3:枚举⊆范围", find_counterexample(w_set, r_range) is None)
cex = find_counterexample(w_set, r_range2)
ok("z3:枚举越界反例", cex is not None and cex.get("lv") == 3)

# check_compatibility 汇总
problems = check_compatibility([(1, fw(-40, 100))], [("p1", fw(-10, 50)), ("p2", fw(-40, 100))])
ok("z3:交叉检查定位拒收程序", len(problems) == 1 and problems[0]["program_id"] == "p1"
   and problems[0]["firmware_version"] == 1)


# ---------- 报文编解码 ----------

def expect_codec_error(name, doc):
    try:
        build_codec(S(doc))
    except CodecError:
        PASS.append(name)
        return
    raise AssertionError(f"FAILED(编解码应拒绝但未拒绝): {name}")


def flat(props, required=()):
    return {"type": "object", "properties": props, "required": list(required),
            "additionalProperties": False}


RANGE = lambda lo, hi: {"type": "integer", "minimum": lo, "maximum": hi}  # noqa: E731


def brute_force(codec, doc):
    """枚举全部候选报文（字段取缺省或各分支值域内任一值），用独立判定器筛出合法报文。"""
    MISSING = object()
    values = {}
    for f in codec.fields:
        vs = set()
        for doms in codec.branch_domains:
            if f in doms:
                dom = doms[f]
                if dom.lo is not None:
                    vs.update(range(dom.lo, dom.hi + 1))
                if dom.allow_null:
                    vs.add(None)
        values[f] = sorted(vs, key=lambda v: (v is not None, v))
    msgs = []
    for combo in itertools.product(*[[MISSING] + values[f] for f in codec.fields]):
        msgs.append({f: v for f, v in zip(codec.fields, combo) if v is not MISSING})
    valid = [m for m in msgs if message_valid(m, S(doc))]
    return valid


# 单分支：缺省 / null / 整数是不同状态
doc_flat = flat({"a": RANGE(0, 2), "b": {"enum": [None, 1]}}, required=["a"])
c = build_codec(S(doc_flat))
ok("编解码:合法报文总数", c.total == 9)
ok("编解码:最小位宽", c.width == 4 and c.total <= 2 ** c.width < 2 * c.total)
ok("编解码:缺省与null不同码",
   c.encode({"a": 0})["code"] != c.encode({"a": 0, "b": None})["code"])
for m in [{"a": 0}, {"a": 2, "b": 1}, {"a": 1, "b": None}]:
    e = c.encode(m)
    ok(f"编解码:往返 {m}", c.decode(e["code"])["message"] == m)
    ok(f"编解码:键顺序无关 {m}", c.encode(dict(reversed(list(m.items()))))["code"] == e["code"])

# 多分支重叠：同一报文只占一个码；码值与分支顺序无关
doc_any = {"anyOf": [
    flat({"a": RANGE(0, 3), "b": RANGE(0, 1)}, required=["a"]),
    flat({"a": RANGE(0, 1), "b": RANGE(0, 1)}, required=["a", "b"]),
    flat({"z": {"type": "null"}}, required=["z"]),
]}
c1 = build_codec(S(doc_any))
c2 = build_codec(S({"anyOf": list(reversed(doc_any["anyOf"]))}))
ok("编解码:anyOf 分支顺序不改变码", all(
    c1.encode(m)["code"] == c2.encode(m)["code"]
    for m in [{"a": 0}, {"a": 3}, {"a": 0, "b": 1}, {"z": None}]))
ok("编解码:重叠报文只占一个码", len({c1.encode({"a": 0, "b": 1})["code"],
                                     c1.encode({"a": 0})["code"]}) == 2)
ok("编解码:并集总数为互斥分区", c1.total == sum(c1.own_counts))

# 穷举核对：码是全部合法报文的一一映射，未用码值被明确拒绝
for label, doc in [("平面", doc_flat), ("anyOf", doc_any)]:
    codec = build_codec(S(doc))
    valid = brute_force(codec, doc)
    ok(f"穷举:{label}合法报文数一致", len(valid) == codec.total)
    codes = {}
    for m in valid:
        code = codec.encode(m)["code"]
        assert code not in codes, (m, code)
        codes[code] = m
        assert codec.decode(code)["message"] == m
    ok(f"穷举:{label}码一一对应且可逆", len(codes) == len(valid))
    ok(f"穷举:{label}位宽最小", len(codes) <= 2 ** codec.width < 2 * len(codes))
    bad = [format(i, f"0{codec.width}b") for i in range(2 ** codec.width)
           if format(i, f"0{codec.width}b") not in codes]
    rejected = 0
    for code in bad:
        try:
            codec.decode(code)
        except CodecError:
            rejected += 1
    ok(f"穷举:{label}未使用码值均判无效", rejected == len(bad))

# 大区间：不枚举报文也能编码（组合计数）
doc_big = flat({"t": RANGE(-10 ** 12, 10 ** 12), "h": RANGE(0, 100)}, required=["t"])
c = build_codec(S(doc_big))
ok("编解码:大区间总数", c.total == (2 * 10 ** 12 + 1) * 102)
m = {"t": -999999999999, "h": 100}
ok("编解码:大区间往返", c.decode(c.encode(m)["code"])["message"] == m)

# 错误与不支持格式
codec = build_codec(S(doc_flat))
for label, fn in [
    ("未知字段", lambda: codec.encode({"a": 1, "x": 2})),
    ("越界整数", lambda: codec.encode({"a": 3})),
    ("浮点", lambda: codec.encode({"a": 1.5})),
    ("布尔", lambda: codec.encode({"a": True})),
    ("缺必填", lambda: codec.encode({})),
    ("非对象报文", lambda: codec.encode([1])),
    ("非二进制码", lambda: codec.decode("01x")),
    ("位宽不符", lambda: codec.decode("01")),
    ("未使用码值", lambda: codec.decode("1111")),
    ("空码", lambda: codec.decode("")),
]:
    try:
        fn()
    except CodecError:
        PASS.append(f"编解码错误:{label}")
    else:
        raise AssertionError(f"FAILED(应报错): {label}")

expect_codec_error("编解码不支持:字符串字段", flat({"s": {"enum": ["x"]}}))
expect_codec_error("编解码不支持:单侧有界", flat({"t": {"type": "integer", "minimum": 0}}))
expect_codec_error("编解码不支持:无界整数", flat({"t": {"type": "integer"}}))
expect_codec_error("编解码不支持:不连续枚举",
                   flat({"t": {"type": "integer", "enum": [1, 3]}}))
expect_codec_error("编解码不支持:4个分支", {"anyOf": [
    flat({"a": RANGE(0, 1)}) for _ in range(4)]})
expect_codec_error("编解码不支持:7个字段", flat({f"f{i}": RANGE(0, 1) for i in range(7)}))
# 空对象格式只有唯一的合法报文 {}：位宽仍取最小正位宽 1
c_empty = build_codec(S(flat({})))
ok("编解码:空对象唯一报文", c_empty.total == 1 and c_empty.width == 1
   and c_empty.decode(c_empty.encode({})["code"])["message"] == {})

print(f"全部 {len(PASS)} 项单元自测通过")
