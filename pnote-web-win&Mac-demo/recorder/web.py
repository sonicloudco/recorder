"""Web 界面后端（FastAPI）。

在浏览器中提供与命令行 REPL 等价的全部功能：
    python main.py --web [--host 127.0.0.1] [--port 8000]

可选功能，需先安装：pip install -r requirements-web.txt
架构：单例 Recorder（一条 BLE 连接）+ REST 命令接口 + WebSocket 推送
（下载进度 / 实时码流字节数 / 机身按键事件 / 日志）。

注意：本模块不可使用 `from __future__ import annotations` —— 字符串化注解
会让 FastAPI 无法解析 create_app 闭包内导入的 Request 类型（表现为 422）。
"""

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import List, Optional

from . import asr
from . import protocol as P
from .cli import (GAIN_NAMES, RESULT_NAMES, RT_STATE_NAMES, STATE_NAMES,
                  fmt_duration, fmt_file_time, fmt_size)
from .device import Recorder, RecorderError
from .protocol import FileEntry

logger = logging.getLogger(__name__)

# 前端静态文件目录（web/index.html 等）
WEB_DIR = Path(__file__).resolve().parents[1] / "web"

_INSTALL_HINT = ("未安装 fastapi/uvicorn，Web 界面不可用；请先执行 "
                 "pip install -r requirements-web.txt")


def create_app(output_dir: Path):
    try:
        from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
        from fastapi.responses import JSONResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:
        raise RuntimeError(_INSTALL_HINT) from exc

    app = FastAPI(title="声云 录音卡 Web 控制台")
    recorder = Recorder(output_dir=Path(output_dir))
    S = {"devices": [], "files": []}   # 扫描结果 / 文件列表缓存
    sockets: set = set()
    op_lock = asyncio.Lock()           # 下载/转写/巡检等耗时操作互斥
    rt_state = {"bytes": 0, "ts": 0.0}
    prog_state = {"ts": 0.0}

    # ------------------------------------------------ WebSocket 推送

    async def _send(ws, payload: dict) -> None:
        try:
            await ws.send_json(payload)
        except Exception:
            sockets.discard(ws)

    def broadcast(payload: dict) -> None:
        """同步回调里调度异步群发（回调总在事件循环内触发）。"""
        if not sockets:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        for ws in list(sockets):
            loop.create_task(_send(ws, payload))

    def log_push(level: str, text: str) -> None:
        broadcast({"type": "log", "level": level, "text": text})

    # ------------------------------------------------ Recorder 回调

    def on_progress(received: int, expected: int) -> None:
        now = time.monotonic()
        # 节流：进度最快 0.15s 推一次，避免刷爆 WebSocket
        if received and expected and received < expected \
                and now - prog_state["ts"] < 0.15:
            return
        prog_state["ts"] = now
        broadcast({"type": "progress", "received": received,
                   "expected": expected})

    def on_realtime(event: str, payload) -> None:
        if event == "audio":
            rt_state["bytes"] += len(payload)
            now = time.monotonic()
            if now - rt_state["ts"] < 0.2:
                return
            rt_state["ts"] = now
            broadcast({"type": "realtime", "event": "bytes",
                       "value": rt_state["bytes"],
                       "text": fmt_size(rt_state["bytes"])})
        elif event == "filename":
            broadcast({"type": "realtime", "event": "filename",
                       "value": str(payload)})
        elif event == "state":
            broadcast({"type": "realtime", "event": "state",
                       "value": RT_STATE_NAMES.get(payload, str(payload))})

    def on_device_event(frame) -> None:
        names = {P.KEY_REC_START: "开始录音", P.KEY_REC_SAVE: "保存录音",
                 P.KEY_REC_PAUSE: "暂停录音", P.KEY_REC_RESUME: "继续录音",
                 P.KEY_STATE_RESP: "录音状态"}
        desc = names.get(frame.cmd, f"cmd={frame.cmd}") \
            if frame.type == P.TYPE_KEY else f"type={frame.type} cmd={frame.cmd}"
        broadcast({"type": "device_event", "desc": desc,
                   "body": frame.body.hex(" ") or "-"})

    recorder.on_progress = on_progress
    recorder.on_realtime = on_realtime
    recorder.on_device_event = on_device_event

    # ------------------------------------------------ 工具函数

    async def read_json(req) -> dict:
        try:
            body = await req.json()
            return body if isinstance(body, dict) else {}
        except Exception:
            return {}

    def require_connected() -> None:
        if not recorder.is_connected:
            raise RecorderError("尚未连接设备，请先扫描并连接")

    def busy_guard() -> None:
        if op_lock.locked():
            raise RecorderError("有耗时操作正在进行，请稍候")

    def entry_by_index(idx) -> FileEntry:
        if not isinstance(idx, int) or not (0 <= idx < len(S["files"])):
            raise RecorderError("无效文件序号，请先刷新文件列表")
        return S["files"][idx]

    def file_json(i: int, f: FileEntry) -> dict:
        return {"index": i, "name": f.name, "size": f.size,
                "size_text": fmt_size(f.size),
                "time_text": fmt_file_time(f.duration)}

    def local_path_for(entry: FileEntry) -> Path:
        """与下载落盘一致的本地预期路径（用于转写复用已下载文件）。"""
        safe = re.sub(r'[\\/:*?"<>|]', "_",
                      entry.candidate_names()[0]).rstrip(". ")
        return recorder.output_dir / safe

    def download_json(result) -> dict:
        wav = None
        if result.is_wav and result.wav_info is not None:
            w = result.wav_info
            wav = {"ok": w.ok, "declared": w.declared,
                   "sample_rate": w.sample_rate, "bits": w.bits_per_sample,
                   "channels": w.channels}
        return {"filename": result.filename, "size": len(result.data),
                "size_text": fmt_size(len(result.data)),
                "local_name": result.path.name if result.path else None,
                "url": f"/downloads/{result.path.name}" if result.path else None,
                "is_wav": result.is_wav, "wav": wav}

    # ------------------------------------------------ 异常统一处理

    @app.exception_handler(RecorderError)
    async def _recorder_error(_req, exc):
        return JSONResponse(status_code=400, content={"error": str(exc)})

    @app.exception_handler(asyncio.TimeoutError)
    async def _timeout_error(_req, _exc):
        return JSONResponse(status_code=504,
                            content={"error": "等待设备应答超时"})

    # ------------------------------------------------ 连接管理

    @app.get("/api/status")
    async def api_status():
        connected = recorder.is_connected
        t = recorder.transport
        return {"connected": connected,
                "mtu": t.mtu if connected else None,
                "payload": t.payload_size if connected else None,
                "asr_ready": asr.is_loaded(),
                "files": [file_json(i, f)
                          for i, f in enumerate(S["files"])]}

    @app.post("/api/scan")
    async def api_scan(req: Request):
        body = await read_json(req)
        timeout = float(body.get("timeout") or 6.0)
        compat = bool(body.get("compat"))
        S["devices"] = await recorder.scan(timeout, compat=compat)
        return [{"index": i, "name": d.name or "(无名称)",
                 "address": d.address}
                for i, d in enumerate(S["devices"])]

    @app.post("/api/connect")
    async def api_connect(req: Request):
        body = await read_json(req)
        target = body.get("target")
        if isinstance(target, int):
            if not (0 <= target < len(S["devices"])):
                raise RecorderError("无效设备序号，请重新扫描")
            device = S["devices"][target]
        elif isinstance(target, str) and target.strip():
            device = target.strip()   # bleak 支持直接传地址
        else:
            raise RecorderError("缺少 target（扫描序号或 MAC 地址）")
        await recorder.connect(device)
        synced = True
        try:
            await recorder.sync_time()   # 连接后自动同步时间（0-0）
        except Exception:
            synced = False
        log_push("OK", f"设备已连接，MTU={recorder.transport.mtu}")
        return {"connected": True, "mtu": recorder.transport.mtu,
                "payload": recorder.transport.payload_size,
                "time_synced": synced}

    @app.post("/api/disconnect")
    async def api_disconnect():
        await recorder.disconnect()
        log_push("INFO", "设备已断开")
        return {"connected": False}

    # ------------------------------------------------ 设备信息

    @app.get("/api/info")
    async def api_info():
        require_connected()
        battery = await recorder.get_battery()
        remain, total = await recorder.get_capacity()
        version = await recorder.get_version()
        auth = await recorder.get_auth_code()
        return {"battery": "充电中" if battery == P.BATTERY_CHARGING
                else f"{battery}%",
                "capacity": f"剩余 {fmt_size(remain * 1024)} / "
                            f"共 {fmt_size(total * 1024)}",
                "version": version,
                "auth": auth.hex(" ") or "-"}

    @app.post("/api/synctime")
    async def api_synctime():
        require_connected()
        await recorder.sync_time()
        return {"message": "已按本机时间同步"}

    @app.post("/api/smoke")
    async def api_smoke():
        require_connected()
        busy_guard()
        results = []
        async with op_lock:
            async def get_files():
                S["files"] = await recorder.get_file_list()
                return S["files"]
            steps = [
                ("电量", recorder.get_battery,
                 lambda v: "充电中" if v == P.BATTERY_CHARGING else f"{v}%"),
                ("容量", recorder.get_capacity,
                 lambda v: f"剩余 {fmt_size(v[0] * 1024)} / "
                           f"共 {fmt_size(v[1] * 1024)}"),
                ("固件", recorder.get_version, str),
                ("授权码", recorder.get_auth_code, lambda v: v.hex(" ")),
                ("录音状态", recorder.record_state,
                 lambda v: STATE_NAMES.get(v, str(v))),
                ("录音时间", recorder.record_time,
                 lambda v: f"{fmt_duration(v[0])} / {fmt_size(v[1])}"),
                ("当前文件名", recorder.record_filename, str),
                ("增益", recorder.get_gain,
                 lambda v: GAIN_NAMES.get(v, str(v))),
                ("文件列表", get_files, lambda v: f"{len(v)} 个文件"),
            ]
            for label, coro_fn, render in steps:
                try:
                    value = await coro_fn()
                    results.append({"label": label, "value": render(value),
                                    "ok": True})
                except asyncio.TimeoutError:
                    results.append({"label": label, "value": "无应答（超时）",
                                    "ok": False})
                except Exception as exc:
                    results.append({"label": label, "value": f"失败 {exc}",
                                    "ok": False})
                await asyncio.sleep(0.26)   # 命令间隔，避免固件应接不暇
        return results

    # ------------------------------------------------ 文件操作

    @app.get("/api/files")
    async def api_files():
        require_connected()
        busy_guard()
        async with op_lock:
            S["files"] = await recorder.get_file_list()
        return [file_json(i, f) for i, f in enumerate(S["files"])]

    @app.post("/api/download")
    async def api_download(req: Request):
        require_connected()
        busy_guard()
        body = await read_json(req)
        entry = entry_by_index(body.get("index"))
        offset = int(body.get("offset") or 0)
        filename = body.get("filename") or None
        async with op_lock:
            result = await recorder.download(entry, offset=offset,
                                             filename=filename)
        on_progress(len(result.data), len(result.data))   # 收尾推 100%
        return download_json(result)

    @app.post("/api/segment")
    async def api_segment(req: Request):
        require_connected()
        busy_guard()
        body = await read_json(req)
        entry = entry_by_index(body.get("index"))
        start, end = int(body.get("start", 0)), int(body.get("end", 0))
        if not (0 <= start < end):
            raise RecorderError("字节范围无效（需 0 <= start < end）")
        async with op_lock:
            result = await recorder.download_segment(entry, start, end)
        return download_json(result)

    @app.post("/api/abort")
    async def api_abort():
        require_connected()
        await recorder.abort_download()
        return {"message": "已发送 2-7 终止导入"}

    @app.post("/api/delete")
    async def api_delete(req: Request):
        require_connected()
        entry = entry_by_index((await read_json(req)).get("index"))
        code = await recorder.delete_file(entry)
        if code is None:
            msg = "删除命令已发送（该固件不回应答），请刷新列表核对"
        else:
            msg = "删除成功" if code == 0 else f"删除失败（code={code}）"
        return {"message": msg}

    @app.post("/api/deleteall")
    async def api_deleteall():
        require_connected()
        code = await recorder.delete_all()
        if code is None:
            msg = "删除命令已发送（该固件不回应答），请刷新列表核对"
        else:
            msg = "全部删除成功" if code == 0 else f"删除失败（code={code}）"
        return {"message": msg}

    # ------------------------------------------------ 语音转文字

    @app.post("/api/transcribe")
    async def api_transcribe(req: Request):
        busy_guard()
        body = await read_json(req)
        language = str(body.get("language") or "auto")
        if language not in asr.LANGUAGES:
            raise RecorderError(f"language 须为 {'/'.join(asr.LANGUAGES)}")
        async with op_lock:
            if body.get("index") is not None:
                entry = entry_by_index(body.get("index"))
                path = local_path_for(entry)
                if path.exists():
                    log_push("INFO", f"复用已下载文件：{path.name}")
                else:
                    require_connected()
                    log_push("INFO", f"下载 {entry.name} ...")
                    result = await recorder.download(entry)
                    path = result.path
            else:
                # 仅允许下载目录内的文件，防目录穿越
                name = Path(str(body.get("name") or "")).name
                path = recorder.output_dir / name
                if not name or not path.is_file():
                    raise RecorderError("本地文件不存在")
            if not asr.is_loaded():
                log_push("INFO", "加载识别模型（首次使用需下载约 900MB，"
                                 "请耐心等待）...")
            try:
                text = await asr.transcribe_file(path, language=language)
            except asr.AsrNotAvailable as exc:
                raise RecorderError(str(exc)) from exc
        txt_path = path.with_suffix(".txt")
        txt_path.write_text((text or "") + "\n", encoding="utf-8")
        return {"text": text or "", "txt": txt_path.name,
                "source": path.name}

    @app.get("/api/local")
    async def api_local():
        out = []
        if recorder.output_dir.exists():
            entries = sorted(recorder.output_dir.iterdir(),
                             key=lambda x: x.stat().st_mtime, reverse=True)
            for p in entries:
                if p.is_file():
                    out.append({"name": p.name,
                                "size_text": fmt_size(p.stat().st_size),
                                "url": f"/downloads/{p.name}",
                                "kind": p.suffix.lower().lstrip(".")})
        return out

    # ------------------------------------------------ 实时转写 / 录音控制

    @app.post("/api/rt")
    async def api_rt(req: Request):
        require_connected()
        action = (await read_json(req)).get("action")
        if action == "start":
            rt_state["bytes"] = 0
            await recorder.realtime_start()
            return {"message": "已发送开始实时转写，等待设备推流"}
        if action == "stop":
            session = await recorder.realtime_stop()
            if session is not None and session.path is not None:
                return {"message": f"实时码流已保存：{session.path.name}"
                                   f"（{fmt_size(session.received)}）",
                        "url": f"/downloads/{session.path.name}"}
            return {"message": "实时会话已结束"}
        if action in ("pause", "resume"):
            await recorder.realtime_pause(action == "pause")
            return {"message": "已发送"
                    + ("暂停" if action == "pause" else "继续")}
        raise RecorderError("action 须为 start/stop/pause/resume")

    @app.post("/api/rec")
    async def api_rec(req: Request):
        require_connected()
        action = (await read_json(req)).get("action")
        ops = {"start": (recorder.record_start, "开始录音"),
               "save": (recorder.record_save, "保存录音"),
               "pause": (recorder.record_pause, "暂停录音"),
               "resume": (recorder.record_resume, "继续录音")}
        if action in ops:
            fn, zh = ops[action]
            r = await fn()
            return {"message": f"{zh}：{RESULT_NAMES.get(r, r)}"}
        if action == "state":
            s = await recorder.record_state()
            return {"message": f"录音状态:{STATE_NAMES.get(s, s)}"}
        if action == "time":
            duration, size = await recorder.record_time()
            return {"message": f"录音时长 {fmt_duration(duration)}，"
                               f"当前大小 {fmt_size(size)}"}
        if action == "name":
            return {"message":
                    f"当前文件名：{await recorder.record_filename()}"}
        raise RecorderError("action 无效")

    @app.get("/api/gain")
    async def api_gain_get():
        require_connected()
        g = await recorder.get_gain()
        return {"gain": g, "text": GAIN_NAMES.get(g, str(g))}

    @app.post("/api/gain")
    async def api_gain_set(req: Request):
        require_connected()
        level = (await read_json(req)).get("level")
        if level not in (1, 2, 3):
            raise RecorderError("level 须为 1/2/3")
        r = await recorder.set_gain(level)
        return {"message": "设置增益成功" if r == 0
                else f"设置失败（code={r}）"}

    # ------------------------------------------------ 调试

    @app.post("/api/raw")
    async def api_raw(req: Request):
        require_connected()
        body = await read_json(req)
        try:
            type_, cmd = int(body.get("type")), int(body.get("cmd"))
            params = bytes.fromhex(
                str(body.get("params") or "").replace("0x", "")
                .replace(" ", ""))
        except (TypeError, ValueError):
            raise RecorderError("type/cmd 须为整数，params 须为 hex")
        await recorder.send_raw_command(type_, cmd, params)
        return {"message": f"已发送 {type_}-{cmd} "
                           f"params={params.hex(' ') or '-'}，"
                           "应答见日志设备事件"}

    @app.post("/api/rawframe")
    async def api_rawframe(req: Request):
        require_connected()
        raw = str((await read_json(req)).get("hex") or "")
        try:
            frame = bytes.fromhex(raw.replace("0x", "").replace(" ", ""))
        except ValueError:
            frame = b""
        if not frame:
            raise RecorderError("完整帧 hex 无效")
        await recorder.send_raw_frame(frame)
        return {"message": f"已直发 {len(frame)}B：{frame.hex(' ')}"}

    # ------------------------------------------------ WebSocket / 静态资源

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        sockets.add(ws)
        try:
            while True:
                await ws.receive_text()   # 客户端不发数据，保持连接即可
        except WebSocketDisconnect:
            pass
        finally:
            sockets.discard(ws)

    recorder.output_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/downloads", StaticFiles(directory=str(recorder.output_dir)),
              name="downloads")
    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True),
                  name="web")
    return app


def run_server(output_dir: Path, host: str = "127.0.0.1",
               port: int = 8000) -> None:
    """启动 Web 服务（阻塞直到 Ctrl+C）。"""
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(_INSTALL_HINT) from exc
    app = create_app(output_dir)
    print(f"Web 界面已启动：http://{host}:{port}/  （Ctrl+C 退出）")
    uvicorn.run(app, host=host, port=port, log_level="warning")
