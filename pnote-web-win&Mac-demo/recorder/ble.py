"""BLE 传输层：基于 bleak 的扫描、连接、Notify 订阅与 AE21 写入。

协议第 2 节：
    Service        0xAE20  录音笔业务服务
    Characteristic 0xAE21  WRITE_WITHOUT_RESPONSE  App→Dev 协议帧
    Characteristic 0xAE22  NOTIFY  Dev→App 控制应答、音频、列表、文件数据
    Characteristic 0xAE23  NOTIFY  Dev→App 机身按键及录音状态消息

关键约束：2-2 文件导入请求帧（36B）必须一次 GATT 写入，
不允许分包器拆分（拆成 20+16 会稳定返回“文件不存在”）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, List, Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

logger = logging.getLogger(__name__)


def _uuid16(short: int) -> str:
    """16bit UUID 扩展为 128bit 标准形式。"""
    return f"0000{short:04x}-0000-1000-8000-00805f9b34fb"


SERVICE_UUID = _uuid16(0xAE20)
CHAR_WRITE = _uuid16(0xAE21)
CHAR_NOTIFY_MAIN = _uuid16(0xAE22)
CHAR_NOTIFY_KEY = _uuid16(0xAE23)

DEFAULT_CHUNK = 20  # 未协商 MTU 时的保守单包载荷

# 已知设备型号关键字（厂家测试页确认型号为 QS668，CB08 为早期叫法）
DEVICE_NAME_KEYWORDS = ("cb08", "qs668")


class BleTransport:
    """封装一台录音笔的 BLE 连接与收发。

    on_main / on_key 回调分别收到 AE22 / AE23 的原始通知字节，
    上层各自用独立 FrameParser 处理。
    """

    def __init__(self) -> None:
        self._client: Optional[BleakClient] = None
        self.on_main: Optional[Callable[[bytes], None]] = None
        self.on_key: Optional[Callable[[bytes], None]] = None
        self.on_disconnect: Optional[Callable[[], None]] = None

    # ------------------------------------------------------------ 扫描

    @staticmethod
    async def scan(timeout: float = 6.0,
                   compat: bool = False) -> List[BLEDevice]:
        """扫描广播含 AE20 服务或名称匹配 QS668/CB08 的设备。

        compat=True 时返回全部有名设备（兼容广播不携带服务 UUID
        的固件，由用户自行选择，对应厂家测试页的“兼容扫描”）。
        """
        found: List[BLEDevice] = []
        devices = await BleakScanner.discover(
            timeout=timeout, return_adv=True)
        for device, adv in devices.values():
            name = (device.name or adv.local_name or "")
            uuids = [u.lower() for u in (adv.service_uuids or [])]
            if SERVICE_UUID in uuids or \
                    any(k in name.lower() for k in DEVICE_NAME_KEYWORDS):
                found.append(device)
            elif compat and name:
                found.append(device)
        return found

    # ------------------------------------------------------------ 连接

    async def connect(self, device) -> None:
        """连接并订阅 AE22（必须）与 AE23（按键，尽力订阅）。"""
        client = BleakClient(
            device, disconnected_callback=self._handle_disconnect)
        await client.connect()
        self._client = client

        # 连接成功后必须先订阅 AE22
        await client.start_notify(CHAR_NOTIFY_MAIN, self._notify_main)
        try:
            await client.start_notify(CHAR_NOTIFY_KEY, self._notify_key)
        except Exception as exc:  # 部分固件可能无 AE23
            logger.warning("AE23 订阅失败（忽略）：%s", exc)

    async def disconnect(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            try:
                await client.disconnect()
            except Exception:
                pass

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    @property
    def mtu(self) -> int:
        """真实 ATT_MTU；bleak 在 Windows/WinRT 上自动协商。"""
        if self._client is not None:
            try:
                return self._client.mtu_size
            except Exception:
                pass
        return 23

    @property
    def payload_size(self) -> int:
        """常规命令的单次写入载荷上限：MTU-3。"""
        return max(self.mtu - 3, DEFAULT_CHUNK)

    # ------------------------------------------------------------ 写入

    async def write_frame(self, frame: bytes, *, atomic: bool = False) -> None:
        """向 AE21 写入完整协议帧。

        atomic=True 用于 2-2 等必须整帧单写的命令，超过载荷上限时
        直接报错而不是拆分，避免设备解析出错误文件名。
        """
        if self._client is None:
            raise RuntimeError("BLE 未连接")
        limit = self.payload_size
        if len(frame) <= limit:
            await self._client.write_gatt_char(
                CHAR_WRITE, frame, response=False)
            return
        if atomic:
            raise RuntimeError(
                f"帧长 {len(frame)}B 超过单写上限 {limit}B，"
                "该命令要求整帧单写，请确认 MTU 协商结果")
        # 普通长帧按 MTU-3 分片顺序写入
        for i in range(0, len(frame), limit):
            await self._client.write_gatt_char(
                CHAR_WRITE, frame[i:i + limit], response=False)
            await asyncio.sleep(0.01)

    # ------------------------------------------------------------ 回调

    def _notify_main(self, _sender, data: bytearray) -> None:
        if self.on_main is not None:
            self.on_main(bytes(data))

    def _notify_key(self, _sender, data: bytearray) -> None:
        if self.on_key is not None:
            self.on_key(bytes(data))

    def _handle_disconnect(self, _client) -> None:
        logger.info("设备已断开")
        self._client = None
        if self.on_disconnect is not None:
            self.on_disconnect()
