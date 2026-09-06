"""CRC-16/XMODEM 实现。

参数（见协议 3.1 节）：
    Polynomial = 0x1021, Init = 0x0000,
    RefIn/RefOut = false/false, XorOut = 0x0000
    检验向量: ASCII "123456789" -> 0x31C3
"""

# 预生成 256 项查表，加速逐字节计算
_TABLE = []
for _byte in range(256):
    _crc = _byte << 8
    for _ in range(8):
        if _crc & 0x8000:
            _crc = ((_crc << 1) ^ 0x1021) & 0xFFFF
        else:
            _crc = (_crc << 1) & 0xFFFF
    _TABLE.append(_crc)


def crc16_xmodem(data: bytes, crc: int = 0x0000) -> int:
    """计算 CRC-16/XMODEM。

    协议约定：输入为帧头中的 LEN 两个原始字节 + DATA，
    不包含 MAGIC、SEQ 和 CRC 字段本身。
    """
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ _TABLE[((crc >> 8) ^ b) & 0xFF]
    return crc
