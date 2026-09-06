"""Web 层（FastAPI）接口测试。

复用 test_device 的 FakeTransport 思路模拟设备，
用 TestClient 验证 REST 接口与状态流转（无需真机/蓝牙）。
fastapi/httpx 未安装时整组跳过。
"""
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from fastapi.testclient import TestClient
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

from recorder import protocol as P


class FakeTransport:
    """与 test_device.FakeTransport 一致：handler 返回应答帧列表。"""

    def __init__(self, recorder, handler) -> None:
        self._recorder = recorder
        self._handler = handler
        self._seq = 0
        self.is_connected = True
        self.mtu = 247
        self.payload_size = 244

    async def write_frame(self, frame: bytes, *, atomic: bool = False):
        data = frame[6:]
        type_, cmd, body = data[0], data[1], data[2:]
        for resp_data in self._handler(type_, cmd, body) or []:
            self._seq = (self._seq + 1) & 0xFF
            resp = P.build_frame(self._seq, resp_data)
            for i in range(0, len(resp), 20):
                self._recorder._feed_main(resp[i:i + 20])

    async def disconnect(self):
        self.is_connected = False


def handler(type_, cmd, body):
    if (type_, cmd) == (P.TYPE_CONTROL, P.CTRL_GET_BATTERY):
        return [bytes([P.TYPE_CONTROL, P.CTRL_BATTERY_RESP, 62])]
    if (type_, cmd) == (P.TYPE_CONTROL, P.CTRL_GET_CAPACITY):
        return [bytes([P.TYPE_CONTROL, P.CTRL_CAPACITY_RESP])
                + struct.pack("<II", 1000, 2000)]
    if (type_, cmd) == (P.TYPE_CONTROL, P.CTRL_GET_VERSION):
        return [bytes([P.TYPE_CONTROL, P.CTRL_VERSION_RESP]) + b"V1.0.0"]
    if (type_, cmd) == (P.TYPE_FILE, P.FILE_LIST_REQ):
        entry = struct.pack(">II", 9, 18620) + \
            b"call20260728-211836.".ljust(20, b"\x00")
        return [bytes([P.TYPE_FILE, P.FILE_LIST_DATA])
                + struct.pack(">I", 1) + entry,
                bytes([P.TYPE_FILE, P.FILE_LIST_DONE, 0])]
    if (type_, cmd) == (P.TYPE_FILE, P.FILE_IMPORT_REQ):
        name = body[4:].split(b"\x00", 1)[0]
        payload = b"RIFF" + struct.pack("<I", 36) + b"WAVE" + b"x" * 32
        return [bytes([P.TYPE_FILE, P.FILE_IMPORT_START]) + name,
                bytes([P.TYPE_FILE, P.FILE_DATA]) + payload,
                bytes([P.TYPE_FILE, P.FILE_IMPORT_END, P.IMPORT_END_OK])]


@unittest.skipUnless(HAS_FASTAPI, "未安装 fastapi/httpx")
class TestWebApi(unittest.TestCase):

    def setUp(self):
        from recorder import web
        self._tmp = tempfile.TemporaryDirectory()
        self.app = web.create_app(Path(self._tmp.name))
        self.client = TestClient(self.app)

    def tearDown(self):
        self._tmp.cleanup()

    def test_status_initially_disconnected(self):
        r = self.client.get("/api/status")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertFalse(data["connected"])
        self.assertEqual(data["files"], [])

    def test_command_requires_connection(self):
        r = self.client.get("/api/info")
        self.assertEqual(r.status_code, 400)
        self.assertIn("尚未连接", r.json()["error"])

    def test_transcribe_rejects_missing_local_file(self):
        r = self.client.post("/api/transcribe",
                             json={"name": "nope.wav", "language": "auto"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("不存在", r.json()["error"])

    def test_transcribe_rejects_path_traversal(self):
        r = self.client.post(
            "/api/transcribe",
            json={"name": "../协议.md", "language": "auto"})
        self.assertEqual(r.status_code, 400)

    def test_raw_rejects_bad_params(self):
        r = self.client.post("/api/raw",
                             json={"type": "x", "cmd": 3, "params": ""})
        self.assertEqual(r.status_code, 400)

    def test_local_list_and_static_download(self):
        wav = Path(self._tmp.name) / "a.wav"
        wav.write_bytes(b"RIFF" + struct.pack("<I", 4) + b"WAVE")
        r = self.client.get("/api/local")
        names = [f["name"] for f in r.json()]
        self.assertIn("a.wav", names)
        r2 = self.client.get("/downloads/a.wav")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.content, wav.read_bytes())

    def test_index_served(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("QS668", r.text)


@unittest.skipUnless(HAS_FASTAPI, "未安装 fastapi/httpx")
class TestWebWithFakeDevice(unittest.IsolatedAsyncioTestCase):
    """直连 Recorder + FakeTransport，验证 web 层用到的设备调用链。"""

    async def test_battery_and_files_roundtrip(self):
        import asyncio
        from recorder.device import Recorder
        with tempfile.TemporaryDirectory() as tmp:
            rec = Recorder(output_dir=Path(tmp))
            rec.transport = FakeTransport(rec, handler)
            rec._loop = asyncio.get_running_loop()
            self.assertEqual(await rec.get_battery(), 62)
            files = await rec.get_file_list(timeout=3)
            self.assertEqual(len(files), 1)
            result = await rec.download(files[0])
            self.assertTrue(result.is_wav)
            self.assertTrue(result.path.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
