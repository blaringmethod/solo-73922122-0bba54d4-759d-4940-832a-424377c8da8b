"""集成自测：对运行中的服务执行关键操作（默认 http://127.0.0.1:8000）。
运行：BASE_URL=http://127.0.0.1:8000 python3 tests/selftest.py
覆盖：两个内置示例全流程、非法语法拒绝、8 份上限、并发升级一致性、报文编解码、HTML 页面冒烟。
"""
import json
import os
import sys
import threading
import uuid

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from schema_spec import message_valid, parse_schema  # noqa: E402
from solver import check_compatibility  # noqa: E402

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
PASS = []


def ok(name, cond, extra=""):
    assert cond, f"FAILED: {name} {extra}"
    PASS.append(name)
    print(f"  ✔ {name}")


def temp_doc(lo, hi, qc=None, qc_required=False):
    props = {"temp": {"type": "integer", "minimum": lo, "maximum": hi},
             "site": {"type": "string", "enum": ["WQ-01", "WQ-02"]}}
    required = ["temp", "site"]
    if qc is not None:
        props["qc"] = {"enum": qc}
        if qc_required:
            required.append("qc")
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": False}


def main():
    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.get("/api/health")
    ok("健康检查", r.status_code == 200 and r.json()["status"] == "ok")

    model = f"t{uuid.uuid4().hex[:10]}"
    r = c.post("/api/models", json={"name": model})
    ok("创建型号", r.status_code == 201, r.text)

    def pub_fw(doc):
        return c.post(f"/api/models/{model}/firmware", json={"schema": doc})

    def pub_pg(pid, doc):
        return c.post(f"/api/models/{model}/programs",
                      json={"program_id": pid, "schema": doc})

    # ---- 示例一：温度扩范围 ----
    print("示例一：温度扩范围")
    r = pub_fw(temp_doc(-10, 50))
    ok("固件v1(-10~50)发布", r.status_code == 200 and r.json()["version"] == 1, r.text)
    r = pub_pg("analyzer", temp_doc(-10, 50))
    ok("程序analyzer旧版发布", r.status_code == 200, r.text)
    r = pub_fw(temp_doc(-40, 100))
    body = r.json()
    ok("固件v2(-40~100)被旧程序拒绝", r.status_code == 409 and not body["ok"], r.text)
    p = body["problems"][0]
    ok("拒绝依据含拒收程序", p["program_id"] == "analyzer", json.dumps(p))
    cex = p["counterexample"]
    ok("反例是合法报文且越旧界", message_valid(cex, parse_schema(temp_doc(-40, 100)))
       and not message_valid(cex, parse_schema(temp_doc(-10, 50))), json.dumps(cex))
    st = c.get(f"/api/models/{model}").json()
    ok("拒绝后已发布格式未变", len(st["firmwares"]) == 1 and len(st["programs"]) == 1)
    r = pub_pg("analyzer", temp_doc(-40, 100))
    ok("程序升级为-40~100", r.status_code == 200, r.text)
    r = pub_fw(temp_doc(-40, 100))
    ok("固件v2再次发布通过", r.status_code == 200 and r.json()["version"] == 2, r.text)

    # ---- 示例二：质控码缺省 ----
    print("示例二：质控码缺省")
    r = pub_pg("strict-qc", temp_doc(-40, 100, qc=["N", "Q"], qc_required=True))
    ok("程序strict-qc(qc必填)被拒", r.status_code == 409, r.text)
    cex = r.json()["problems"][0]["counterexample"]
    ok("反例为qc缺省或null", "qc" not in cex or cex["qc"] is None, json.dumps(cex))
    r = pub_pg("strict-qc", temp_doc(-40, 100, qc=["N", "Q", None]))
    ok("程序strict-qc(qc可选)通过", r.status_code == 200, r.text)
    r = pub_fw(temp_doc(-40, 100, qc=["N", "Q", None]))
    ok("固件v3被未声明qc的analyzer拒收", r.status_code == 409
       and r.json()["problems"][0]["program_id"] == "analyzer", r.text)
    ok("反例含qc字段", "qc" in r.json()["problems"][0]["counterexample"], r.text)
    r = pub_pg("analyzer", temp_doc(-40, 100, qc=["N", "Q", None]))
    ok("analyzer升级为qc可选", r.status_code == 200, r.text)
    r = pub_fw(temp_doc(-40, 100, qc=["N", "Q", None]))
    ok("固件v3(qc可缺省)发布", r.status_code == 200 and r.json()["version"] == 3, r.text)

    # ---- 报文编解码（可选版本、位宽、往返、不变量、非法输入说明） ----
    print("报文编解码")
    m3 = f"k{uuid.uuid4().hex[:10]}"
    assert c.post("/api/models", json={"name": m3}).status_code == 201

    def R(lo, hi):
        return {"type": "integer", "minimum": lo, "maximum": hi}

    # v1：平面对象，整数区间 + 仅 null 字段（缺省与显式 null 必须区分）
    codec_fw1 = {"type": "object",
                 "properties": {"temp": R(-40, 100), "qc": {"enum": [None]},
                                "site": R(1, 2)},
                 "required": ["temp"], "additionalProperties": False}
    r = c.post(f"/api/models/{m3}/firmware", json={"schema": codec_fw1})
    ok("编解码固件v1发布", r.status_code == 200 and r.json()["version"] == 1, r.text)
    info = c.get(f"/api/models/{m3}/codec/1")
    ok("编解码信息可用", info.status_code == 200 and info.json()["supported"], info.text)
    info = info.json()
    ok("编解码位宽最小", info["total"] <= 2 ** info["width"] < 2 * info["total"],
       json.dumps(info))
    ok("编解码合法报文数", info["total"] == 141 * 2 * 3, json.dumps(info))

    msg1 = {"temp": -40, "qc": None, "site": 2}
    msg2 = {"temp": -40, "site": 2}
    e1 = c.post(f"/api/models/{m3}/codec/1/encode", json={"message": msg1})
    e2 = c.post(f"/api/models/{m3}/codec/1/encode", json={"message": msg2})
    ok("编解码:编码成功", e1.status_code == 200 and e2.status_code == 200, e1.text)
    ok("编解码:缺省与显式null不同码", e1.json()["code"] != e2.json()["code"])
    ok("编解码:位宽与码长一致", len(e1.json()["code"]) == info["width"])
    d = c.post(f"/api/models/{m3}/codec/1/decode", json={"code": e1.json()["code"]})
    ok("编解码:解码还原JSON", d.status_code == 200 and d.json()["message"] == msg1, d.text)
    reordered = c.post(f"/api/models/{m3}/codec/1/encode",
                       json={"message": {"site": 2, "qc": None, "temp": -40}})
    ok("编解码:JSON键顺序无关", reordered.json()["code"] == e1.json()["code"])

    # v2：3 个 anyOf 分支，分支 2/3 的 {level:0}、{level:1} 重叠，只应占一码
    codec_fw2 = {"anyOf": [
        {"type": "object", "properties": {"temp": R(-40, 100), "qc": {"enum": [None]}},
         "required": ["temp"], "additionalProperties": False},
        {"type": "object", "properties": {"level": R(0, 3), "site": R(1, 2)},
         "required": ["level"], "additionalProperties": False},
        {"type": "object", "properties": {"level": R(0, 1)},
         "required": ["level"], "additionalProperties": False},
    ]}
    r = c.post(f"/api/models/{m3}/firmware", json={"schema": codec_fw2})
    ok("编解码固件v2(anyOf)发布", r.status_code == 200 and r.json()["version"] == 2, r.text)
    info2 = c.get(f"/api/models/{m3}/codec/2").json()
    ok("编解码:anyOf字段数", info2["field_count"] == 4 and info2["branch_count"] == 3,
       json.dumps(info2))
    # 分支1 有 141 个 temp × 2 种 qc 状态 = 282 封报文（不省略任何字段时）。
    # 分支2 有 4×3−? 分支3 有 2 个；{level:0} 与 {level:1} 同时被分支2、3接受。
    ok("编解码:anyOf合法报文数（并集去重）", info2["total"] == 282 + 14 - 2,
       json.dumps(info2))
    code_a = c.post(f"/api/models/{m3}/codec/2/encode",
                    json={"message": {"level": 0, "site": 1}}).json()
    code_b = c.post(f"/api/models/{m3}/codec/2/encode",
                    json={"message": {"site": 1, "level": 0}}).json()
    ok("编解码:同一报文只占一个码", code_a["code"] == code_b["code"])
    code_overlap = c.post(f"/api/models/{m3}/codec/2/encode",
                          json={"message": {"level": 1}}).json()
    ok("编解码:跨分支重叠报文也只占一码",
       c.post(f"/api/models/{m3}/codec/2/decode",
              json={"code": code_overlap["code"]}).json()["message"] == {"level": 1})
    ok("编解码:anyOf位宽最小",
       info2["total"] <= 2 ** info2["width"] < 2 * info2["total"], json.dumps(info2))
    for m in [{"level": 0}, {"level": 3, "site": 2}, {"level": 1},
              {"temp": 100, "qc": None}]:
        e = c.post(f"/api/models/{m3}/codec/2/encode", json={"message": m})
        assert e.status_code == 200, e.text
        back = c.post(f"/api/models/{m3}/codec/2/decode", json={"code": e.json()["code"]})
        ok(f"编解码:anyOf往返 {m}", back.status_code == 200 and back.json()["message"] == m,
           back.text)

    # v1 的码不受后续版本影响
    e1_again = c.post(f"/api/models/{m3}/codec/1/encode", json={"message": msg1})
    ok("编解码:后续版本不改变旧版编码", e1_again.json()["code"] == e1.json()["code"])

    # 非法报文 / 无效码 / 不支持的格式：均说明原因
    r = c.post(f"/api/models/{m3}/codec/1/encode",
               json={"message": {"temp": 1000}})
    ok("编解码:越界报文被拒且说明原因",
       r.status_code == 422 and "超出允许区间" in r.json()["error"], r.text)
    r = c.post(f"/api/models/{m3}/codec/1/encode",
               json={"message": {"temp": 1, "ghost": 2}})
    ok("编解码:未知字段被拒", r.status_code == 422 and "未声明字段" in r.json()["error"], r.text)
    r = c.post(f"/api/models/{m3}/codec/1/encode", json={"message": {"site": 1}})
    ok("编解码:缺必填被拒", r.status_code == 422 and "必填" in r.json()["error"], r.text)
    r = c.post(f"/api/models/{m3}/codec/1/encode", json={"message": {"temp": 1.5}})
    ok("编解码:非整数被拒", r.status_code == 422 and "不是整数" in r.json()["error"], r.text)
    r = c.post(f"/api/models/{m3}/codec/1/decode", json={"code": "0" * (info["width"] + 1)})
    ok("编解码:位宽不符被拒", r.status_code == 422 and "位宽" in r.json()["error"], r.text)
    r = c.post(f"/api/models/{m3}/codec/1/decode", json={"code": "2" * info["width"]})
    ok("编解码:非二进制被拒", r.status_code == 422 and "0 和 1" in r.json()["error"], r.text)
    unused = format(info["total"], f"0{info['width']}b")
    r = c.post(f"/api/models/{m3}/codec/1/decode", json={"code": unused})
    ok("编解码:未使用码值被拒", r.status_code == 422 and "无效" in r.json()["error"], r.text)

    codec_fw3 = {"type": "object",
                 "properties": {"site": {"enum": ["WQ-01"]}},
                 "additionalProperties": False}
    r = c.post(f"/api/models/{m3}/firmware", json={"schema": codec_fw3})
    ok("编解码:字符串字段格式仍可发布", r.status_code == 200, r.text)
    info3 = c.get(f"/api/models/{m3}/codec/3")
    ok("编解码:不支持的格式说明原因",
       info3.status_code == 422 and "字符串" in info3.json()["error"], info3.text)
    r = c.post(f"/api/models/{m3}/codec/3/encode", json={"message": {"site": "WQ-01"}})
    ok("编解码:不支持格式的编码请求说明原因",
       r.status_code == 422 and "不支持编解码" in r.json()["error"], r.text)
    r = c.post(f"/api/models/{m3}/codec/9/encode", json={"message": {}})
    ok("编解码:不存在的版本404", r.status_code == 404, r.text)

    # ---- 非法语法拒绝 ----
    print("非法语法")
    bad_docs = [
        {"type": "object", "properties": {"a": {"type": "object", "properties": {}}},
         "additionalProperties": False},
        {"type": "object", "properties": {"a": {"type": "string", "pattern": "^x"}},
         "additionalProperties": False},
        {"type": "object", "properties": {}, "additionalProperties": True},
        {"type": "object", "properties": {"a": {"enum": [True]}}, "additionalProperties": False},
        {"type": "object", "properties": {"a": {"type": "string"}}, "additionalProperties": False},
        {"anyOf": [{"type": "string"}]},
    ]
    for i, doc in enumerate(bad_docs):
        r = pub_fw(doc)
        ok(f"非法语法{i+1}被拒(422)", r.status_code == 422 and not r.json()["ok"], r.text)
    st = c.get(f"/api/models/{model}").json()
    ok("非法发布未改状态", len(st["firmwares"]) == 3)

    # ---- 上限：固件至多 8 份 ----
    print("上限检查")
    for lo, hi in [(-40, -30), (-29, -20), (-19, -10), (-9, 0), (1, 10)]:
        r = pub_fw(temp_doc(lo, hi))
        assert r.status_code == 200, r.text
    r = pub_fw(temp_doc(11, 20))
    ok("第9份固件被拒", r.status_code == 422 and "上限" in r.json()["message"], r.text)
    # ---- 上限：程序至多 8 个 ----
    for i in range(2, 8):  # 已有 analyzer、strict-qc 两个，再补 6 个到 8 个
        r = pub_pg(f"prog-{i}", temp_doc(-40, 100, qc=["N", "Q", None]))
        assert r.status_code == 200, r.text
    r = pub_pg("prog-9", temp_doc(-40, 100))
    ok("第9个程序号被拒", r.status_code == 422 and "上限" in r.json()["message"], r.text)
    r = pub_pg("prog-2", temp_doc(-40, 100, qc=["N", "Q", None]))
    ok("替换既有程序不受上限影响", r.status_code == 200, r.text)

    # ---- 日志留痕 ----
    logs = c.get(f"/api/models/{model}/log").json()["logs"]
    ok("日志含拒绝记录", any(l["result"] == "rejected" for l in logs))
    ok("日志含无效记录", any(l["result"] == "invalid" for l in logs))

    # ---- 并发升级：不能各自通过却留下不兼容组合 ----
    print("并发升级一致性")
    m2 = f"c{uuid.uuid4().hex[:10]}"
    assert c.post("/api/models", json={"name": m2}).status_code == 201
    assert c.post(f"/api/models/{m2}/firmware",
                  json={"schema": temp_doc(0, 50)}).status_code == 200
    assert c.post(f"/api/models/{m2}/programs",
                  json={"program_id": "P", "schema": temp_doc(0, 100)}).status_code == 200
    barrier = threading.Barrier(2)
    results = {}

    def narrow_program():  # A: 程序收窄到 0~50（与现有固件兼容）
        barrier.wait()
        r = c.post(f"/api/models/{m2}/programs",
                   json={"program_id": "P", "schema": temp_doc(0, 50)})
        results["A"] = r.status_code

    def widen_firmware():  # B: 固件扩到 0~100（只与旧程序兼容）
        barrier.wait()
        r = c.post(f"/api/models/{m2}/firmware", json={"schema": temp_doc(0, 100)})
        results["B"] = r.status_code

    for _ in range(5):  # 多跑几轮，每轮重建场景
        results.clear()
        barrier = threading.Barrier(2)
        t1 = threading.Thread(target=narrow_program)
        t2 = threading.Thread(target=widen_firmware)
        t1.start(); t2.start(); t1.join(); t2.join()
        ok("并发恰好一方成功", sorted(results.values()) == [200, 409],
           f"A={results.get('A')} B={results.get('B')}")
        st = c.get(f"/api/models/{m2}").json()
        fw_pairs = [(f["version"], parse_schema(f["schema"])) for f in st["firmwares"]]
        pg_pairs = [(p["program_id"], parse_schema(p["schema"])) for p in st["programs"]]
        ok("并发后状态全量兼容", check_compatibility(fw_pairs, pg_pairs) == [])
        # 还原场景：若 A 成功则把程序放宽回 0~100；若 B 成功则状态已含 0~100 固件，
        # 重新发布程序 0~100 使下一轮场景一致
        r = c.post(f"/api/models/{m2}/programs",
                   json={"program_id": "P", "schema": temp_doc(0, 100)})
        assert r.status_code == 200, r.text
        # 若 B 成功过，固件已到 v2；删不了固件（只追加），改用新型号重开
        if results.get("B") == 200:
            m2 = f"c{uuid.uuid4().hex[:10]}"
            assert c.post("/api/models", json={"name": m2}).status_code == 201
            assert c.post(f"/api/models/{m2}/firmware",
                          json={"schema": temp_doc(0, 50)}).status_code == 200
            assert c.post(f"/api/models/{m2}/programs",
                          json={"program_id": "P", "schema": temp_doc(0, 100)}).status_code == 200

    # ---- HTML 页面冒烟 ----
    print("HTML 页面")
    r = c.get("/")
    ok("首页可打开", r.status_code == 200 and "设备型号" in r.text)
    r = c.get(f"/models/{model}")
    ok("型号页可打开", r.status_code == 200 and "历代固件写入格式" in r.text
       and "现用程序读取格式" in r.text and "发布日志" in r.text)
    ok("页面含示例", "温度扩范围" in r.text and "质控码缺省" in r.text)
    r = c.post(f"/models/{model}/firmware",
               data={"schema_text": json.dumps(temp_doc(-40, 100))})
    ok("网页表单发布固件被拒并展示依据", r.status_code == 200
       and "发布被拒绝" in r.text and "反例" in r.text, str(r.status_code))
    r = c.post(f"/models/{model}/programs",
               data={"program_id": "analyzer",
                      "schema_text": json.dumps(temp_doc(-40, 100, qc=["N", "Q", None]))})
    ok("网页表单发布程序成功", r.status_code == 200 and "发布成功" in r.text, str(r.status_code))

    # 型号页编解码入口（网页表单）
    r = c.get(f"/models/{m3}")
    ok("型号页含编解码入口", r.status_code == 200 and "报文编解码" in r.text
       and f"/models/{m3}/codec" in r.text)
    ok("型号页含编解码示例", "报文编解码" in r.text and "可编解码" in r.text)
    r = c.post(f"/models/{m3}/codec",
               data={"version": "1", "action": "encode",
                     "encode_text": json.dumps(msg1)})
    ok("网页编码展示位宽与二进制码", r.status_code == 200 and "编码成功" in r.text
       and "位宽" in r.text and e1.json()["code"] in r.text, str(r.status_code))
    r = c.post(f"/models/{m3}/codec",
               data={"version": "1", "action": "decode", "decode_text": e1.json()["code"]})
    ok("网页解码还原JSON对象", r.status_code == 200 and "解码成功" in r.text
       and '"temp"' in r.text and "-40" in r.text)
    r = c.post(f"/models/{m3}/codec",
               data={"version": "1", "action": "encode", "encode_text": '{"temp": 999}'})
    ok("网页编码失败说明原因", r.status_code == 200 and "编码失败" in r.text
       and "超出允许区间" in r.text)
    r = c.post(f"/models/{m3}/codec",
               data={"version": "1", "action": "decode", "decode_text": "xyz"})
    ok("网页解码失败说明原因", r.status_code == 200 and "解码失败" in r.text
       and "0 和 1" in r.text)
    r = c.post(f"/models/{m3}/codec",
               data={"version": "3", "action": "encode", "encode_text": '{"site": "WQ-01"}'})
    ok("网页不支持格式说明原因", r.status_code == 200 and "不支持编解码" in r.text)

    print(f"\n全部 {len(PASS)} 项集成自测通过")
    return model


if __name__ == "__main__":
    used_model = main()
    print(f"MODEL={used_model}")
