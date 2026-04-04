#!/usr/bin/env python3

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Iterable, List

import requests
from bs4 import BeautifulSoup

from docx_renderer import convert_html_file_to_docx


TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
BLOCK_NAMES = {"p", "blockquote", "li", "h1", "h2"}
META_CLASSES = {"eyebrow", "meta", "chapter-meta"}


class TranslationSplitMismatch(RuntimeError):
    pass


def iter_translatable_blocks(soup: BeautifulSoup) -> Iterable:
    for tag in soup.find_all(True):
        if tag.name in BLOCK_NAMES:
            yield tag
            continue

        if tag.name == "div" and META_CLASSES.intersection(tag.get("class", [])):
            yield tag


def visible_text(tag) -> str:
    return tag.get_text("\n", strip=True)


def replace_text(soup: BeautifulSoup, tag, text: str) -> None:
    tag.clear()
    lines = text.splitlines() or [text]
    for idx, line in enumerate(lines):
        if idx > 0:
            tag.append(soup.new_tag("br"))
        if line:
            tag.append(line)


def manual_translation(tag) -> str | None:
    classes = set(tag.get("class", []))
    text = visible_text(tag)
    parts = [piece.strip() for piece in tag.stripped_strings]

    if "eyebrow" in classes:
        return "Wattpad 导出"

    if tag.name == "h2" and text == "Story Summary":
        return "故事简介"

    if tag.name == "h2" and text == "Contents":
        return "目录"

    if "meta" in classes and parts:
        label = parts[0].rstrip(":")
        value = "\n".join(parts[1:]).strip()
        if label == "Author":
            return f"作者：\n{value}" if value else "作者："
        if label == "Status":
            status_map = {"Completed": "已完结", "Ongoing": "连载中"}
            mapped = status_map.get(value, value)
            return f"状态：\n{mapped}" if mapped else "状态："
        if label == "Chapters":
            return f"章节数：\n{value}" if value else "章节数："
        if label == "Total Estimated Words":
            return f"预计总字数：\n{value}" if value else "预计总字数："

    if "chapter-meta" in classes:
        match = re.search(r"Part ID:\s*(\d+)\s*\|\s*Approx\.\s*([\d,]+)\s*words", text)
        if match:
            return f"章节 ID：{match.group(1)} | 约 {match.group(2)} 词"

    return None


def extract_translated_text(payload: str) -> str:
    data = json.loads(payload)
    return "".join(segment[0] for segment in data[0])


def translate_batch(session: requests.Session, texts: List[str], source_lang: str, target_lang: str) -> List[str]:
    if not texts:
        return []

    joined = texts[0]
    for idx, text in enumerate(texts[1:], start=1):
        joined += f"\n[[[SEP{idx}]]]\n{text}"

    params = {
        "client": "gtx",
        "sl": source_lang,
        "tl": target_lang,
        "dt": "t",
        "q": joined,
    }

    last_error = None
    for attempt in range(5):
        try:
            response = session.get(TRANSLATE_URL, params=params, timeout=60)
            response.raise_for_status()
            translated = extract_translated_text(response.text)
            parts = re.split(r"\n?\[\[\[SEP\d+\]\]\]\n?", translated)
            if len(parts) != len(texts):
                raise TranslationSplitMismatch(
                    f"Translation split mismatch: expected {len(texts)} parts, got {len(parts)}"
                )
            return [part.strip() for part in parts]
        except TranslationSplitMismatch as exc:
            if len(texts) == 1:
                raise RuntimeError(f"Single-text translation split mismatch: {exc}") from exc
            midpoint = len(texts) // 2
            left = translate_batch(session, texts[:midpoint], source_lang, target_lang)
            right = translate_batch(session, texts[midpoint:], source_lang, target_lang)
            return left + right
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(f"Translation failed after retries: {last_error}") from last_error


def batched_translate(
    session: requests.Session,
    texts: List[str],
    source_lang: str,
    target_lang: str,
    max_chars: int,
    max_items: int,
) -> List[str]:
    translated: List[str] = []
    bucket: List[str] = []
    bucket_chars = 0

    def flush() -> None:
        nonlocal bucket, bucket_chars
        if not bucket:
            return
        translated.extend(translate_batch(session, bucket, source_lang, target_lang))
        bucket = []
        bucket_chars = 0

    for text in texts:
        extra = len(text) + 24
        if bucket and (bucket_chars + extra > max_chars or len(bucket) >= max_items):
            flush()
        bucket.append(text)
        bucket_chars += extra

    flush()
    return translated


def translate_html_document(
    input_html: str | Path,
    output_html: str | Path | None = None,
    output_docx: str | Path | None = None,
    source_lang: str = "en",
    target_lang: str = "zh-CN",
    max_chars: int = 3800,
    max_items: int = 24,
    author: str = "hipstateasee",
    progress: bool = True,
) -> dict[str, Path]:
    input_path = Path(input_html).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(input_path)

    default_html = input_path.with_name(f"{input_path.stem}-zh-cn.html")
    default_docx = input_path.with_name(f"{input_path.stem}-zh-cn.docx")
    resolved_output_html = (
        Path(output_html).expanduser().resolve() if output_html else default_html
    )
    resolved_output_docx = (
        Path(output_docx).expanduser().resolve() if output_docx else default_docx
    )

    soup = BeautifulSoup(input_path.read_text(encoding="utf-8"), "html.parser")
    blocks = [tag for tag in iter_translatable_blocks(soup) if visible_text(tag)]
    texts = []
    translatable_tags = []
    for tag in blocks:
        manual = manual_translation(tag)
        if manual is not None:
            replace_text(soup, tag, manual)
            continue
        translatable_tags.append(tag)
        texts.append(visible_text(tag))

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        if progress:
            print(f"Translating {len(texts)} blocks...", file=sys.stderr)
        translated = batched_translate(
            session=session,
            texts=texts,
            source_lang=source_lang,
            target_lang=target_lang,
            max_chars=max_chars,
            max_items=max_items,
        )
    finally:
        session.close()

    for idx, (tag, text) in enumerate(zip(translatable_tags, translated), start=1):
        replace_text(soup, tag, text)
        if progress and idx % 250 == 0:
            print(f"Updated {idx}/{len(translatable_tags)} translated blocks...", file=sys.stderr)

    translated_title = soup.find("h1").get_text(strip=True) if soup.find("h1") else resolved_output_html.stem
    if soup.title and soup.title.string:
        soup.title.string = f"{translated_title} 中文版"

    resolved_output_html.write_text(str(soup), encoding="utf-8")
    convert_html_file_to_docx(
        resolved_output_html,
        resolved_output_docx,
        title=f"{translated_title} 中文版",
        author=author,
    )

    return {
        "html_path": resolved_output_html,
        "docx_path": resolved_output_docx,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Translate exported Wattpad HTML into Chinese DOCX.")
    parser.add_argument("input_html", help="Path to the source HTML file")
    parser.add_argument(
        "--output-html",
        default=None,
        help="Path for translated HTML output",
    )
    parser.add_argument(
        "--output-docx",
        default=None,
        help="Path for translated DOCX output",
    )
    parser.add_argument("--source-lang", default="en")
    parser.add_argument("--target-lang", default="zh-CN")
    parser.add_argument("--max-chars", type=int, default=3800)
    parser.add_argument("--max-items", type=int, default=24)
    args = parser.parse_args()

    result = translate_html_document(
        input_html=args.input_html,
        output_html=args.output_html,
        output_docx=args.output_docx,
        source_lang=args.source_lang,
        target_lang=args.target_lang,
        max_chars=args.max_chars,
        max_items=args.max_items,
    )

    print(f"HTML: {result['html_path']}")
    print(f"DOCX: {result['docx_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
