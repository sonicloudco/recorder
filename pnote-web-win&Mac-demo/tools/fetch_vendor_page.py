"""临时脚本：抓取厂家测试页面源码用于研究。"""
import re
import urllib.request
from pathlib import Path

BASE = "https://nextproto.top/qs668/"
out = Path("docs/vendor_page")
out.mkdir(parents=True, exist_ok=True)

html = urllib.request.urlopen(BASE, timeout=20).read().decode("utf-8", "replace")
(out / "index.html").write_text(html, encoding="utf-8")
print("html bytes:", len(html))

scripts = re.findall(r"<script[^>]*\bsrc=[\"']([^\"']+)", html)
links = re.findall(r"<link[^>]*\bhref=[\"']([^\"']+)", html)
print("external scripts:", scripts)
print("links:", links)

inline = re.findall(r"<script(?![^>]*\bsrc)[^>]*>(.*?)</script>", html, re.S)
print("inline script blocks:", [len(s) for s in inline])

for i, s in enumerate(inline):
    (out / f"inline_{i}.js").write_text(s, encoding="utf-8")

for src in scripts:
    url = src if src.startswith("http") else BASE + src.lstrip("./")
    try:
        js = urllib.request.urlopen(url, timeout=20).read().decode("utf-8", "replace")
        name = src.split("/")[-1].split("?")[0] or "script.js"
        (out / name).write_text(js, encoding="utf-8")
        print(f"saved {name}: {len(js)} bytes")
    except Exception as exc:
        print(f"fetch {url} failed: {exc}")
