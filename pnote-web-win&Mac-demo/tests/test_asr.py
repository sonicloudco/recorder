"""asr 模块测试（不依赖 funasr 实际安装）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recorder import asr


class TestAsr(unittest.TestCase):

    def test_language_codes(self):
        self.assertIn("auto", asr.LANGUAGES)
        self.assertIn("zh", asr.LANGUAGES)

    def test_missing_dependency_raises_clear_error(self):
        """未安装 funasr 时应抛出带安装指引的 AsrNotAvailable。"""
        try:
            import funasr  # noqa: F401
            self.skipTest("funasr 已安装，跳过缺依赖分支")
        except ImportError:
            pass
        self.assertFalse(asr.is_loaded())
        with self.assertRaises(asr.AsrNotAvailable) as ctx:
            asr._load_model()
        self.assertIn("requirements-asr.txt", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
