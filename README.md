# 水质平台 · 报文契约发布

固件升级前确认新报文能被分析程序读取、程序升级后仍能读旧归档的契约发布网页；
并为每版固件提供**报文编解码**（记录槽宽度 + 定长二进制码）。
按设备型号登记**历代固件写入格式**（只追加）与**现用程序读取格式**（按程序号替换），
各至多 8 份。每次发布前用 **z3** 对「全部历代写入格式 × 全部现用程序」做**全量**兼容判定
（逻辑证明，非抽样试样例）：任一写入格式允许的全部报文必须被每个现用程序接受，
否则拒绝发布，并给出**合法反例报文**与**拒收程序**。

## 快速启动（Docker Compose）

```bash
docker compose up --build
```

- 操作入口（网页）：http://localhost:8000
- 健康检查：http://localhost:8000/api/health
- 数据保存在命名卷 `pgdata`（PostgreSQL），服务重启后已发布格式保留。

## 网页操作

1. 首页创建设备型号（如 `WQ-5000`），进入型号管理页。
2. 在「发布新格式」粘贴受限 JSON Schema：
   - 左栏**追加固件写入格式**（版本号自动递增，只追加）；
   - 右栏**发布/替换程序读取格式**（同一程序号覆盖旧版）。
3. 发布结果即时展示：成功，或拒绝 + 每个不兼容组合的反例报文与拒收程序；
   页面下方「发布日志」保留最近 50 条成功/拒绝/无效记录（含拒绝依据）。
4. 页面内置示例，点按钮即可填入编辑器：
   - **温度扩范围**：固件 v2 扩到 -40~100℃，直接发布会被旧程序（-10~50℃）拒收，
     反例如 `{"temp": -40, ...}`；先升级程序再发固件则通过。
   - **质控码缺省**：固件缺省 `qc`（或写 `null`），把 `qc` 列为 `required` 的程序会被拒，
     反例为不含 `qc` 的报文；程序改为可选并允许 `null` 后通过。
   - **顶层 anyOf**：程序同时接受多种报文形态。
   - **报文编解码**：可编解码的固件格式，发布后到「报文编解码」试用。

## 报文编解码

在型号页的「报文编解码」中选择一版**已发布固件**：

- 输入 JSON 报文 → 展示位宽与二进制码；输入二进制码 → 还原 JSON 对象；
- 版本卡片显示该版是否可编解码及原因；编解码面板显示位宽、合法报文总数、字段与允许值。

支持范围（其它格式会说明具体原因，不影响兼容性发布）：

- 平面对象，或顶层 `anyOf` 至多 **3** 个分支；各分支合计至多 **6** 个不同字段；
- 字段值只允许**双侧有界的整数区间**（含整数枚举恰好构成连续区间的情况）和/或 `null`
  ——字符串枚举、单侧有界/无界整数、不连续整数枚举均不支持编解码。

编码规则：

- 位宽 = `max(1, ceil(log2(合法报文总数)))`，即能容纳该版全部合法报文的最小正位宽；
  未使用的码值解码时明确判为无效码。
- 「合法报文总数」和「第几个报文」用**组合计数 + 容斥**计算，不逐条枚举报文，
  所以大区间（如 `0..10^12`）也即时完成。
- 各分支先按「字段名/必填性/值域」规范化排序（与 `anyOf` 数组顺序无关），
  报文集按「首个接受该报文的分支」划分为互不相交的段；同一报文命中多个分支也只占一个码。
- 段内按字段字典序做定长混合进制排名：每字段状态依次为「缺省 < `null` < 区间内整数升序」，
  因此**字段缺省与显式 `null` 是不同的码**。
- 码只由该版固件格式决定：与 JSON 键顺序、后续版本发布、服务重启均无关。

编解码与兼容性检查相互独立：某版固件即使不支持编解码，仍可正常参与 z3 兼容判定。

## 受限 JSON Schema 语法（其余一律拒绝）

- 平面对象：`{"type":"object","properties":{...},"required":[...],"additionalProperties":false}`
  （`required` 可省略，默认空；`additionalProperties` 必须显式为 `false`）
- 顶层 `anyOf`：`{"anyOf":[<平面对象>, ...]}`，且只能单独出现在顶层
- 整数上下界：`{"type":"integer","minimum":m,"maximum":M}`（上下界均可省略）
- 字符串/整数/空值枚举：`{"enum":[...]}`，可配 `"type"` 限定；枚举值只允许字符串、整数、`null`
- 空值类型：`{"type":"null"}`

嵌套对象、数组、`pattern`、`oneOf`/`allOf`/`not`、浮点/布尔值、`string` 无枚举等语法均被拒绝。

## 校验原理

对每对（写入格式 W，读取格式 R），把报文编码为 z3 变量（每个字段：是否出现、
值类型 null/int/string、整数值、字符串枚举下标），求解 `W 合法 ∧ R 拒收`：

- **UNSAT** ⇒ 不存在反例，`L(W) ⊆ L(R)` 成立——这是对全部报文的证明，不是抽样；
- **SAT** ⇒ 从模型还原一个反例报文，并用独立的纯 Python 判定器复核
  （确实满足 W、确实被 R 拒收）后随拒绝结果返回。

## 并发与事务

- 每次发布在事务内先对型号行加锁（PostgreSQL `SELECT ... FOR UPDATE`），
  再基于最新已提交状态做**全量交叉检查**后落库——并发升级被串行化，
  不会各自通过却留下不兼容组合；
- 检查失败整体回滚，**已发布格式不变**；拒绝/无效尝试另写发布日志留痕；
- 固件格式只追加（无修改/删除入口），程序格式按程序号替换；各至多 8 份。

## JSON API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/models` | 创建型号 `{"name": "WQ-5000"}` |
| GET | `/api/models/{name}` | 查看当前固件/程序格式 |
| GET | `/api/models/{name}/log` | 发布日志（含拒绝依据） |
| POST | `/api/models/{name}/firmware` | 追加固件格式 `{"schema": {...}}` |
| POST | `/api/models/{name}/programs` | 发布/替换程序格式 `{"program_id": "...", "schema": {...}}` |
| GET | `/api/models/{name}/codec/{version}` | 该版固件编解码信息（位宽、合法报文总数、字段）；不支持时 422 + 原因 |
| POST | `/api/models/{name}/codec/{version}/encode` | 编码 `{"message": {...}}` → `{"code": "0101", "width": n, ...}` |
| POST | `/api/models/{name}/codec/{version}/decode` | 解码 `{"code": "0101"}` → `{"message": {...}, ...}` |

响应：成功 `200 {"ok": true, ...}`；不兼容 `409 {"ok": false, "problems": [{firmware_version, program_id, counterexample}]}`；语法/上限错误 `422`。
编解码失败（格式不支持 / 非法报文 / 无效码）返回 `422 {"ok": false, "error": "原因"}`。

## 本地开发与自测

```bash
pip install -r app/requirements.txt httpx
python3 tests/test_units.py                 # 语法子集 + z3 判定 + 编解码单元自测
cd app && DATABASE_URL=sqlite:////tmp/contracts.db uvicorn main:app --port 8000 &
python3 tests/selftest.py                   # 集成自测（示例/编解码/上限/并发/页面）
```

`DATABASE_URL` 缺省为 SQLite（便于本地自测）；Compose 中注入 PostgreSQL 连接串。

## 目录结构

```
app/
  main.py         FastAPI 入口（HTML 页面 + JSON API + 编解码入口）
  service.py      发布事务：加锁 → 全量检查 → 落库/回滚
  solver.py       z3 编码与全量兼容判定（含反例复核）
  codec.py        报文编解码：组合计数 + 容斥定长码（最小正位宽）
  schema_spec.py  受限 JSON Schema 子集解析与纯 Python 报文判定
  db.py           SQLAlchemy 模型（PostgreSQL JSONB / SQLite JSON）
  examples.py     温度扩范围、质控码缺省、anyOf、编解码示例
  templates/      页面模板
tests/
  test_units.py   单元自测（含编解码穷举核对）
  selftest.py     集成自测（含编解码与页面入口）
docker-compose.yml
```
