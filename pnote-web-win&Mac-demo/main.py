"""CB08 录音笔处理程序入口。

用法：
    python main.py            # 启动命令行交互
    python main.py -o out     # 指定下载目录
    python main.py -v         # 输出调试日志
    python main.py --web      # 启动 Web 界面（需 requirements-web.txt）
    python main.py --web --port 8000
"""
import argparse
import logging
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="CB08 录音笔处理程序")
    parser.add_argument("-o", "--output", default="downloads",
                        help="下载文件保存目录（默认 downloads）")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="输出调试日志")
    parser.add_argument("--web", action="store_true",
                        help="启动 Web 界面而非命令行 REPL")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Web 监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8000,
                        help="Web 监听端口（默认 8000）")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.web:
        from recorder.web import run_server
        run_server(Path(args.output), host=args.host, port=args.port)
    else:
        import asyncio
        from recorder.cli import Cli
        try:
            asyncio.run(Cli(Path(args.output)).run())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
