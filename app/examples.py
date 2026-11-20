"""内置示例：温度扩范围、质控码缺省、anyOf、编解码。供网页一键填入编辑器。"""

TEMP_FW_V1 = {
    "type": "object",
    "properties": {
        "temp": {"type": "integer", "minimum": -10, "maximum": 50},
        "site": {"type": "string", "enum": ["WQ-01", "WQ-02"]},
    },
    "required": ["temp", "site"],
    "additionalProperties": False,
}

TEMP_FW_V2 = {
    "type": "object",
    "properties": {
        "temp": {"type": "integer", "minimum": -40, "maximum": 100},
        "site": {"type": "string", "enum": ["WQ-01", "WQ-02"]},
    },
    "required": ["temp", "site"],
    "additionalProperties": False,
}

TEMP_PROG_OLD = {
    "type": "object",
    "properties": {
        "temp": {"type": "integer", "minimum": -10, "maximum": 50},
        "site": {"type": "string", "enum": ["WQ-01", "WQ-02"]},
    },
    "required": ["temp", "site"],
    "additionalProperties": False,
}

TEMP_PROG_NEW = {
    "type": "object",
    "properties": {
        "temp": {"type": "integer", "minimum": -40, "maximum": 100},
        "site": {"type": "string", "enum": ["WQ-01", "WQ-02"]},
    },
    "required": ["temp", "site"],
    "additionalProperties": False,
}

QC_FW = {
    "type": "object",
    "properties": {
        "temp": {"type": "integer", "minimum": -40, "maximum": 100},
        "site": {"type": "string", "enum": ["WQ-01", "WQ-02"]},
        "qc": {"enum": ["N", "Q", None]},
    },
    "required": ["temp", "site"],
    "additionalProperties": False,
}

QC_PROG_REQUIRE = {
    "type": "object",
    "properties": {
        "temp": {"type": "integer", "minimum": -40, "maximum": 100},
        "site": {"type": "string", "enum": ["WQ-01", "WQ-02"]},
        "qc": {"enum": ["N", "Q"]},
    },
    "required": ["temp", "site", "qc"],
    "additionalProperties": False,
}

QC_PROG_OPTIONAL = {
    "type": "object",
    "properties": {
        "temp": {"type": "integer", "minimum": -40, "maximum": 100},
        "site": {"type": "string", "enum": ["WQ-01", "WQ-02"]},
        "qc": {"enum": ["N", "Q", None]},
    },
    "required": ["temp", "site"],
    "additionalProperties": False,
}

ANYOF_PROG = {
    "anyOf": [
        {
            "type": "object",
            "properties": {
                "temp": {"type": "integer", "minimum": -40, "maximum": 100},
                "site": {"type": "string", "enum": ["WQ-01", "WQ-02"]},
                "qc": {"enum": ["N", "Q", None]},
            },
            "required": ["temp", "site"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "enum": [0, 1, 2, 3]},
                "site": {"type": "string", "enum": ["WQ-01", "WQ-02"]},
            },
            "required": ["level", "site"],
            "additionalProperties": False,
        },
    ]
}

CODEC_FW = {
    "type": "object",
    "properties": {
        "temp": {"type": "integer", "minimum": -40, "maximum": 100},
        "site": {"type": "integer", "minimum": 1, "maximum": 2},
        "qc": {"enum": [None, 0, 1, 2]},
    },
    "required": ["temp"],
    "additionalProperties": False,
}

CODEC_FW_ANYOF = {
    "anyOf": [
        {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "minimum": 0, "maximum": 3},
                "site": {"type": "integer", "minimum": 1, "maximum": 2},
            },
            "required": ["level"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "minimum": 0, "maximum": 1},
            },
            "required": ["level"],
            "additionalProperties": False,
        },
    ]
}


EXAMPLES = [
    {
        "title": "示例一：温度扩范围",
        "story": ("固件 v2 把温度量程从 -10~50℃ 扩到 -40~100℃。若直接发布固件 v2，"
                  "旧程序（只认 -10~50℃）会拒收，系统给出反例报文（如 temp=-40）；"
                  "应先把程序升级为 -40~100℃，再发布固件 v2。"),
        "steps": ["发布固件 v1（-10~50℃）", "发布程序 analyzer 旧版（-10~50℃）",
                  "直接发布固件 v2（-40~100℃）→ 被拒并给出反例",
                  "把 analyzer 替换为新版（-40~100℃）→ 通过",
                  "再次发布固件 v2 → 通过"],
        "snippets": [
            {"label": "固件 v1：temp -10~50℃", "target": "firmware", "doc": TEMP_FW_V1},
            {"label": "固件 v2：temp -40~100℃", "target": "firmware", "doc": TEMP_FW_V2},
            {"label": "程序 analyzer 旧版：-10~50℃", "target": "program",
             "program_id": "analyzer", "doc": TEMP_PROG_OLD},
            {"label": "程序 analyzer 新版：-40~100℃", "target": "program",
             "program_id": "analyzer", "doc": TEMP_PROG_NEW},
        ],
    },
    {
        "title": "示例二：质控码缺省",
        "story": ("新固件在质控码不可得时缺省 qc 字段（或写 null）。若程序把 qc 列为 "
                  "required 且不允许 null，缺省报文会被拒收，反例为不含 qc（或 qc=null）的报文；"
                  "程序需把 qc 改为可选并允许 null。"),
        "steps": ["发布固件：qc 可选、可取 null", "发布程序 strict-qc：qc 必填 → 被拒并给出反例",
                  "把 strict-qc 替换为 qc 可选版本 → 通过"],
        "snippets": [
            {"label": "固件：qc 可缺省/为 null", "target": "firmware", "doc": QC_FW},
            {"label": "程序 strict-qc：qc 必填（会被拒）", "target": "program",
             "program_id": "strict-qc", "doc": QC_PROG_REQUIRE},
            {"label": "程序 strict-qc：qc 可选（兼容）", "target": "program",
             "program_id": "strict-qc", "doc": QC_PROG_OPTIONAL},
        ],
    },
    {
        "title": "示例三：顶层 anyOf（多报文类型）",
        "story": ("程序可声明顶层 anyOf 以同时接受多种报文形态，例如「温度报文」或「水位档位报文」。"
                  "下面是一份接受两种形态的读取格式，可发布为第二个程序试一试。"),
        "steps": ["把 anyOf 程序发布为 multiplexer → 与已登记固件做全量交叉检查"],
        "snippets": [
            {"label": "程序 multiplexer：anyOf 两种报文", "target": "program",
             "program_id": "multiplexer", "doc": ANYOF_PROG},
        ],
    },
    {
        "title": "示例四：报文编解码",
        "story": ("为某一版固件确定定长二进制码。先发布下面的固件（建议另起新型号，避免与已有程序冲突），"
                  "再在「报文编解码」中选择该版本：输入 {\"temp\": 21, \"site\": 1} 可看到位宽与码；"
                  "把码贴回右侧可还原 JSON。qc 缺省与 qc=null 是不同的码；"
                  "第二个格式演示 anyOf 分支重叠时同一报文只占一个码。"),
        "steps": ["发布固件：temp -40~100、site 1~2、qc 可为 null/0/1/2",
                  "在报文编解码中选择该版本并编码/解码",
                  "发布固件：anyOf 两个 level 分支（重叠），观察字段数与位宽"],
        "snippets": [
            {"label": "固件：可编解码（整数区间 + null）", "target": "firmware",
             "doc": CODEC_FW},
            {"label": "固件：anyOf 重叠分支（可编解码）", "target": "firmware",
             "doc": CODEC_FW_ANYOF},
        ],
    },
]
