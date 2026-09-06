"""高层设备逻辑：请求/应答匹配、文件列表组装、下载会话、实时音频与录音控制。

超时与兼容策略（协议第 9 节）：
    - 列表无 CMD=18：收到数据后空闲约 1.2 秒 best-effort 收尾
    - 文件下载无数据：空闲约 12 秒判定超时；received=0 可换候选名
    - 传输中断且已有数据：不自动换文件名
    - 主动取消：发送 2-7，清理定时器与 Future
    - 旧固件删除命令可能不回应答，等待超时按“已发送”处理
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import protocol as P
from .ble import BleTransport
from .protocol import CrcError, FileEntry, Frame, FrameParser, SeqGenerator

logger = logging.getLogger(__name__)

CMD_TIMEOUT = 5.0          # 普通请求应答超时
LIST_IDLE_TIMEOUT = 1.2    # 列表最后一帧后的空闲收尾
DOWNLOAD_IDLE_TIMEOUT = 12.0  # 文件下载空闲超时
DELETE_RESP_TIMEOUT = 3.0  # 删除应答等待（旧固件可能不发送）


@dataclass
class DownloadResult:
    """一次文件下载会话的结果。"""
    filename: str          # 实际成功的请求文件名
    data: bytes            # 完整文件字节
    path: Optional[Path] = None  # 写盘路径
    is_wav: bool = False
    wav_info: Optional[P.WavInfo] = None  # 7.4 节验收：声明长度/采样参数
    device_name: str = ""  # 2-3 帧中设备回报的实际导入文件名


@dataclass
class RealtimeSession:
    """实时转写会话状态。"""
    filename: str = ""     # 设备通告的本次录音文件名
    received: int = 0      # 已接收音频字节数
    path: Optional[Path] = None
    _fp: Optional[object] = dc_field(default=None, repr=False)


class RecorderError(Exception):
    pass


class Recorder:
    """CB08 录音笔高层封装。所有方法需在同一 asyncio loop 中调用。"""

    def __init__(self, output_dir: Path = Path("downloads")) -> None:
        self.transport = BleTransport()
        self.output_dir = Path(output_dir)
        self._seq = SeqGenerator()
        self._parser_main = FrameParser("AE22")
        self._parser_key = FrameParser("AE23")
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # (type, cmd) -> Future 的一次性应答等待表
        self._waiters: Dict[Tuple[int, int], asyncio.Future] = {}

        # 文件列表组装状态
        self._list_entries: List[FileEntry] = []
        self._list_future: Optional[asyncio.Future] = None
        self._list_idle_handle: Optional[asyncio.TimerHandle] = None

        # 下载会话状态
        self._dl_active = False
        self._dl_started = False
        self._dl_device_name = ""
        self._dl_buf = bytearray()
        self._dl_future: Optional[asyncio.Future] = None
        self._dl_idle_handle: Optional[asyncio.TimerHandle] = None
        self._dl_expected = 0
        self._dl_last_report = 0.0
        self.on_progress: Optional[Callable[[int, int], None]] = None

        # 实时会话
        self._rt: Optional[RealtimeSession] = None
        self.on_realtime: Optional[Callable[[str, object], None]] = None

        # 设备事件（AE23 按键等）
        self.on_device_event: Optional[Callable[[Frame], None]] = None

        self.transport.on_main = self._feed_main
        self.transport.on_key = self._feed_key
        self.transport.on_disconnect = self._handle_disconnect

    # ============================================================ 连接管理

    async def scan(self, timeout: float = 6.0, compat: bool = False):
        return await BleTransport.scan(timeout, compat=compat)

    async def connect(self, device) -> None:
        self._loop = asyncio.get_running_loop()
        await self.transport.connect(device)

    async def disconnect(self) -> None:
        self._cleanup_sessions(RecorderError("连接已断开"))
        await self.transport.disconnect()

    @property
    def is_connected(self) -> bool:
        return self.transport.is_connected

    def _handle_disconnect(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(
                self._cleanup_sessions, RecorderError("设备已断开"))

    def _cleanup_sessions(self, exc: Exception) -> None:
        """断开/异常时结束全部挂起的 Future 并释放定时器。"""
        for fut in list(self._waiters.values()):
            if not fut.done():
                fut.set_exception(exc)
        self._waiters.clear()
        for handle_name in ("_list_idle_handle", "_dl_idle_handle"):
            handle = getattr(self, handle_name)
            if handle is not None:
                handle.cancel()
                setattr(self, handle_name, None)
        for fut_name in ("_list_future", "_dl_future"):
            fut = getattr(self, fut_name)
            if fut is not None and not fut.done():
                fut.set_exception(exc)
            setattr(self, fut_name, None)
        self._dl_active = False
        self._close_realtime_file()
        self._rt = None
        self._parser_main.reset()
        self._parser_key.reset()

    # ============================================================ 帧收发

    def _feed_main(self, chunk: bytes) -> None:
        self._dispatch_stream(self._parser_main, chunk, source="AE22")

    def _feed_key(self, chunk: bytes) -> None:
        self._dispatch_stream(self._parser_key, chunk, source="AE23")

    def _dispatch_stream(self, parser: FrameParser, chunk: bytes,
                         source: str) -> None:
        """bleak 回调线程可能不在 loop 线程，统一调度回 loop。"""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(
            self._dispatch_chunk, parser, chunk, source)

    def _dispatch_chunk(self, parser: FrameParser, chunk: bytes,
                        source: str) -> None:
        while True:
            try:
                for frame in parser.feed(chunk):
                    self._handle_frame(frame, source)
                return
            except CrcError as exc:
                # 丢弃损坏帧并记录原始 hex，继续解析剩余缓冲
                logger.warning("%s", exc)
                chunk = b""

    def _handle_frame(self, frame: Frame, source: str) -> None:
        if frame.is_ack:
            logger.debug("ACK type=%d from %s", frame.type, source)
            return
        key = (frame.type, frame.cmd)

        # 下载会话专用帧优先处理
        if frame.type == P.TYPE_FILE and self._dl_active and \
                frame.cmd in (P.FILE_IMPORT_START, P.FILE_DATA,
                              P.FILE_IMPORT_END):
            self._handle_download_frame(frame)
            return

        # 文件列表帧
        if frame.type == P.TYPE_FILE and frame.cmd == P.FILE_LIST_DATA:
            self._handle_list_frame(frame)
            return
        if frame.type == P.TYPE_FILE and frame.cmd == P.FILE_LIST_DONE:
            self._finish_list()
            return

        # 实时音频帧
        if frame.type == P.TYPE_REALTIME:
            self._handle_realtime_frame(frame)
            return

        # 文件数据帧但无下载会话：警告并丢弃，避免误报设备事件
        if frame.type == P.TYPE_FILE and frame.cmd == P.FILE_DATA:
            logger.warning("收到文件数据但无下载会话，%dB 已忽略", len(frame.body))
            return

        # 一次性请求应答
        fut = self._waiters.pop(key, None)
        if fut is not None and not fut.done():
            fut.set_result(frame)
            return

        # AE23 或无人等待的 TYPE=3：视为设备事件（机身按键触发）
        if self.on_device_event is not None:
            self.on_device_event(frame)
        else:
            logger.debug("未处理帧 %s type=%d cmd=%s body=%s",
                         source, frame.type, frame.cmd, frame.body.hex(" "))

    async def _request(self, type_: int, cmd: int, params: bytes,
                       resp_cmd: int, resp_type: Optional[int] = None,
                       timeout: float = CMD_TIMEOUT) -> Frame:
        """发送命令并等待指定 (type, cmd) 的应答帧。"""
        resp_type = type_ if resp_type is None else resp_type
        key = (resp_type, resp_cmd)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters[key] = fut
        try:
            frame = P.build_command(self._seq.next(), type_, cmd, params)
            await self.transport.write_frame(frame)
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._waiters.pop(key, None)

    async def _send(self, type_: int, cmd: int, params: bytes = b"") -> None:
        frame = P.build_command(self._seq.next(), type_, cmd, params)
        await self.transport.write_frame(frame)

    # ============================================================ 控制命令

    async def sync_time(self, ts: Optional[time.struct_time] = None) -> None:
        """0-0 同步时间（无应答）。"""
        ts = ts or time.localtime()
        await self._send(P.TYPE_CONTROL, P.CTRL_SYNC_TIME,
                         P.encode_sync_time(ts.tm_year, ts.tm_mon, ts.tm_mday,
                                            ts.tm_hour, ts.tm_min, ts.tm_sec))

    async def get_capacity(self) -> Tuple[int, int]:
        """0-1/0-2 容量查询，返回 (remain, total)，单位 KB。"""
        frame = await self._request(P.TYPE_CONTROL, P.CTRL_GET_CAPACITY, b"",
                                    P.CTRL_CAPACITY_RESP)
        return P.decode_capacity(frame.body)

    async def get_battery(self) -> int:
        """0-3/0-4 电量查询：0~100，110=充电中。"""
        frame = await self._request(P.TYPE_CONTROL, P.CTRL_GET_BATTERY, b"",
                                    P.CTRL_BATTERY_RESP)
        return P.decode_battery(frame.body)

    async def get_version(self) -> str:
        """0-10/0-11 固件版本。"""
        frame = await self._request(P.TYPE_CONTROL, P.CTRL_GET_VERSION, b"",
                                    P.CTRL_VERSION_RESP)
        return frame.body.split(b"\x00", 1)[0].decode("ascii",
                                                      errors="replace")

    async def get_auth_code(self) -> bytes:
        """0-12/0-13 授权码。"""
        frame = await self._request(P.TYPE_CONTROL, P.CTRL_GET_AUTH, b"",
                                    P.CTRL_AUTH_RESP)
        return frame.body

    # ============================================================ 文件列表

    async def get_file_list(self, timeout: float = 15.0) -> List[FileEntry]:
        """2-0 请求文件列表；累积 2-1 各帧条目，2-18 或空闲 1.2s 收尾。"""
        if self._list_future is not None:
            raise RecorderError("已有列表请求进行中")
        self._list_entries = []
        loop = asyncio.get_running_loop()
        self._list_future = loop.create_future()
        try:
            await self._send(P.TYPE_FILE, P.FILE_LIST_REQ)
            # 无文件时设备可能既不发 2-1 也不发 2-18，兜底空闲计时
            self._arm_list_idle()
            return await asyncio.wait_for(self._list_future, timeout)
        finally:
            self._cancel_list_idle()
            self._list_future = None

    def _handle_list_frame(self, frame: Frame) -> None:
        entries = P.decode_file_list(frame.body)
        self._list_entries.extend(entries)
        self._arm_list_idle()  # 每帧刷新空闲收尾计时

    def _arm_list_idle(self) -> None:
        self._cancel_list_idle()
        if self._loop is not None and self._list_future is not None:
            self._list_idle_handle = self._loop.call_later(
                LIST_IDLE_TIMEOUT, self._finish_list)

    def _cancel_list_idle(self) -> None:
        if self._list_idle_handle is not None:
            self._list_idle_handle.cancel()
            self._list_idle_handle = None

    def _finish_list(self) -> None:
        self._cancel_list_idle()
        if self._list_future is not None and not self._list_future.done():
            self._list_future.set_result(list(self._list_entries))

    # ============================================================ 文件下载

    async def download(self, entry: FileEntry, offset: int = 0,
                       filename: Optional[str] = None) -> DownloadResult:
        """按 7.2 节流程下载文件：依次尝试候选文件名。

        offset>0 续传或显式指定 filename 时不换候选名（协议第 9 节：
        传输中断且已有数据时不要自动换文件名）。
        """
        if filename is not None or offset > 0:
            names = [filename or entry.candidate_names()[0]]
        else:
            names = entry.candidate_names()
        last_error: Optional[Exception] = None
        for name in names:
            try:
                return await self._download_once(name, entry, offset)
            except FileNotFoundOnDevice as exc:
                logger.info("候选名 %s 不存在，尝试下一个", name)
                last_error = exc
                continue
        raise last_error or RecorderError("全部候选文件名均下载失败")

    async def download_segment(self, entry: FileEntry, start: int, end: int,
                               filename: Optional[str] = None
                               ) -> DownloadResult:
        """2-12 分段导入：start:4B LE + end:4B LE + filename。"""
        name = filename or entry.candidate_names()[0]
        return await self._download_once(name, entry, 0,
                                         segment=(start, end))

    async def _download_once(self, filename: str, entry: FileEntry,
                             offset: int,
                             segment: Optional[Tuple[int, int]] = None
                             ) -> DownloadResult:
        if self._dl_active:
            raise RecorderError("已有下载会话进行中")
        loop = asyncio.get_running_loop()
        self._dl_active = True
        self._dl_started = False
        self._dl_device_name = ""
        self._dl_buf = bytearray()
        self._dl_future = loop.create_future()
        # WAV 进度估算：时长×32000B/s + 44
        self._dl_expected = (entry.estimated_wav_size
                             if filename.lower().endswith(".wav")
                             else entry.size)
        try:
            # 步骤 3：导入请求帧完整单写（强制约束，不允许拆包）
            if segment is not None:
                frame = P.build_segment_request(
                    self._seq.next(), filename, segment[0], segment[1])
            else:
                frame = P.build_import_request(
                    self._seq.next(), filename, offset)
            await self.transport.write_frame(frame, atomic=True)
            self._arm_download_idle()
            data = await self._dl_future
            wav_info = P.inspect_wav(data)
            result = DownloadResult(filename=filename, data=data,
                                    is_wav=P.is_wav(data),
                                    wav_info=wav_info,
                                    device_name=self._dl_device_name)
            result.path = self._save_download(filename, entry, data)
            return result
        except asyncio.CancelledError:
            await self.abort_download()
            raise
        finally:
            self._cancel_download_idle()
            self._dl_active = False
            self._dl_future = None

    def _handle_download_frame(self, frame: Frame) -> None:
        if frame.cmd == P.FILE_IMPORT_START:
            # 步骤 4：收到 2-3 建立会话；记录设备回报的实际导入名
            self._dl_started = True
            name = frame.body.split(b"\x00", 1)[0].decode(
                "utf-8", errors="replace")
            self._dl_device_name = name
            logger.info("开始导入：%s", name)
            self._arm_download_idle()
        elif frame.cmd == P.FILE_DATA:
            self._dl_buf.extend(frame.body)
            self._arm_download_idle()  # 有效数据刷新空闲超时
            self._report_progress()
        elif frame.cmd == P.FILE_IMPORT_END:
            self._finish_download(frame.body[0] if frame.body else
                                  P.IMPORT_END_STOPPED)

    def _report_progress(self) -> None:
        now = time.monotonic()
        if self.on_progress is not None and now - self._dl_last_report > 0.2:
            self._dl_last_report = now
            self.on_progress(len(self._dl_buf), self._dl_expected)

    def _finish_download(self, code: int) -> None:
        """步骤 5：处理 2-5 结束码。"""
        self._cancel_download_idle()
        fut = self._dl_future
        if fut is None or fut.done():
            return
        if code == P.IMPORT_END_OK:
            fut.set_result(bytes(self._dl_buf))
        elif code == P.IMPORT_END_NOT_FOUND:
            fut.set_exception(FileNotFoundOnDevice("文件不存在（code=1）"))
        elif code == P.IMPORT_END_BAD_OFFSET:
            fut.set_exception(RecorderError(
                "offset 过大（code=2），请重置 offset 后重试"))
        else:
            fut.set_exception(RecorderError(
                f"导入停止（code={code}），已接收 {len(self._dl_buf)}B；"
                "不要自动换文件名，可用 offset 续传"))

    def _arm_download_idle(self) -> None:
        self._cancel_download_idle()
        if self._loop is not None:
            self._dl_idle_handle = self._loop.call_later(
                DOWNLOAD_IDLE_TIMEOUT, self._download_idle_timeout)

    def _cancel_download_idle(self) -> None:
        if self._dl_idle_handle is not None:
            self._dl_idle_handle.cancel()
            self._dl_idle_handle = None

    def _download_idle_timeout(self) -> None:
        fut = self._dl_future
        if fut is None or fut.done():
            return
        if not self._dl_buf:
            # received=0 可换候选名
            fut.set_exception(FileNotFoundOnDevice(
                "下载空闲超时且未收到数据，视为文件名无效"))
        else:
            fut.set_exception(RecorderError(
                f"下载空闲超时，已接收 {len(self._dl_buf)}B，传输中断"))

    async def abort_download(self) -> None:
        """2-7 主动终止导入。"""
        try:
            await self._send(P.TYPE_FILE, P.FILE_IMPORT_ABORT)
        except Exception:
            pass

    def _save_download(self, filename: str, entry: FileEntry,
                       data: bytes) -> Path:
        """写盘；同名文件加入 time/size 后缀避免覆盖。"""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r'[\\/:*?"<>|]', "_", filename).rstrip(". ")
        path = self.output_dir / safe
        if path.exists():
            stem, suffix = path.stem, path.suffix
            path = self.output_dir / \
                f"{stem}_{entry.duration}s_{entry.size}{suffix}"
            n = 1
            while path.exists():
                path = self.output_dir / \
                    f"{stem}_{entry.duration}s_{entry.size}_{n}{suffix}"
                n += 1
        path.write_bytes(data)
        return path

    # ============================================================ 文件删除

    async def delete_file(self, entry: FileEntry) -> Optional[int]:
        """2-8 删除单个文件（28B 原始条目）；旧固件可能无 2-13 应答。"""
        try:
            frame = await self._request(
                P.TYPE_FILE, P.FILE_DELETE_ONE, entry.raw,
                P.FILE_DELETE_ONE_RESP, timeout=DELETE_RESP_TIMEOUT)
            return frame.body[0] if frame.body else None
        except asyncio.TimeoutError:
            return None  # 已发送，固件未应答

    async def delete_all(self) -> Optional[int]:
        """2-9 删除全部文件；旧固件可能无 2-10 应答。"""
        try:
            frame = await self._request(
                P.TYPE_FILE, P.FILE_DELETE_ALL, b"",
                P.FILE_DELETE_ALL_RESP, timeout=DELETE_RESP_TIMEOUT)
            return frame.body[0] if frame.body else None
        except asyncio.TimeoutError:
            return None

    # ============================================================ 实时音频

    async def realtime_start(self) -> RealtimeSession:
        """1-0 开始实时转写；设备开始推流前会通告文件名。"""
        if self._rt is not None:
            raise RecorderError("实时会话已在进行中")
        self._rt = RealtimeSession()
        await self._send(P.TYPE_REALTIME, P.RT_START)
        return self._rt

    async def realtime_stop(self) -> Optional[RealtimeSession]:
        """1-2 结束实时转写，关闭码流备份文件。"""
        await self._send(P.TYPE_REALTIME, P.RT_STOP)
        session, self._rt = self._rt, None
        self._close_realtime_file(session)
        return session

    async def realtime_pause(self, pause: bool) -> None:
        """1-3 暂停/继续：0=继续 1=暂停。"""
        await self._send(P.TYPE_REALTIME, P.RT_PAUSE_RESUME,
                         bytes([1 if pause else 0]))

    def _handle_realtime_frame(self, frame: Frame) -> None:
        rt = self._rt
        if frame.cmd == P.RT_START:
            # 设备通告本次录音文件名
            name = frame.body.split(b"\x00", 1)[0].decode(
                "utf-8", errors="replace")
            if rt is not None:
                rt.filename = name
                self._open_realtime_file(rt)
            self._emit_realtime("filename", name)
        elif frame.cmd == P.RT_AUDIO_DATA:
            if rt is not None:
                rt.received += len(frame.body)
                if rt._fp is not None:
                    rt._fp.write(frame.body)
            self._emit_realtime("audio", frame.body)
        elif frame.cmd == P.RT_DEV_STATE:
            state = frame.body[0] if frame.body else -1
            self._emit_realtime("state", state)
            if state == 2 and rt is not None:  # 设备端停止
                self._rt = None
                self._close_realtime_file(rt)

    def _emit_realtime(self, event: str, payload) -> None:
        if self.on_realtime is not None:
            self.on_realtime(event, payload)

    def _open_realtime_file(self, rt: RealtimeSession) -> None:
        """实时音频码流原样保存作为备份（OPUS 系码流）。"""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        base = re.sub(r'[\\/:*?"<>|]', "_", rt.filename) or \
            time.strftime("realtime-%Y%m%d-%H%M%S")
        if not base.lower().endswith(".opus"):
            base += ".opus"
        rt.path = self.output_dir / base
        n = 1
        while rt.path.exists():
            rt.path = self.output_dir / f"{Path(base).stem}_{n}.opus"
            n += 1
        rt._fp = open(rt.path, "wb")

    def _close_realtime_file(self,
                             rt: Optional[RealtimeSession] = None) -> None:
        rt = rt or self._rt
        if rt is not None and rt._fp is not None:
            try:
                rt._fp.close()
            finally:
                rt._fp = None

    # ============================================================ 录音控制

    async def _key_request(self, cmd: int, resp_cmd: int,
                           params: bytes = b"") -> Frame:
        return await self._request(P.TYPE_KEY, cmd, params, resp_cmd)

    async def record_start(self) -> int:
        """3-1/3-2 开始录音：1成功 2失败。"""
        frame = await self._key_request(P.KEY_REC_START, P.KEY_REC_START_RESP)
        return frame.body[0]

    async def record_save(self) -> int:
        """3-3/3-4 保存录音。"""
        frame = await self._key_request(P.KEY_REC_SAVE, P.KEY_REC_SAVE_RESP)
        return frame.body[0]

    async def record_pause(self) -> int:
        """3-5/3-6 暂停录音。"""
        frame = await self._key_request(P.KEY_REC_PAUSE, P.KEY_REC_PAUSE_RESP)
        return frame.body[0]

    async def record_resume(self) -> int:
        """3-7/3-8 继续录音。"""
        frame = await self._key_request(P.KEY_REC_RESUME,
                                        P.KEY_REC_RESUME_RESP)
        return frame.body[0]

    async def record_state(self) -> int:
        """3-19/3-20 录音状态：1录音中 2未录音 3暂停。"""
        frame = await self._key_request(P.KEY_GET_STATE, P.KEY_STATE_RESP)
        return frame.body[0]

    async def record_time(self) -> Tuple[int, int]:
        """3-21/3-22 录音时间：(duration秒, currentSize字节)。"""
        frame = await self._key_request(P.KEY_GET_TIME, P.KEY_TIME_RESP)
        return P.decode_record_time(frame.body)

    async def record_filename(self) -> str:
        """3-23/3-24 当前录音文件名。"""
        frame = await self._key_request(P.KEY_GET_FILENAME,
                                        P.KEY_FILENAME_RESP)
        return frame.body.split(b"\x00", 1)[0].decode("utf-8",
                                                      errors="replace")

    async def get_gain(self) -> int:
        """3-25/3-26 获取增益：1低 2中 3高。"""
        frame = await self._key_request(P.KEY_GET_GAIN, P.KEY_GAIN_RESP)
        return frame.body[0]

    async def set_gain(self, level: int) -> int:
        """3-27/3-28 设置增益 1~3：返回 0成功 1失败。"""
        if level not in (1, 2, 3):
            raise ValueError("增益取值 1~3")
        frame = await self._key_request(P.KEY_SET_GAIN, P.KEY_SET_GAIN_RESP,
                                        bytes([level]))
        return frame.body[0]

    # ============================================================ 原始命令

    async def send_raw_command(self, type_: int, cmd: int,
                               params: bytes = b"") -> None:
        """按协议封包发送任意 TYPE/CMD/PARAMS（调试厂商新增命令）。"""
        await self._send(type_, cmd, params)

    async def send_raw_frame(self, frame: bytes) -> None:
        """直接发送完整帧字节（抓包复现），不做封包。"""
        await self.transport.write_frame(frame, atomic=True)


class FileNotFoundOnDevice(RecorderError):
    """2-5 code=1：设备上无此文件名，可尝试候选名。"""
