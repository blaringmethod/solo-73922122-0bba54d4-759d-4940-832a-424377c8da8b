"""契约发布网页：FastAPI 入口（HTML 页面 + JSON API）。"""
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

import service
from codec import CodecError, build_codec, codec_summary
from db import (Base, DeviceModel, FirmwareFormat, ProgramFormat, PublishLog,
                SessionLocal, engine)
from examples import EXAMPLES
from schema_spec import SchemaError, parse_schema

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["tojson"] = lambda v, indent=2: json.dumps(
    v, ensure_ascii=False, indent=indent)
templates.env.filters["tojsonc"] = lambda v: json.dumps(
    v, ensure_ascii=False, separators=(",", ":"))


def _html_json(v):
    """嵌入 <script> 的 JSON：转义 <, >, &，避免 </script> 提前闭合与 HTML 实体化。"""
    text = (json.dumps(v, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))
    return Markup(text)  # 已手工转义，不要再被 Jinja 自动转义


templates.env.filters["script_json"] = _html_json


def _wait_for_db(retries=40, delay=1.0):
    for i in range(retries):
        try:
            with engine.connect():
                return
        except OperationalError:
            if i == retries - 1:
                raise
            time.sleep(delay)


@asynccontextmanager
async def lifespan(app):
    _wait_for_db()
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="水质平台报文契约发布", lifespan=lifespan)


# ---------------- 公共数据 ----------------

def _get_model_or_404(name: str) -> DeviceModel:
    with SessionLocal() as s:
        m = s.execute(select(DeviceModel).where(DeviceModel.name == name)).scalar_one_or_none()
        if m is None:
            raise HTTPException(404, f"型号 {name!r} 不存在")
        return DeviceModel(id=m.id, name=m.name)


def _model_context(name: str, result=None, codec_result=None):
    with SessionLocal() as s:
        model = s.execute(select(DeviceModel).where(DeviceModel.name == name)).scalar_one_or_none()
        if model is None:
            raise HTTPException(404, f"型号 {name!r} 不存在")
        firmwares = s.execute(select(FirmwareFormat)
                              .where(FirmwareFormat.model_id == model.id)
                              .order_by(FirmwareFormat.version)).scalars().all()
        programs = s.execute(select(ProgramFormat)
                             .where(ProgramFormat.model_id == model.id)
                             .order_by(ProgramFormat.program_id)).scalars().all()
        logs = s.execute(select(PublishLog).where(PublishLog.model_id == model.id)
                         .order_by(PublishLog.id.desc()).limit(50)).scalars().all()
        s.expunge_all()
        return {
            "model": model, "firmwares": firmwares, "programs": programs,
            "logs": logs, "examples": EXAMPLES, "result": result,
            "codec_result": codec_result,
            "codec_infos": {f.version: codec_summary(f.doc) for f in firmwares},
            "max_formats": service.MAX_FORMATS,
        }


# ---------------- 编解码（按型号 + 已发布固件版本） ----------------

def _firmware_doc(name: str, version: str):
    """取某型号某版固件的 schema 文档；不存在/非法时抛 HTTPException。"""
    ver_text = str(version or "").strip()
    if not ver_text.isdigit():
        raise HTTPException(422, f"固件版本必须是整数，收到 {version!r}")
    with SessionLocal() as s:
        model = s.execute(select(DeviceModel).where(DeviceModel.name == name)).scalar_one_or_none()
        if model is None:
            raise HTTPException(404, f"型号 {name!r} 不存在")
        fw = s.execute(select(FirmwareFormat)
                       .where(FirmwareFormat.model_id == model.id,
                              FirmwareFormat.version == int(ver_text))).scalar_one_or_none()
        if fw is None:
            raise HTTPException(404, f"型号 {name!r} 没有固件版本 v{ver_text}")
        return fw.doc, fw.version


def codec_encode(name: str, version: str, message) -> dict:
    """网页/API 共用的编码入口；返回 {ok, ...} 或 {ok: false, error, ...}。"""
    doc, ver = _firmware_doc(name, version)
    try:
        codec = build_codec(parse_schema(doc))
    except (SchemaError, CodecError) as e:
        return {"ok": False, "kind": "encode", "version": ver,
                "error": f"该版固件格式不支持编解码：{e}"}
    try:
        result = codec.encode(message)
    except CodecError as e:
        return {"ok": False, "kind": "encode", "version": ver, "error": str(e)}
    result.update({"ok": True, "kind": "encode", "version": ver})
    return result


def codec_decode(name: str, version: str, code: str) -> dict:
    """网页/API 共用的解码入口；返回 {ok, ...} 或 {ok: false, error, ...}。"""
    doc, ver = _firmware_doc(name, version)
    try:
        codec = build_codec(parse_schema(doc))
    except (SchemaError, CodecError) as e:
        return {"ok": False, "kind": "decode", "version": ver,
                "error": f"该版固件格式不支持编解码：{e}"}
    try:
        result = codec.decode(code)
    except CodecError as e:
        return {"ok": False, "kind": "decode", "version": ver, "error": str(e)}
    result.update({"ok": True, "kind": "decode", "version": ver})
    return result


# ---------------- HTML 页面 ----------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    with SessionLocal() as s:
        models = s.execute(select(DeviceModel).order_by(DeviceModel.name)).scalars().all()
        rows = []
        for m in models:
            fw = s.execute(select(FirmwareFormat).where(FirmwareFormat.model_id == m.id)).scalars().all()
            pg = s.execute(select(ProgramFormat).where(ProgramFormat.model_id == m.id)).scalars().all()
            rows.append({"name": m.name, "fw_count": len(fw), "pg_count": len(pg)})
    return templates.TemplateResponse(request, "index.html", {"rows": rows})


@app.post("/models")
def create_model(request: Request, name: str = Form(...)):
    name = (name or "").strip()
    if not service.valid_name(name):
        with SessionLocal() as s:
            models = s.execute(select(DeviceModel).order_by(DeviceModel.name)).scalars().all()
            rows = [{"name": m.name, "fw_count": 0, "pg_count": 0} for m in models]
        return templates.TemplateResponse(
            request, "index.html", {"rows": rows,
                                    "error": "型号名只能由字母数字及 _ . - 组成，长度 1~64"},
            status_code=400)
    with SessionLocal() as s:
        if s.execute(select(DeviceModel).where(DeviceModel.name == name)).scalar_one_or_none():
            return RedirectResponse(f"/models/{name}", status_code=303)
        s.add(DeviceModel(name=name))
        s.commit()
    return RedirectResponse(f"/models/{name}", status_code=303)


@app.get("/models/{name}", response_class=HTMLResponse)
def model_page(request: Request, name: str):
    return templates.TemplateResponse(request, "model.html", _model_context(name))


@app.post("/models/{name}/firmware", response_class=HTMLResponse)
def fw_publish_html(request: Request, name: str, schema_text: str = Form(...)):
    model = _get_model_or_404(name)
    try:
        doc = json.loads(schema_text)
    except json.JSONDecodeError as e:
        outcome = service.PublishOutcome(False, f"JSON 解析失败：{e}")
    else:
        outcome = service.publish_firmware(model.id, doc)
    return templates.TemplateResponse(request, "model.html",
                                      _model_context(name, result=outcome))


@app.post("/models/{name}/programs", response_class=HTMLResponse)
def prog_publish_html(request: Request, name: str,
                      program_id: str = Form(...), schema_text: str = Form(...)):
    model = _get_model_or_404(name)
    try:
        doc = json.loads(schema_text)
    except json.JSONDecodeError as e:
        outcome = service.PublishOutcome(False, f"JSON 解析失败：{e}")
    else:
        outcome = service.publish_program(model.id, program_id.strip(), doc)
    return templates.TemplateResponse(request, "model.html",
                                      _model_context(name, result=outcome))


@app.post("/models/{name}/codec", response_class=HTMLResponse)
def codec_html(request: Request, name: str,
               version: str = Form(...), action: str = Form(...),
               encode_text: str = Form(""), decode_text: str = Form("")):
    # 两栏各有独立字段名：点「编码」用左栏，点「解码」用右栏，无需 JavaScript
    text = (encode_text if action == "encode" else decode_text).strip()
    if action == "encode":
        result = {"ok": False, "kind": "encode", "version": version, "input": text}
        if not text:
            result["error"] = "请输入要编码的 JSON 报文"
        else:
            try:
                message = json.loads(text)
            except json.JSONDecodeError as e:
                result["error"] = f"JSON 解析失败：{e}"
            else:
                outcome = codec_encode(name, version, message)
                if outcome.get("ok"):
                    outcome["input"] = json.dumps(message, ensure_ascii=False)
                else:
                    outcome["input"] = text
                result = outcome
    elif action == "decode":
        result = {"ok": False, "kind": "decode", "version": version, "input": text}
        if not text:
            result["error"] = "请输入要解码的二进制码"
        else:
            outcome = codec_decode(name, version, text)
            outcome.setdefault("input", text)
            if outcome.get("ok"):
                outcome["message_text"] = json.dumps(outcome["message"],
                                                     ensure_ascii=False)
            result = outcome
    else:
        result = {"ok": False, "kind": action, "version": version,
                  "error": f"未知操作 {action!r}"}
    ctx = _model_context(name, codec_result=result)
    return templates.TemplateResponse(request, "model.html", ctx)

# ---------------- JSON API ----------------

class ModelIn(BaseModel):
    name: str


class FirmwareIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    doc: dict = Field(alias="schema")


class ProgramIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    program_id: str
    doc: dict = Field(alias="schema")


def _outcome_response(outcome: service.PublishOutcome, extra=None):
    body = {"ok": outcome.ok, "message": outcome.message}
    if outcome.problems:
        body["problems"] = outcome.problems
    if extra:
        body.update(extra)
    status = 200 if outcome.ok else (409 if outcome.problems else 422)
    return JSONResponse(body, status_code=status)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/models", status_code=201)
def api_create_model(body: ModelIn):
    name = body.name.strip()
    if not service.valid_name(name):
        raise HTTPException(422, "型号名只能由字母数字及 _ . - 组成，长度 1~64")
    with SessionLocal() as s:
        if s.execute(select(DeviceModel).where(DeviceModel.name == name)).scalar_one_or_none():
            raise HTTPException(409, f"型号 {name!r} 已存在")
        s.add(DeviceModel(name=name))
        try:
            s.commit()
        except IntegrityError:
            s.rollback()
            raise HTTPException(409, f"型号 {name!r} 已存在")
    return {"ok": True, "name": name}


@app.get("/api/models/{name}")
def api_model_state(name: str):
    ctx = _model_context(name)
    return {
        "name": ctx["model"].name,
        "firmwares": [{"version": f.version, "schema": f.doc,
                       "created_at": str(f.created_at)} for f in ctx["firmwares"]],
        "programs": [{"program_id": p.program_id, "schema": p.doc,
                      "updated_at": str(p.updated_at)} for p in ctx["programs"]],
    }


@app.get("/api/models/{name}/log")
def api_model_log(name: str):
    ctx = _model_context(name)
    return {"logs": [{"action": l.action, "program_id": l.program_id,
                      "result": l.result, "detail": l.detail,
                      "created_at": str(l.created_at)} for l in ctx["logs"]]}


@app.post("/api/models/{name}/firmware")
def api_publish_firmware(name: str, body: FirmwareIn):
    model = _get_model_or_404(name)
    outcome = service.publish_firmware(model.id, body.doc)
    return _outcome_response(outcome, {"version": outcome.version})


@app.post("/api/models/{name}/programs")
def api_publish_program(name: str, body: ProgramIn):
    model = _get_model_or_404(name)
    outcome = service.publish_program(model.id, body.program_id.strip(), body.doc)
    return _outcome_response(outcome)


# ---------------- 编解码 JSON API ----------------

class EncodeIn(BaseModel):
    message: object


class DecodeIn(BaseModel):
    code: str


def _codec_response(result: dict):
    if result.get("ok"):
        return JSONResponse(result, status_code=200)
    # 格式不受支持 / 报文非法 / 码无效：说明原因
    return JSONResponse(result, status_code=422)


@app.post("/api/models/{name}/codec/{version}/encode")
def api_codec_encode(name: str, version: str, body: EncodeIn):
    return _codec_response(codec_encode(name, version, body.message))


@app.post("/api/models/{name}/codec/{version}/decode")
def api_codec_decode(name: str, version: str, body: DecodeIn):
    return _codec_response(codec_decode(name, version, body.code))


@app.get("/api/models/{name}/codec/{version}")
def api_codec_info(name: str, version: str):
    doc, ver = _firmware_doc(name, version)
    info = codec_summary(doc)
    info["version"] = ver
    return JSONResponse(info, status_code=200 if info.get("supported") else 422)
