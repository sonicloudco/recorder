"""设备层（Recorder）异步逻辑测试。

用 FakeTransport 模拟 CB08 应答，端到端验证：
    - 请求/应答匹配（电量）
    - 多帧文件列表组装 + CMD=18 收尾 / 无 18 空闲收尾
    - 下载会话：2-3 → 2-4×N → 2-5 code0 写盘
    - 候选名回退：.wav 返回 code=1 后自动改试 .opus
    - 删除应答与旧固件无应答兼容
"""
import asyncio
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recorder import protocol as P
from recorder.device import Recorder


class FakeTransport:
    """替代 BleTransport：把上行帧交给 handler，handler 返回应答帧列表。"""

    def __init__(self, recorder: Recorder, handler) -> None:
        self._recorder = recorder
        self._handler = handler
        self._seq = 0
        self.is_connected = True
        self.atomic_frames = []  # 记录被要求整帧单写的帧

    async def write_frame(self, frame: bytes, *, atomic: bool = False):
        if atomic:
            self.atomic_frames.append(frame)
        data = frame[6:]
        type_, cmd, body = data[0], data[1], data[2:]
        for resp_data in self._handler(type_, cmd, body) or []:
            self._seq = (self._seq + 1) & 0xFF
            resp = P.build_frame(self._seq, resp_data)
            # 模拟 BLE 通知分片：每 20B 一段
            for i in range(0, len(resp), 20):
                self._recorder._feed_main(resp[i:i + 20])

    async def disconnect(self):
        self.is_connected = False


def make_recorder(handler, tmpdir) -> Recorder:
    rec = Recorder(output_dir=Path(tmpdir))
    rec.transport = FakeTransport(rec, handler)
    rec._loop = asyncio.get_event_loop()
    return rec


def entry_bytes(duration, size, name):
    return struct.pack(">II", duration, size) + \
        name.encode().ljust(20, b"\x00")


class TestRecorder(unittest.IsolatedAsyncioTestCase):

    async def test_battery_request_response(self):
        def handler(type_, cmd, body):
            if (type_, cmd) == (P.TYPE_CONTROL, P.CTRL_GET_BATTERY):
                return [bytes([P.TYPE_CONTROL, P.CTRL_BATTERY_RESP, 85])]
        with tempfile.TemporaryDirectory() as tmp:
            rec = make_recorder(handler, tmp)
            rec._loop = asyncio.get_running_loop()
            self.assertEqual(await rec.get_battery(), 85)

    async def test_file_list_multi_frame_with_done(self):
        def handler(type_, cmd, body):
            if (type_, cmd) == (P.TYPE_FILE, P.FILE_LIST_REQ):
                f1 = bytes([P.TYPE_FILE, P.FILE_LIST_DATA]) + \
                    struct.pack(">I", 1) + \
                    entry_bytes(75, 38444, "note20260710-162938.")
                f2 = bytes([P.TYPE_FILE, P.FILE_LIST_DATA]) + \
                    struct.pack(">I", 1) + \
                    entry_bytes(10, 5000, "note20260711-090000.")
                done = bytes([P.TYPE_FILE, P.FILE_LIST_DONE, 0])
                return [f1, f2, done]
        with tempfile.TemporaryDirectory() as tmp:
            rec = make_recorder(handler, tmp)
            rec._loop = asyncio.get_running_loop()
            files = await rec.get_file_list(timeout=3)
            self.assertEqual([f.name for f in files],
                             ["note20260710-162938.", "note20260711-090000."])

    async def test_file_list_idle_fallback_without_done(self):
        """旧固件不发 CMD=18：空闲 1.2s 后返回已累积列表。"""
        def handler(type_, cmd, body):
            if (type_, cmd) == (P.TYPE_FILE, P.FILE_LIST_REQ):
                return [bytes([P.TYPE_FILE, P.FILE_LIST_DATA]) +
                        struct.pack(">I", 1) +
                        entry_bytes(5, 100, "a.")]
        with tempfile.TemporaryDirectory() as tmp:
            rec = make_recorder(handler, tmp)
            rec._loop = asyncio.get_running_loop()
            files = await rec.get_file_list(timeout=5)
            self.assertEqual(len(files), 1)

    async def test_download_success_and_atomic_write(self):
        payload = b"RIFF" + struct.pack("<I", 36) + b"WAVE" + b"x" * 32
        def handler(type_, cmd, body):
            if (type_, cmd) == (P.TYPE_FILE, P.FILE_IMPORT_REQ):
                name = body[4:].split(b"\x00", 1)[0].decode()
                start = bytes([P.TYPE_FILE, P.FILE_IMPORT_START]) + \
                    name.encode()
                chunks = [bytes([P.TYPE_FILE, P.FILE_DATA]) + payload[i:i+16]
                          for i in range(0, len(payload), 16)]
                end = bytes([P.TYPE_FILE, P.FILE_IMPORT_END,
                             P.IMPORT_END_OK])
                return [start] + chunks + [end]
        with tempfile.TemporaryDirectory() as tmp:
            rec = make_recorder(handler, tmp)
            rec._loop = asyncio.get_running_loop()
            entry = P.FileEntry(duration=1, size=len(payload),
                                name="note20260710-162938.")
            result = await rec.download(entry)
            self.assertEqual(result.filename, "note20260710-162938.wav")
            self.assertEqual(result.data, payload)
            self.assertTrue(result.is_wav)
            self.assertTrue(result.path.exists())
            self.assertEqual(result.path.read_bytes(), payload)
            # 2-2 请求帧必须走整帧单写路径且为 36B
            self.assertEqual(len(rec.transport.atomic_frames), 1)
            self.assertEqual(len(rec.transport.atomic_frames[0]), 36)

    async def test_download_candidate_fallback(self):
        """.wav 返回 code=1（不存在）后自动改试 .opus。"""
        requested = []
        payload = b"OPUSDATA" * 4
        def handler(type_, cmd, body):
            if (type_, cmd) == (P.TYPE_FILE, P.FILE_IMPORT_REQ):
                name = body[4:].split(b"\x00", 1)[0].decode()
                requested.append(name)
                if name.endswith(".wav"):
                    return [bytes([P.TYPE_FILE, P.FILE_IMPORT_END,
                                   P.IMPORT_END_NOT_FOUND])]
                start = bytes([P.TYPE_FILE, P.FILE_IMPORT_START]) + \
                    name.encode()
                data = bytes([P.TYPE_FILE, P.FILE_DATA]) + payload
                end = bytes([P.TYPE_FILE, P.FILE_IMPORT_END,
                             P.IMPORT_END_OK])
                return [start, data, end]
        with tempfile.TemporaryDirectory() as tmp:
            rec = make_recorder(handler, tmp)
            rec._loop = asyncio.get_running_loop()
            entry = P.FileEntry(duration=1, size=32, name="a.")
            result = await rec.download(entry)
            self.assertEqual(requested, ["a.wav", "a.opus"])
            self.assertEqual(result.data, payload)

    async def test_download_resume_no_fallback(self):
        """offset>0 续传时 code=1 不应换候选名，直接报错。"""
        requested = []
        def handler(type_, cmd, body):
            if (type_, cmd) == (P.TYPE_FILE, P.FILE_IMPORT_REQ):
                requested.append(body[4:].split(b"\x00", 1)[0].decode())
                return [bytes([P.TYPE_FILE, P.FILE_IMPORT_END,
                               P.IMPORT_END_NOT_FOUND])]
        with tempfile.TemporaryDirectory() as tmp:
            rec = make_recorder(handler, tmp)
            rec._loop = asyncio.get_running_loop()
            entry = P.FileEntry(duration=1, size=32, name="a.")
            from recorder.device import FileNotFoundOnDevice
            with self.assertRaises(FileNotFoundOnDevice):
                await rec.download(entry, offset=1024)
            self.assertEqual(requested, ["a.wav"])  # 只试一个名字

    async def test_segment_download_frame(self):
        captured = {}
        def handler(type_, cmd, body):
            if (type_, cmd) == (P.TYPE_FILE, P.FILE_IMPORT_SEG):
                captured["start"], captured["end"] = \
                    struct.unpack_from("<II", body, 0)
                return [bytes([P.TYPE_FILE, P.FILE_IMPORT_START]) + b"a.wav",
                        bytes([P.TYPE_FILE, P.FILE_DATA]) + b"seg",
                        bytes([P.TYPE_FILE, P.FILE_IMPORT_END,
                               P.IMPORT_END_OK])]
        with tempfile.TemporaryDirectory() as tmp:
            rec = make_recorder(handler, tmp)
            rec._loop = asyncio.get_running_loop()
            entry = P.FileEntry(duration=1, size=3, name="a.")
            result = await rec.download_segment(entry, 100, 200)
            self.assertEqual((captured["start"], captured["end"]), (100, 200))
            self.assertEqual(result.data, b"seg")

    async def test_delete_with_and_without_response(self):
        def handler(type_, cmd, body):
            if (type_, cmd) == (P.TYPE_FILE, P.FILE_DELETE_ONE):
                self.assertEqual(len(body), 28)  # 28B 原始条目
                return [bytes([P.TYPE_FILE, P.FILE_DELETE_ONE_RESP, 0])]
            if (type_, cmd) == (P.TYPE_FILE, P.FILE_DELETE_ALL):
                return []  # 旧固件不回应答
        with tempfile.TemporaryDirectory() as tmp:
            rec = make_recorder(handler, tmp)
            rec._loop = asyncio.get_running_loop()
            entry = P.FileEntry(duration=1, size=1, name="a.",
                                raw=b"\x00" * 28)
            self.assertEqual(await rec.delete_file(entry), 0)
            self.assertIsNone(await rec.delete_all())  # 超时按已发送处理


if __name__ == "__main__":
    unittest.main(verbosity=2)
