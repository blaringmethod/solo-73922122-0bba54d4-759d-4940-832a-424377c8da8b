"""固件报文编解码：为某一版固件格式构造最小正位宽的定长二进制码。

支持范围（更复杂的格式一律给出明确原因）：
- 平面对象，或顶层 anyOf 至多 3 个分支；
- 全部分支合计至多 6 个不同字段名；
- 字段值为「双侧有界的整数区间」和/或 null（含只允许 null 的字段）。

设计要点：
- 报文集是各分支语言之并。先对分支做规范化排序（只取决于字段/区间，
  与 anyOf 数组顺序、JSON 键顺序无关），再按「首个接受该报文的分支」把并集
  划分成互不相交的 own 集；同一报文即使命中多个分支也只对应一个码。
- 合法报文总数、排名、反排名全部用组合计数（字段状态数的乘积 + 容斥）完成，
  不逐条枚举报文——区间可以很大（如 0..10^12），编码依旧即时完成。
- own 集内的顺序是字段字典序上的定长混合进制顺序：每个字段的状态依次为
  缺省 < null < 区间内整数升序。缺省与显式 null 因此是不同的状态、不同的码。
- 位宽 = max(1, ceil(log2(合法报文总数)))，即能容纳全部合法报文的最小正位宽；
  未被使用的码值在解码时会被明确判为无效码。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from schema_spec import FieldSpec, Schema, SchemaError, parse_schema

MAX_CODEC_BRANCHES = 3
MAX_CODEC_FIELDS = 6


class CodecError(ValueError):
    """该固件格式不支持编解码，或报文/码非法。"""


# ---------------------------------------------------------------------------
# 值域：双侧有界整数区间，可选允许 null
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Domain:
    lo: Optional[int] = None
    hi: Optional[int] = None
    allow_null: bool = False

    @property
    def has_ints(self) -> bool:
        return self.lo is not None

    @property
    def int_count(self) -> int:
        if self.lo is None:
            return 0
        return self.hi - self.lo + 1

    @property
    def present_size(self) -> int:
        return self.int_count + (1 if self.allow_null else 0)

    def accepts(self, v) -> bool:
        if v is None:
            return self.allow_null
        if isinstance(v, bool) or not isinstance(v, int):
            return False
        # 只允许 null 的字段，其「允许值」里没有任何整数
        return self.lo is not None and self.lo <= v <= self.hi

    def ints_below(self, v: int) -> int:
        """区间内严格小于 v 的整数个数。"""
        if self.lo is None or v <= self.lo:
            return 0
        if v > self.hi:
            return self.int_count
        return v - self.lo

    def describe(self) -> str:
        parts = []
        if self.lo is not None:
            parts.append(f"{self.lo}..{self.hi}")
        if self.allow_null:
            parts.append("null")
        return " | ".join(parts) if parts else "（无允许值）"


NULL_ONLY = Domain(None, None, True)


def _domain_from_spec(spec: FieldSpec, where: str) -> Domain:
    """把 FieldSpec 转成编解码支持的 Domain；不支持时抛 CodecError。"""
    if spec.str_set:
        raise CodecError(
            f"{where}：字段允许字符串值；编解码只支持双侧有界整数区间和 null")
    if spec.int_kind == "range":
        if spec.int_lo is None or spec.int_hi is None:
            raise CodecError(
                f"{where}：整数必须同时给出 minimum 和 maximum（双侧有界）")
        return Domain(spec.int_lo, spec.int_hi, spec.allow_null)
    if spec.int_kind == "set":
        ints = sorted(spec.int_set or ())
        if not ints:
            if spec.allow_null:
                return NULL_ONLY
            raise CodecError(f"{where}：字段没有任何允许值")
        if ints != list(range(ints[0], ints[-1] + 1)):
            raise CodecError(
                f"{where}：整数枚举不是连续区间；编解码只支持双侧有界整数区间")
        return Domain(ints[0], ints[-1], spec.allow_null)
    if spec.allow_null:
        return NULL_ONLY
    raise CodecError(f"{where}：字段没有任何允许值")


def _intersect_domains(domains) -> Optional[Domain]:
    """多个值域的交集；没有公共值时返回 None。"""
    has_ints = all(d.has_ints for d in domains)
    allow_null = all(d.allow_null for d in domains)
    lo = hi = None
    if has_ints:
        lo = max(d.lo for d in domains)
        hi = min(d.hi for d in domains)
        if lo > hi:
            has_ints = False
            lo = hi = None
    if not has_ints and not allow_null:
        return None
    return Domain(lo, hi, allow_null)


# ---------------------------------------------------------------------------
# 分支语言：每个字段的「缺省 / 必填 / 禁止」状态与值域
# ---------------------------------------------------------------------------

FORBID, OPTIONAL, REQUIRED = "forbid", "optional", "required"


def _state_count(mode: str, dom: Optional[Domain]) -> int:
    if mode == FORBID:
        return 1
    n = dom.present_size
    return n if mode == REQUIRED else 1 + n


def _state_less(mode: str, dom: Optional[Domain], present_value) -> int:
    """目标为「字段出现且值为 present_value」时，该字段允许状态中比它小的个数。"""
    if mode == FORBID:
        return 1  # 只有「缺省」，而缺省小于任何出现状态
    base = 1 if mode == OPTIONAL else 0
    if present_value is None:
        return base  # 缺省（若有）< null
    return base + (1 if dom.allow_null else 0) + dom.ints_below(present_value)


def _lang_total(lang: dict, fields) -> int:
    total = 1
    for f in fields:
        n = _state_count(*lang[f])
        total *= n
    return total


def _lang_accepts(lang: dict, msg: dict) -> bool:
    for f in lang:
        mode, dom = lang[f]
        if f in msg:
            if mode == FORBID or not dom.accepts(msg[f]):
                return False
        elif mode == REQUIRED:
            return False
    return True


def _lang_prefix_count(lang: dict, msg: dict, fields) -> int:
    """lang 中按字段字典序严格小于 msg 的报文个数（msg 不必属于 lang）。"""
    suffix = [1] * (len(fields) + 1)
    for i in range(len(fields) - 1, -1, -1):
        suffix[i] = suffix[i + 1] * _state_count(*lang[fields[i]])

    count = 0
    for i, f in enumerate(fields):
        mode, dom = lang[f]
        if f in msg:
            v = msg[f]
            less = _state_less(mode, dom, v)
            equal_ok = mode != FORBID and dom.accepts(v)
        else:
            less = 0
            equal_ok = mode != REQUIRED
        if less:
            count += less * suffix[i + 1]
        if not equal_ok:
            break
    return count


def _subsets(items):
    """遍历 items 的全部子集（含空集）。最多 2^3 个。"""
    n = len(items)
    for mask in range(1 << n):
        yield [items[i] for i in range(n) if (mask >> i) & 1]


def _intersect_langs(langs, fields):
    """多个分支语言的交集；没有交集时返回 None。"""
    out = {}
    for f in fields:
        pairs = [lang[f] for lang in langs]
        modes = [m for m, _ in pairs]
        if FORBID in modes:
            if REQUIRED in modes:
                return None
            out[f] = (FORBID, None)
            continue
        dom = _intersect_domains([d for _, d in pairs])
        if dom is None:
            # 值域没有公共「出现时的取值」：字段只能缺省。
            # 任一分支必填则该分支交集为空。
            if REQUIRED in modes:
                return None
            out[f] = (FORBID, None)
            continue
        out[f] = (REQUIRED if REQUIRED in modes else OPTIONAL, dom)
    return out


# ---------------------------------------------------------------------------
# Codec
# ---------------------------------------------------------------------------

class Codec:
    """由单版固件格式确定的可逆定长编码器。"""

    def __init__(self, schema: Schema):
        raw_branches = list(schema.branches)
        if len(raw_branches) > MAX_CODEC_BRANCHES:
            raise CodecError(
                f"顶层 anyOf 有 {len(raw_branches)} 个分支，编解码至多支持 "
                f"{MAX_CODEC_BRANCHES} 个")
        if not raw_branches:
            raise CodecError("格式没有任何分支")

        all_fields = set()
        for b in raw_branches:
            all_fields |= set(b.properties)
        self.fields = sorted(all_fields)
        self._field_set = set(self.fields)
        if len(self.fields) > MAX_CODEC_FIELDS:
            raise CodecError(
                f"各分支合计有 {len(self.fields)} 个不同字段，编解码至多支持 "
                f"{MAX_CODEC_FIELDS} 个")

        # 每个分支每个字段的值域；不支持的形式在这里给出具体字段原因
        branch_domains = []
        for bi, b in enumerate(raw_branches):
            doms = {}
            for f, spec in b.properties.items():
                doms[f] = _domain_from_spec(spec, f"分支 {bi + 1} 的字段 {f!r}")
            branch_domains.append(doms)

        # 规范化分支语言（与 anyOf 数组顺序无关）：按字段名排序，
        # 只取决于字段名/必填性/值域
        lang_items = []
        for bi, b in enumerate(raw_branches):
            doms = branch_domains[bi]
            lang_items.append(tuple(
                (f,
                 REQUIRED if f in b.required else OPTIONAL,
                 doms[f].allow_null,
                 0 if doms[f].lo is None else 1,
                 doms[f].lo if doms[f].lo is not None else 0,
                 doms[f].hi if doms[f].hi is not None else 0)
                for f in sorted(b.properties)
            ))
        order = sorted(range(len(raw_branches)), key=lambda i: lang_items[i])
        self.branches = [raw_branches[i] for i in order]
        self.branch_domains = [branch_domains[i] for i in order]

        # 分支语言
        self.langs = []
        for b, doms in zip(self.branches, self.branch_domains):
            lang = {}
            for f in self.fields:
                if f in b.properties:
                    mode = REQUIRED if f in b.required else OPTIONAL
                    lang[f] = (mode, doms[f])
                else:
                    lang[f] = (FORBID, None)
            self.langs.append(lang)

        # own_i = 分支 i 中不被任何更早规范化分支接受的报文；并集按 own 集分区
        self.own_counts = []
        for i in range(len(self.langs)):
            cnt = 0
            for subset in _subsets(list(range(i))):
                langs = [self.langs[i]] + [self.langs[j] for j in subset]
                inter = _intersect_langs(langs, self.fields)
                if inter is None:
                    continue
                cnt += (-1) ** len(subset) * _lang_total(inter, self.fields)
            if cnt < 0:
                raise CodecError("内部错误：分支并集计数为负")
            self.own_counts.append(cnt)

        self.total = sum(self.own_counts)
        if self.total <= 0:
            raise CodecError("该格式没有任何合法报文，无法编解码")
        self.width = max(1, (self.total - 1).bit_length())

    # ------------------------------ 元信息 ------------------------------

    def info(self) -> dict:
        return {
            "supported": True,
            "width": self.width,
            "total": self.total,
            "branch_count": len(self.langs),
            "field_count": len(self.fields),
            "fields": [
                {
                    "name": f,
                    "domains": sorted(
                        {self.branch_domains[i][f].describe()
                         for i in range(len(self.branches))
                         if f in self.branch_domains[i]}
                    ),
                }
                for f in self.fields
            ],
        }

    # ------------------------------ 编码 ------------------------------

    def encode(self, message) -> dict:
        self._validate_message_shape(message)
        accepting = [i for i, lang in enumerate(self.langs)
                     if _lang_accepts(lang, message)]
        if not accepting:
            raise CodecError(self._why_no_branch(message))

        branch_idx = accepting[0]  # 规范化后的首个接受分支
        rank = self._own_prefix(branch_idx, message)
        index = sum(self.own_counts[:branch_idx]) + rank
        if not 0 <= index < self.total:  # pragma: no cover - 防御
            raise CodecError("内部错误：编码下标越界")
        return {
            "width": self.width,
            "code": format(index, f"0{self.width}b"),
            "index": index,
            "total": self.total,
            "branch": branch_idx + 1,
            "branch_count": len(self.langs),
            "message": {f: message[f] for f in self.fields if f in message},
        }

    def _validate_message_shape(self, message) -> None:
        if not isinstance(message, dict):
            raise CodecError("报文必须是 JSON 对象（平面对象），不能是数组或标量")
        unknown = [k for k in message if k not in self._field_set]
        if unknown:
            raise CodecError(
                f"报文含未声明字段 {sorted(unknown)}；该版本允许的字段为 "
                f"{self.fields}")

        for f, v in message.items():
            doms = [self.branch_domains[i][f]
                    for i in range(len(self.branches))
                    if f in self.branch_domains[i]]
            if any(d.accepts(v) for d in doms):
                continue
            allowed = " 或 ".join(sorted({d.describe() for d in doms}))
            if v is None:
                raise CodecError(f"字段 {f!r} 不接受 null（允许值：{allowed}）")
            if isinstance(v, bool):
                raise CodecError(f"字段 {f!r} 的值是布尔值；布尔值不是整数")
            if not isinstance(v, int):
                raise CodecError(
                    f"字段 {f!r} 的值 {v!r} 不是整数或 null（允许值：{allowed}）")
            raise CodecError(
                f"字段 {f!r} 的值 {v} 超出允许区间（允许值：{allowed}）")

    def _why_no_branch(self, message: dict) -> str:
        keys = set(message)
        best = None
        for i, b in enumerate(self.branches):
            missing = b.required - keys
            extra = keys - set(b.properties)
            score = len(missing) + len(extra)
            if best is None or score < best[0]:
                best = (score, i, missing, extra)
        _, i, missing, extra = best
        parts = []
        if missing:
            parts.append(f"缺少必填字段 {sorted(missing)}")
        if extra:
            parts.append(f"分支 {i + 1} 不允许字段 {sorted(extra)}")
        if not parts:
            parts.append("各字段的取值无法同时满足同一分支")
        return f"报文不匹配任何分支（分支 {i + 1}）：" + "；".join(parts)

    def _is_own(self, branch_idx: int, message: dict) -> bool:
        if not _lang_accepts(self.langs[branch_idx], message):
            return False
        return not any(_lang_accepts(self.langs[j], message)
                       for j in range(branch_idx))

    def _own_prefix(self, branch_idx: int, message: dict) -> int:
        """分支 branch_idx 的 own 集中，严格小于 message 的报文数（容斥）。"""
        total = 0
        for subset in _subsets(list(range(branch_idx))):
            langs = [self.langs[branch_idx]] + [self.langs[j] for j in subset]
            inter = _intersect_langs(langs, self.fields)
            if inter is None:
                continue
            total += (-1) ** len(subset) * _lang_prefix_count(
                inter, message, self.fields)
        return total

    # ------------------------------ 解码 ------------------------------

    def decode(self, code: str) -> dict:
        if not isinstance(code, str):
            raise CodecError("码必须是二进制字符串")
        s = code.strip()
        if not s:
            raise CodecError("码不能为空")
        if any(ch not in "01" for ch in s):
            raise CodecError("码只能由 0 和 1 组成（二进制串）")
        if len(s) != self.width:
            raise CodecError(
                f"该版本位宽为 {self.width} bit，码长度必须正好 {self.width} 位"
                f"（收到 {len(s)} 位）")
        index = int(s, 2)
        if index >= self.total:
            raise CodecError(
                f"码值 {index} 无效：该版本共有 {self.total} 个合法报文，"
                f"码值须在 0..{self.total - 1} 之间")

        base = 0
        branch_idx = None
        local = None
        for i, cnt in enumerate(self.own_counts):
            if index < base + cnt:
                branch_idx, local = i, index - base
                break
            base += cnt
        if branch_idx is None:  # pragma: no cover - 防御
            raise CodecError("内部错误：码值未落入任何分支")

        message = self._unrank_own(branch_idx, local)
        if not self._is_own(branch_idx, message):  # pragma: no cover - 防御
            raise CodecError("内部错误：解码结果不属于目标分支")
        if self._own_prefix(branch_idx, message) != local:  # pragma: no cover
            raise CodecError("内部错误：解码与编码不互逆")
        return {
            "message": {f: message[f] for f in self.fields if f in message},
            "width": self.width,
            "code": format(index, f"0{self.width}b"),
            "index": index,
            "total": self.total,
            "branch": branch_idx + 1,
        }

    def _unrank_branch(self, branch_idx: int, k: int) -> dict:
        """分支语言中按字段字典序第 k 个报文。"""
        lang = self.langs[branch_idx]
        digits = [0] * len(self.fields)
        rest = k
        for pos in range(len(self.fields) - 1, -1, -1):
            n = _state_count(*lang[self.fields[pos]])
            if n <= 0:  # pragma: no cover - 防御
                raise CodecError("内部错误：分支语言为空")
            digits[pos] = rest % n
            rest //= n
        if rest:  # pragma: no cover - 防御
            raise CodecError("内部错误：分支下标越界")

        message = {}
        for pos, f in enumerate(self.fields):
            mode, dom = lang[f]
            if mode == FORBID:
                continue
            digit = digits[pos]
            if mode == OPTIONAL:
                if digit == 0:
                    continue
                digit -= 1
            if dom.allow_null and digit == 0:
                message[f] = None
            else:
                offset = 1 if dom.allow_null else 0
                message[f] = dom.lo + (digit - offset)
        return message

    def _unrank_own(self, branch_idx: int, local_rank: int) -> dict:
        """own 集中第 local_rank 个报文：在分支语言上二分反排名。"""
        lo, hi = 0, _lang_total(self.langs[branch_idx], self.fields) - 1
        target = local_rank + 1
        while lo < hi:
            mid = (lo + hi) // 2
            msg = self._unrank_branch(branch_idx, mid)
            cumulative = self._own_prefix(branch_idx, msg)
            if self._is_own(branch_idx, msg):
                cumulative += 1
            if cumulative >= target:
                hi = mid
            else:
                lo = mid + 1
        return self._unrank_branch(branch_idx, lo)


def build_codec(schema: Schema) -> Codec:
    """构造编解码器；格式不受支持时抛 CodecError（附具体原因）。"""
    return Codec(schema)


def codec_summary(schema_doc) -> dict:
    """供页面展示的概要；格式不符合受限语法或不受支持时给出原因。"""
    try:
        schema = parse_schema(schema_doc)
        return build_codec(schema).info()
    except (SchemaError, CodecError) as e:
        return {"supported": False, "error": str(e)}
