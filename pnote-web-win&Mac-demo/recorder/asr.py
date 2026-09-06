"""本地离线语音转文字（FunASR SenseVoiceSmall）。

可选功能，仅 transcribe 命令依赖，需先安装：
    pip install -r requirements-asr.txt
首次运行会从 ModelScope 自动下载模型（约 900MB），之后完全离线可用。

下载得到的 WAV 为 16000Hz/16bit/单声道，是 SenseVoice 的标准输入，
无需重采样即可直接识别。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 模型单例：首次加载耗时较长（含 VAD 模型），之后复用
_model = None

# SenseVoice 支持的语言代码
LANGUAGES = ("auto", "zh", "en", "yue", "ja", "ko")


class AsrNotAvailable(RuntimeError):
    """funasr 未安装或模型加载失败。"""


def is_loaded() -> bool:
    return _model is not None


def _load_model():
    global _model
    if _model is not None:
        return _model
    try:
        from funasr import AutoModel
    except ImportError as exc:
        raise AsrNotAvailable(
            "未安装 funasr，转文字功能不可用；请先执行 "
            "pip install -r requirements-asr.txt") from exc
    logger.info("加载 SenseVoiceSmall 模型（首次运行会自动下载）...")
    # VAD 切分长音频，单段最长 30s，任意时长录音均可识别
    _model = AutoModel(
        model="iic/SenseVoiceSmall",
        vad_model="fsmn-vad",
        vad_kwargs={"max_single_segment_time": 30000},
        device="cpu",
        disable_update=True,
    )
    return _model


def transcribe_file_sync(path: Path, language: str = "auto") -> str:
    """同步识别单个音频文件，返回整理后的文本。"""
    model = _load_model()
    result = model.generate(
        input=str(path),
        cache={},
        language=language,
        use_itn=True,        # 数字/标点正规化
        batch_size_s=60,
        merge_vad=True,
        merge_length_s=15,
    )
    if not result:
        return ""
    # 去除 <|zh|><|NEUTRAL|> 等情绪/事件标记
    from funasr.utils.postprocess_utils import rich_transcription_postprocess
    return rich_transcription_postprocess(result[0]["text"]).strip()


async def transcribe_file(path: Path, language: str = "auto") -> str:
    """异步入口：识别在线程池执行，不阻塞 BLE 事件循环。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, transcribe_file_sync, Path(path), language)
