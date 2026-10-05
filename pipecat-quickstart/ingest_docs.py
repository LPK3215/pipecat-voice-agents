"""把文档灌入本地知识库（RAG 的「写入侧」）。

为什么写入与查询要分开（与 HANDBOOK-02 的原则一致）：
    工具里的检索必须是**毫秒级同步**返回。如果检索时现去解析文件、现算嵌入，
    用户就要多等几秒 —— 语音场景直接崩。所以解析/切块/算向量都在这里离线做好、
    落库；运行时工具只查库。

用法：
    cd server
    uv run ../ingest_docs.py ../README.md                     # 单个文件
    uv run ../ingest_docs.py --dir ./docs                     # 目录（递归，含 .md/.txt）
    uv run ../ingest_docs.py ../README.md --source 使用手册    # 指定 source 名
    uv run ../ingest_docs.py --list                           # 看已入库文档（不加载模型）
"""

import argparse
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "server"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BASE / "server" / ".env", override=True)

import knowledge  # noqa: E402

SUFFIXES = {".md", ".txt", ".markdown"}


def collect(paths: list[Path], directory: Path | None) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files += [f for f in sorted(p.rglob("*")) if f.suffix.lower() in SUFFIXES]
        elif p.is_file():
            files.append(p)
        else:
            print(f"  [跳过] 不存在: {p}")
    if directory:
        files += [f for f in sorted(directory.rglob("*")) if f.suffix.lower() in SUFFIXES]
    # 去重且保持顺序
    seen, out = set(), []
    for f in files:
        r = f.resolve()
        if r not in seen:
            seen.add(r)
            out.append(f)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="把文档灌入本地知识库")
    ap.add_argument("paths", nargs="*", type=Path, help="要入库的文件或目录")
    ap.add_argument("--dir", type=Path, default=None, help="目录（递归收录 .md/.txt）")
    ap.add_argument("--source", default=None, help="自定义 source 名（仅单文件时有效）")
    ap.add_argument("--list", action="store_true", help="列出已入库文档后退出")
    args = ap.parse_args()

    knowledge.init_db()

    if args.list or (not args.paths and not args.dir):
        st = knowledge.stats()
        print(f"知识库：{st['documents']} 篇 / {st['chunks']} 块")
        for d in knowledge.list_documents():
            print(f"  - {d['source']}（{d['chunks']} 块）{d['title']}")
        return 0

    files = collect(args.paths, args.dir)
    if not files:
        print("没有找到可入库的文件（支持 .md/.txt）")
        return 1

    print("=" * 70)
    print(f"入库 {len(files)} 个文件（首次会加载嵌入模型，稍等）")
    print("=" * 70)
    total = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ 读取失败 {f}: {exc}")
            continue
        source = args.source if (args.source and len(files) == 1) else str(f)
        try:
            n = knowledge.ingest(text, source=source, title=f.stem)
            total += n
            print(f"  ✅ {source} → {n} 块")
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ 入库失败 {f}: {type(exc).__name__}: {exc}")

    st = knowledge.stats()
    print("-" * 70)
    print(f"完成：本次 {total} 块；累计 {st['documents']} 篇 / {st['chunks']} 块")
    return 0


if __name__ == "__main__":
    sys.exit(main())
