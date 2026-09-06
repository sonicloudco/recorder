"""协议层单元测试。

关键验证点：
    - CRC-16/XMODEM 标准检验向量 "123456789" -> 0x31C3
    - 协议 7.3 节真机成功下载帧的逐字节复现
    - 帧头 LEN/CRC 小端、文件列表大端
    - 流式解析器半帧 / 多帧 / 噪声 / CRC 错误处理
"""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recorder import protocol as P
from recorder.crc16 import crc16_xmodem
from recorder.protocol import CrcError, FileEntry, FrameParser

# 7.3 节 2026-07-10 真机成功下载 note20260710-162938.wav 的 TX 帧
REAL_FRAME = bytes.fromhex(
    "5a039e201e000202000000006e6f7465"
    "32303236303731302d3136323933382e"
    "77617600")


class TestCrc16(unittest.TestCase):
    def test_check_vector(self):
        self.assertEqual(crc16_xmodem(b"123456789"), 0x31C3)

    def test_empty(self):
        self.assertEqual(crc16_xmodem(b""), 0x0000)


class TestFrameBuild(unittest.TestCase):
    def test_real_import_request_frame(self):
        """复现 7.3 节真机 36B 下载请求帧。"""
        frame = P.build_import_request(
            seq=3, filename="note20260710-162938.wav", offset=0)
        self.assertEqual(len(frame), 36)
        self.assertEqual(frame, REAL_FRAME)

    def test_header_little_endian(self):
        frame = P.build_command(0, P.TYPE_CONTROL, P.CTRL_GET_BATTERY)
        self.assertEqual(frame[0], 0x5A)
        (length,) = struct.unpack_from("<H", frame, 4)
        self.assertEqual(length, 2)  # DATA=[TYPE][CMD]
        (crc,) = struct.unpack_from("<H", frame, 2)
        self.assertEqual(crc, crc16_xmodem(frame[4:6] + frame[6:]))

    def test_filename_padding(self):
        raw = P.encode_filename_24("a.wav")
        self.assertEqual(len(raw), 24)
        self.assertEqual(raw[:5], b"a.wav")
        self.assertEqual(raw[5:], b"\x00" * 19)

    def test_sync_time_params(self):
        params = P.encode_sync_time(2026, 7, 28, 12, 34, 56)
        self.assertEqual(len(params), 7)
        self.assertEqual(struct.unpack_from("<H", params)[0], 2026)
        self.assertEqual(list(params[2:]), [7, 28, 12, 34, 56])

    def test_segment_request(self):
        frame = P.build_segment_request(0, "a.wav", start=100, end=200)
        data = frame[6:]
        self.assertEqual(data[0], P.TYPE_FILE)
        self.assertEqual(data[1], P.FILE_IMPORT_SEG)
        self.assertEqual(struct.unpack_from("<II", data, 2), (100, 200))


class TestFrameParser(unittest.TestCase):
    def test_parse_real_frame(self):
        parser = FrameParser("t")
        frames = list(parser.feed(REAL_FRAME))
        self.assertEqual(len(frames), 1)
        f = frames[0]
        self.assertEqual(f.seq, 3)
        self.assertEqual(f.type, P.TYPE_FILE)
        self.assertEqual(f.cmd, P.FILE_IMPORT_REQ)
        self.assertEqual(f.body[:4], b"\x00\x00\x00\x00")

    def test_split_across_notifies(self):
        """一个通知含半帧：20B + 16B 跨通知重组。"""
        parser = FrameParser("t")
        self.assertEqual(list(parser.feed(REAL_FRAME[:20])), [])
        frames = list(parser.feed(REAL_FRAME[20:]))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].cmd, P.FILE_IMPORT_REQ)

    def test_multiple_frames_one_notify(self):
        parser = FrameParser("t")
        frames = list(parser.feed(REAL_FRAME + REAL_FRAME))
        self.assertEqual(len(frames), 2)

    def test_leading_noise_skipped(self):
        parser = FrameParser("t")
        frames = list(parser.feed(b"\x00\xff" + REAL_FRAME))
        self.assertEqual(len(frames), 1)

    def test_crc_error_raises_and_resyncs(self):
        parser = FrameParser("t")
        bad = bytearray(REAL_FRAME)
        bad[10] ^= 0xFF  # 破坏 DATA
        with self.assertRaises(CrcError):
            list(parser.feed(bytes(bad)))
        self.assertEqual(parser.crc_errors, 1)
        # 损坏帧丢弃后，后续好帧应能继续解析
        frames = list(parser.feed(REAL_FRAME))
        self.assertEqual([f.cmd for f in frames], [P.FILE_IMPORT_REQ])

    def test_ack_frame(self):
        # DATA 只有一个 TYPE 字节按 ACK 处理
        data = bytes([P.TYPE_FILE])
        frame = P.build_frame(9, data)
        parser = FrameParser("t")
        frames = list(parser.feed(frame))
        self.assertTrue(frames[0].is_ack)
        self.assertIsNone(frames[0].cmd)

    def test_bogus_len_resync(self):
        """LEN 超过 8192 视为假帧头，跳字节重同步（参考厂家页面）。"""
        parser = FrameParser("t")
        bogus = bytes([0x5A, 0x00, 0x00, 0x00, 0xFF, 0xFF])  # LEN=0xFFFF
        frames = list(parser.feed(bogus + REAL_FRAME))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].cmd, P.FILE_IMPORT_REQ)


class TestDecoders(unittest.TestCase):
    def _entry_bytes(self, duration, size, name):
        return struct.pack(">II", duration, size) + \
            name.encode().ljust(20, b"\x00")

    def test_file_list_big_endian(self):
        body = struct.pack(">I", 2) + \
            self._entry_bytes(75, 38444, "note20260710-162938.") + \
            self._entry_bytes(3600, 1000000, "note20260101-000000.")
        entries = P.decode_file_list(body)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].duration, 75)
        self.assertEqual(entries[0].size, 38444)
        self.assertEqual(entries[0].name, "note20260710-162938.")
        self.assertEqual(len(entries[0].raw), 28)

    def test_candidate_names_rebuild_extension(self):
        entry = FileEntry(duration=1, size=1, name="note20260710-162938.")
        self.assertEqual(entry.candidate_names(), [
            "note20260710-162938.wav",
            "note20260710-162938.opus",
            "note20260710-162938.",
        ])

    def test_estimated_wav_size(self):
        # 协议 7.4：1.2 秒 16kHz/16bit/mono ≈ 时长×32000+44
        entry = FileEntry(duration=1, size=999, name="a.")
        self.assertEqual(entry.estimated_wav_size, 32044)

    def test_capacity_little_endian(self):
        remain, total = P.decode_capacity(struct.pack("<II", 1024, 4096))
        self.assertEqual((remain, total), (1024, 4096))

    def test_record_time(self):
        duration, size = P.decode_record_time(struct.pack("<HI", 65, 130000))
        self.assertEqual((duration, size), (65, 130000))

    def test_is_wav(self):
        header = b"RIFF" + struct.pack("<I", 38436) + b"WAVE" + b"\x00" * 32
        self.assertTrue(P.is_wav(header))
        self.assertFalse(P.is_wav(b"OggS" + b"\x00" * 40))

    def test_inspect_wav(self):
        """7.4 节验收：声明长度、采样率、位深、声道。"""
        data_len = 100
        header = bytearray(44 + data_len)
        header[0:4] = b"RIFF"
        struct.pack_into("<I", header, 4, 36 + data_len)  # 总长-8
        header[8:12] = b"WAVE"
        struct.pack_into("<H", header, 22, 1)       # mono
        struct.pack_into("<I", header, 24, 16000)   # 16kHz
        struct.pack_into("<H", header, 34, 16)      # 16bit
        info = P.inspect_wav(bytes(header))
        self.assertTrue(info.ok)
        self.assertEqual(info.declared, 144)
        self.assertEqual((info.sample_rate, info.bits_per_sample,
                          info.channels), (16000, 16, 1))
        # 声明长度不一致时 ok=False
        info2 = P.inspect_wav(bytes(header) + b"x")
        self.assertFalse(info2.ok)

    def test_epoch_timestamp_heuristic(self):
        """7.1 节：个别固件 time 字段为绝对时间戳。"""
        self.assertFalse(P.is_epoch_timestamp(75))          # 普通时长
        self.assertFalse(P.is_epoch_timestamp(3600 * 24))   # 一天时长
        self.assertTrue(P.is_epoch_timestamp(1783412978))   # 2026 年时间戳


if __name__ == "__main__":
    unittest.main(verbosity=2)
