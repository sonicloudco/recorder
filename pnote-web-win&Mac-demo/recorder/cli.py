"""命令行交互式界面。

启动后进入 REPL，输入 help 查看全部命令。
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import re
import shlex
import sys
from pathlib import Path
from typing import List, Optional

from . import asr
from . import protocol as P
from .device import Recorder, RecorderError
from .protocol import FileEntry, Frame

logger = logging.getLogger(__name__)

HELP_TEXT = """\
可用命令：
  连接管理
    scan [秒] [compat]  扫描录音笔（默认 6 秒；compat 列出全部有名设备，
                         兼容广播不带服务 UUID 的固件）
    connect <序号|地址>   连接扫描结果中的设备
    disconnect           断开连接
    status               显示连接状态 / MTU

  设备信息
    smoke                只读项巡检（电量/容量/固件/授权码/录音状态/
                         时长/文件名/增益/文件列表，对应验收清单）
    info                 电量 + 容量 + 固件版本一次查询
    battery              查询电量
    capacity             查询存储容量
    version              查询固件版本
    auth                 查询授权码
    synctime             按本机时间同步设备时间

  文件操作
    list                 拉取文件列表
    download <序号|all> [offset] [文件名]
                         下载文件（WAV 优先，自动候选名；
                         指定 offset 可续传，此时不换候选名；
                         下载中按 Ctrl+C 发送 2-7 终止）
    seg <序号> <start> <end>
                         2-12 分段下载指定字节范围
    transcribe <序号|all|本地文件> [zh|en|auto...]
                         语音转文字（本地离线 SenseVoice；序号目标
                         未下载时先自动下载；结果存为同名 .txt）
    delete <序号>        删除单个文件（需确认）
    deleteall            删除全部文件（需确认）

  实时转写
    rt start             开始实时音频推流（OPUS 码流保存到本地）
    rt stop              停止实时推流
    rt pause / rt resume 暂停 / 继续

  录音控制
    rec start|save|pause|resume   远程控制录音
    rec state            查询录音状态
    rec time             查询录音时长与当前大小
    rec name             查询当前录音文件名
    gain [1|2|3]         查询或设置增益（1低 2中 3高）

  其他
    raw <type> <cmd> [参数hex]   按协议封包发送任意命令（调试用）
    rawframe <完整帧hex>         直接发送完整帧（抓包复现）
    help                 显示本帮助
    quit / exit          退出程序
"""

STATE_NAMES = {1: "录音中", 2: "未录音", 3: "暂停"}
GAIN_NAMES = {1: "低", 2: "中", 3: "高"}
RESULT_NAMES = {1: "成功", 2: "失败"}
RT_STATE_NAMES = {0: "继续", 1: "暂停", 2: "停止"}


def fmt_duration(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def fmt_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def fmt_file_time(value: int) -> str:
    """列表 time 字段：个别固件用绝对时间戳而非时长（7.1 节）。"""
    if P.is_epoch_timestamp(value):
        return datetime.datetime.fromtimestamp(value).strftime(
            "%Y-%m-%d %H:%M")
    return fmt_duration(value)


class Cli:
    def __init__(self, output_dir: Path) -> None:
        self.recorder = Recorder(output_dir=output_dir)
        self.devices: list = []
        self.files: List[FileEntry] = []
        self.rt_bytes = 0
        self.recorder.on_progress = self._print_progress
        self.recorder.on_realtime = self._on_realtime
        self.recorder.on_device_event = self._on_device_event

    # ------------------------------------------------------------ 回调

    def _print_progress(self, received: int, expected: int) -> None:
        if expected > 0:
            pct = min(received / expected * 100, 100.0)
            sys.stdout.write(
                f"\r  下载中 {fmt_size(received)} / ~{fmt_size(expected)}"
                f" ({pct:.0f}%)   ")
        else:
            sys.stdout.write(f"\r  下载中 {fmt_size(received)}   ")
        sys.stdout.flush()

    def _on_realtime(self, event: str, payload) -> None:
        if event == "filename":
            print(f"\n[实时] 设备录音文件名：{payload}")
        elif event == "audio":
            self.rt_bytes += len(payload)
            sys.stdout.write(f"\r[实时] 已接收码流 {fmt_size(self.rt_bytes)}   ")
            sys.stdout.flush()
        elif event == "state":
            print(f"\n[实时] 设备状态：{RT_STATE_NAMES.get(payload, payload)}")

    def _on_device_event(self, frame: Frame) -> None:
        """AE23 机身按键 / 状态事件。"""
        if frame.type == P.TYPE_KEY:
            names = {P.KEY_REC_START: "开始录音", P.KEY_REC_SAVE: "保存录音",
                     P.KEY_REC_PAUSE: "暂停录音", P.KEY_REC_RESUME: "继续录音",
                     P.KEY_STATE_RESP: "录音状态"}
            desc = names.get(frame.cmd, f"cmd={frame.cmd}")
            print(f"\n[设备事件] {desc} body={frame.body.hex(' ') or '-'}")
        else:
            print(f"\n[设备事件] type={frame.type} cmd={frame.cmd} "
                  f"body={frame.body.hex(' ') or '-'}")

    # ------------------------------------------------------------ 命令

    async def cmd_scan(self, args: List[str]) -> None:
        compat = "compat" in [a.lower() for a in args]
        nums = [a for a in args if a.replace(".", "").isdigit()]
        timeout = float(nums[0]) if nums else 6.0
        print(f"扫描中（{timeout:.0f} 秒{'，兼容模式' if compat else ''}）...")
        self.devices = await self.recorder.scan(timeout, compat=compat)
        if not self.devices:
            print("未发现录音笔；可尝试 scan compat 列出全部设备")
            return
        for i, dev in enumerate(self.devices):
            print(f"  [{i}] {dev.name or '(无名称)'}  {dev.address}")

    async def cmd_connect(self, args: List[str]) -> None:
        if not args:
            print("用法：connect <序号|MAC地址>")
            return
        target = args[0]
        device = None
        if target.isdigit() and int(target) < len(self.devices):
            device = self.devices[int(target)]
        else:
            device = target  # bleak 支持直接传地址
        print("连接中...")
        await self.recorder.connect(device)
        print(f"已连接，MTU={self.recorder.transport.mtu}，"
              f"单写载荷上限={self.recorder.transport.payload_size}B")
        # 连接后同步一次时间（协议 0-0）
        try:
            await self.recorder.sync_time()
            print("已同步设备时间")
        except Exception as exc:
            print(f"时间同步失败（忽略）：{exc}")

    async def cmd_disconnect(self, _args: List[str]) -> None:
        await self.recorder.disconnect()
        print("已断开")

    async def cmd_status(self, _args: List[str]) -> None:
        if self.recorder.is_connected:
            t = self.recorder.transport
            print(f"已连接  MTU={t.mtu}  单写上限={t.payload_size}B")
        else:
            print("未连接")

    async def cmd_info(self, _args: List[str]) -> None:
        battery = await self.recorder.get_battery()
        remain, total = await self.recorder.get_capacity()
        version = await self.recorder.get_version()
        bat_str = "充电中" if battery == P.BATTERY_CHARGING else f"{battery}%"
        print(f"电量：{bat_str}")
        print(f"容量：剩余 {fmt_size(remain * 1024)} / 共 {fmt_size(total * 1024)}")
        print(f"固件：{version}")

    async def cmd_battery(self, _args: List[str]) -> None:
        battery = await self.recorder.get_battery()
        print("电量：充电中" if battery == P.BATTERY_CHARGING
              else f"电量：{battery}%")

    async def cmd_capacity(self, _args: List[str]) -> None:
        remain, total = await self.recorder.get_capacity()
        print(f"容量：剩余 {fmt_size(remain * 1024)} / 共 {fmt_size(total * 1024)}")

    async def cmd_version(self, _args: List[str]) -> None:
        print(f"固件版本:{await self.recorder.get_version()}")

    async def cmd_auth(self, _args: List[str]) -> None:
        code = await self.recorder.get_auth_code()
        print(f"授权码：{code.hex(' ')}  (ASCII: "
              f"{code.decode('ascii', errors='replace')})")

    async def cmd_synctime(self, _args: List[str]) -> None:
        await self.recorder.sync_time()
        print("已按本机时间同步")

    async def cmd_smoke(self, _args: List[str]) -> None:
        """只读项巡检（参考厂家测试页，对应协议第 11 节验收清单）。"""
        async def step(label, coro_fn, render=str):
            try:
                value = await coro_fn()
                print(f"  {label}：{render(value)}")
            except asyncio.TimeoutError:
                print(f"  {label}：无应答（超时）")
            except Exception as exc:
                print(f"  {label}：失败 {exc}")
            await asyncio.sleep(0.26)  # 命令间隔，避免固件应接不暇

        print("开始只读巡检（不发送任何写入/删除命令）：")
        await step("电量", self.recorder.get_battery,
                   lambda v: "充电中" if v == P.BATTERY_CHARGING else f"{v}%")
        await step("容量", self.recorder.get_capacity,
                   lambda v: f"剩余 {fmt_size(v[0] * 1024)} / "
                             f"共 {fmt_size(v[1] * 1024)}")
        await step("固件", self.recorder.get_version)
        await step("授权码", self.recorder.get_auth_code,
                   lambda v: v.hex(" "))
        await step("录音状态", self.recorder.record_state,
                   lambda v: STATE_NAMES.get(v, v))
        await step("录音时间", self.recorder.record_time,
                   lambda v: f"{fmt_duration(v[0])} / {fmt_size(v[1])}")
        await step("当前文件名", self.recorder.record_filename)
        await step("增益", self.recorder.get_gain,
                   lambda v: GAIN_NAMES.get(v, v))
        await step("文件列表", self.recorder.get_file_list,
                   lambda v: f"{len(v)} 个文件")
        print("巡检完成")

    async def cmd_list(self, _args: List[str]) -> None:
        print("拉取文件列表...")
        self.files = await self.recorder.get_file_list()
        if not self.files:
            print("设备上没有录音文件")
            return
        print(f"共 {len(self.files)} 个文件：")
        print(f"  {'序号':<4}{'时长/时间':>16}{'大小':>10}  文件名")
        for i, f in enumerate(self.files):
            print(f"  [{i:<2}]{fmt_file_time(f.duration):>16}"
                  f"{fmt_size(f.size):>10}  {f.name}")

    async def cmd_download(self, args: List[str]) -> None:
        if not args:
            print("用法：download <序号|all> [offset] [文件名]")
            return
        if not self.files:
            print("请先执行 list 获取文件列表")
            return
        offset = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
        filename = args[2] if len(args) > 2 else None
        if args[0].lower() == "all":
            targets = list(enumerate(self.files))
            offset, filename = 0, None  # 批量下载不支持续传
        elif args[0].isdigit() and int(args[0]) < len(self.files):
            idx = int(args[0])
            targets = [(idx, self.files[idx])]
        else:
            print("无效序号")
            return
        for idx, entry in targets:
            print(f"下载 [{idx}] {entry.name} "
                  f"(时长 {fmt_duration(entry.duration)}"
                  + (f"，续传 offset={offset}" if offset else "") + ")...")
            try:
                result = await self.recorder.download(
                    entry, offset=offset, filename=filename)
                print()  # 结束进度行
                if result.wav_info is not None and result.is_wav:
                    w = result.wav_info
                    ok = "声明长度一致" if w.ok else \
                        f"声明 {w.declared}B ≠ 实际 {len(result.data)}B！"
                    kind = (f"WAV {w.sample_rate}Hz {w.bits_per_sample}bit "
                            f"{w.channels}ch（{ok}）")
                else:
                    kind = "原始码流"
                print(f"  完成：{result.path}  {fmt_size(len(result.data))}"
                      f"  {kind}")
            except RecorderError as exc:
                print(f"\n  下载失败：{exc}")

    async def cmd_seg(self, args: List[str]) -> None:
        """2-12 分段导入：seg <序号> <start> <end>。"""
        if len(args) < 3 or not all(a.isdigit() for a in args[:3]):
            print("用法：seg <序号> <start字节> <end字节>")
            return
        if not self.files:
            print("请先执行 list 获取文件列表")
            return
        idx, start, end = int(args[0]), int(args[1]), int(args[2])
        if idx >= len(self.files):
            print("无效序号")
            return
        entry = self.files[idx]
        print(f"分段下载 [{idx}] {entry.name} 范围 [{start}, {end})...")
        try:
            result = await self.recorder.download_segment(entry, start, end)
            print(f"\n  完成：{result.path}  {fmt_size(len(result.data))}")
        except RecorderError as exc:
            print(f"\n  分段下载失败：{exc}")

    async def _ensure_downloaded(self, entry: FileEntry) -> Optional[Path]:
        """转写前定位本地文件：已下载则复用，否则先下载。"""
        safe = re.sub(r'[\\/:*?"<>|]', "_",
                      entry.candidate_names()[0]).rstrip(". ")
        existing = self.recorder.output_dir / safe
        if existing.exists():
            print(f"  复用已下载文件：{existing}")
            return existing
        if not self.recorder.is_connected:
            print(f"  本地无 {safe} 且未连接设备，无法下载")
            return None
        print(f"下载 {entry.name} ...")
        try:
            result = await self.recorder.download(entry)
            print()
            return result.path
        except RecorderError as exc:
            print(f"\n  下载失败：{exc}")
            return None

    async def cmd_transcribe(self, args: List[str]) -> None:
        """transcribe <序号|all|本地文件> [语言]：本地离线语音转文字。"""
        if not args:
            print("用法：transcribe <序号|all|本地文件路径> "
                  f"[{'|'.join(asr.LANGUAGES)}]")
            return
        language = "auto"
        if len(args) > 1 and args[-1].lower() in asr.LANGUAGES:
            language = args[-1].lower()
        target = args[0]
        paths: List[Path] = []
        if target.lower() == "all" or target.isdigit():
            if not self.files:
                print("请先执行 list 获取文件列表")
                return
            if target.lower() == "all":
                entries = list(self.files)
            elif int(target) < len(self.files):
                entries = [self.files[int(target)]]
            else:
                print("无效序号")
                return
            for entry in entries:
                path = await self._ensure_downloaded(entry)
                if path is not None:
                    paths.append(path)
        else:
            p = Path(target)
            if not p.is_file():
                print(f"文件不存在：{p}")
                return
            paths.append(p)
        if paths and not asr.is_loaded():
            print("加载识别模型（首次使用需下载约 900MB，请耐心等待）...")
        for path in paths:
            print(f"转写 {path.name} ...")
            try:
                text = await asr.transcribe_file(path, language=language)
            except asr.AsrNotAvailable as exc:
                print(f"  {exc}")
                return
            except Exception as exc:
                print(f"  转写失败：{exc}")
                continue
            txt_path = path.with_suffix(".txt")
            txt_path.write_text((text or "") + "\n", encoding="utf-8")
            print(f"  文本已保存:{txt_path}")
            print(f"  内容:{text or '（未识别到语音）'}")

    async def _confirm(self, prompt: str) -> bool:
        """非阻塞确认输入，避免卡住事件循环。"""
        loop = asyncio.get_running_loop()
        answer = await loop.run_in_executor(None, input, prompt)
        return answer.strip().lower() == "yes"

    async def cmd_delete(self, args: List[str]) -> None:
        if not args or not args[0].isdigit():
            print("用法：delete <序号>")
            return
        if not self.files:
            print("请先执行 list 获取文件列表")
            return
        idx = int(args[0])
        if idx >= len(self.files):
            print("无效序号")
            return
        entry = self.files[idx]
        if not await self._confirm(
                f"确认删除设备上的 {entry.name}？此操作不可恢复 (yes/N): "):
            print("已取消")
            return
        code = await self.recorder.delete_file(entry)
        if code is None:
            print("删除命令已发送（该固件不回应答），请用 list 核对")
        else:
            print("删除成功" if code == 0 else f"删除失败（code={code}）")

    async def cmd_deleteall(self, _args: List[str]) -> None:
        if not await self._confirm(
                "确认删除设备上的全部录音？此操作不可恢复 (yes/N): "):
            print("已取消")
            return
        code = await self.recorder.delete_all()
        if code is None:
            print("删除命令已发送（该固件不回应答），请用 list 核对")
        else:
            print("全部删除成功" if code == 0 else f"删除失败（code={code}）")

    async def cmd_rt(self, args: List[str]) -> None:
        sub = args[0].lower() if args else ""
        if sub == "start":
            self.rt_bytes = 0
            await self.recorder.realtime_start()
            print("已发送开始实时转写，等待设备推流...")
        elif sub == "stop":
            session = await self.recorder.realtime_stop()
            print()
            if session is not None and session.path is not None:
                print(f"实时码流已保存：{session.path} "
                      f"({fmt_size(session.received)})")
            else:
                print("实时会话已结束")
        elif sub == "pause":
            await self.recorder.realtime_pause(True)
            print("已发送暂停")
        elif sub == "resume":
            await self.recorder.realtime_pause(False)
            print("已发送继续")
        else:
            print("用法：rt start|stop|pause|resume")

    async def cmd_rec(self, args: List[str]) -> None:
        sub = args[0].lower() if args else ""
        if sub == "start":
            r = await self.recorder.record_start()
            print(f"开始录音：{RESULT_NAMES.get(r, r)}")
        elif sub == "save":
            r = await self.recorder.record_save()
            print(f"保存录音：{RESULT_NAMES.get(r, r)}")
        elif sub == "pause":
            r = await self.recorder.record_pause()
            print(f"暂停录音：{RESULT_NAMES.get(r, r)}")
        elif sub == "resume":
            r = await self.recorder.record_resume()
            print(f"继续录音：{RESULT_NAMES.get(r, r)}")
        elif sub == "state":
            s = await self.recorder.record_state()
            print(f"录音状态：{STATE_NAMES.get(s, s)}")
        elif sub == "time":
            duration, size = await self.recorder.record_time()
            print(f"录音时长 {fmt_duration(duration)}，"
                  f"当前大小 {fmt_size(size)}")
        elif sub == "name":
            print(f"当前文件名：{await self.recorder.record_filename()}")
        else:
            print("用法：rec start|save|pause|resume|state|time|name")

    async def cmd_gain(self, args: List[str]) -> None:
        if args and args[0] in ("1", "2", "3"):
            r = await self.recorder.set_gain(int(args[0]))
            print("设置增益成功" if r == 0 else f"设置失败（code={r}）")
        else:
            g = await self.recorder.get_gain()
            print(f"当前增益：{GAIN_NAMES.get(g, g)}")

    async def cmd_raw(self, args: List[str]) -> None:
        """raw <type> <cmd> [参数hex]：按协议封包发送任意命令。"""
        if len(args) < 2 or not args[0].isdigit() or not args[1].isdigit():
            print("用法：raw <type> <cmd> [参数hex，如 01ff 或 '01 ff']")
            return
        params = b""
        if len(args) > 2:
            cleaned = "".join(args[2:]).replace("0x", "").replace(" ", "")
            try:
                params = bytes.fromhex(cleaned)
            except ValueError:
                print("参数 hex 无效")
                return
        await self.recorder.send_raw_command(int(args[0]), int(args[1]),
                                             params)
        print(f"已发送 {args[0]}-{args[1]} params={params.hex(' ') or '-'}，"
              "应答见[设备事件]输出或 -v 日志")

    async def cmd_rawframe(self, args: List[str]) -> None:
        """rawframe <完整帧hex>：直接发送完整帧（抓包复现）。"""
        cleaned = "".join(args).replace("0x", "").replace(" ", "")
        try:
            frame = bytes.fromhex(cleaned)
        except ValueError:
            frame = b""
        if not frame:
            print("用法：rawframe <完整帧hex，含帧头>")
            return
        await self.recorder.send_raw_frame(frame)
        print(f"已直发 {len(frame)}B：{frame.hex(' ')}")

    # ------------------------------------------------------------ REPL

    NEED_CONNECTION = {
        "disconnect", "smoke", "info", "battery", "capacity", "version",
        "auth", "synctime", "list", "download", "seg", "delete",
        "deleteall", "rt", "rec", "gain", "raw", "rawframe",
    }

    async def run(self) -> None:
        print("CB08 录音笔处理程序（输入 help 查看命令，quit 退出）")
        loop = asyncio.get_running_loop()
        while True:
            try:
                line = await loop.run_in_executor(None, input, "record> ")
            except (EOFError, KeyboardInterrupt):
                break
            line = line.strip()
            if not line:
                continue
            try:
                # posix=False：保留 Windows 路径中的反斜杠，同时支持引号包含空格
                parts = shlex.split(line, posix=False)
            except ValueError:
                parts = line.split()
            # 去掉 posix=False 模式下保留的成对引号
            parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1]
                     and p[0] in "\"'" else p for p in parts]
            cmd, args = parts[0].lower(), parts[1:]
            if cmd in ("quit", "exit"):
                break
            if cmd == "help":
                print(HELP_TEXT)
                continue
            handler = getattr(self, f"cmd_{cmd}", None)
            if handler is None:
                print(f"未知命令：{cmd}（输入 help 查看命令）")
                continue
            if cmd in self.NEED_CONNECTION and not self.recorder.is_connected:
                print("尚未连接设备，请先 scan 后 connect")
                continue
            try:
                await handler(args)
            except asyncio.TimeoutError:
                print("等待设备应答超时")
            except KeyboardInterrupt:
                # 主动取消：若处于下载会话发送 2-7 终止导入
                print("\n已中断当前操作")
                if self.recorder.is_connected:
                    await self.recorder.abort_download()
            except RecorderError as exc:
                print(f"错误：{exc}")
            except Exception as exc:
                logger.exception("命令执行异常")
                print(f"异常：{exc}")
        # 退出前清理
        if self.recorder.is_connected:
            await self.recorder.disconnect()
        print("再见")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="CB08 录音笔处理程序")
    parser.add_argument("-o", "--output", default="downloads",
                        help="下载文件保存目录（默认 downloads）")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="输出调试日志")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(Cli(Path(args.output)).run())
    except KeyboardInterrupt:
        pass
