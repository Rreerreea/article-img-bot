"""CLI-демо без Telegram — показать другу, что пайплайн живой.

По умолчанию MOCK: API не вызывается, денег не тратит. Прогоняет
статью через весь конвейер и кладёт ZIP. Для реальной генерации —
HF_MODE=REAL и ключи Higgsfield (см. README).

    python run_local.py tests/fixtures/8blocks_excerpt.txt
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from src.config import Config
from src.pipeline import PipelineService, RunResult


async def demo(source: str, cfg: Config) -> RunResult:
    svc = PipelineService(cfg)
    slots, est = svc.prepare(source)
    print(f"Режим: {cfg.mode.value}")
    print(est.human())
    if not slots:
        print("Заданий на картинки не нашлось (маркер «Рис.»).")
        return RunResult(zip_path=None, results=[])
    result = await svc.run(slots)
    print(result.human())
    for r in result.results:
        if r.error:
            print(f"  FAIL {r.slot_id}: {r.error}")
    if result.zip_path:
        print(f"ZIP: {result.zip_path}")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Демо-прогон статья → картинки")
    ap.add_argument("source", help="файл .docx/.md/.txt или ссылка")
    args = ap.parse_args(argv)

    cfg = Config.from_env()
    result = asyncio.run(demo(args.source, cfg))
    return 0 if result.zip_path else 1


if __name__ == "__main__":
    sys.exit(main())
