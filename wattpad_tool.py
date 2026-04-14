#!/usr/bin/env python3

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from translate_wattpad_html import translate_html_document
from wattpad_cookies import load_wattpad_cookies
from wattpad_export import USER_AGENT, export_story_assets, fetch_logged_in_user, fetch_story, slugify


SEARCH_ENDPOINT = "https://www.wattpad.com/v4/search/stories"
SEARCH_FIELDS = (
    "stories("
    "id,title,voteCount,readCount,commentCount,description,completed,mature,cover,url,"
    "isPaywalled,paidModel,length,language(id),user(name),numParts,lastPublishedPart(createDate),tags"
    "),total,nextUrl"
)


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def normalize_story(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": raw.get("id"),
        "title": raw.get("title", ""),
        "author": (raw.get("user") or {}).get("name", ""),
        "description": raw.get("description", ""),
        "completed": bool(raw.get("completed")),
        "numParts": raw.get("numParts", 0),
        "voteCount": raw.get("voteCount", 0) or 0,
        "readCount": raw.get("readCount", 0) or 0,
        "commentCount": raw.get("commentCount", 0) or 0,
        "mature": bool(raw.get("mature")),
        "url": raw.get("url", ""),
        "tags": raw.get("tags", []) or [],
        "isPaywalled": bool(raw.get("isPaywalled")),
        "paidModel": raw.get("paidModel"),
        "length": raw.get("length", 0) or 0,
        "lastPublishedPart": ((raw.get("lastPublishedPart") or {}).get("createDate")),
    }


def popularity_key(story: Dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        int(story.get("readCount") or 0),
        int(story.get("voteCount") or 0),
        int(story.get("commentCount") or 0),
        int(story.get("numParts") or 0),
    )


def search_stories(
    session: requests.Session,
    keyword: str,
    max_results: int,
    page_size: int,
    include_mature: bool,
    include_paywalled: bool,
) -> Dict[str, Any]:
    params = {
        "query": keyword,
        "fields": SEARCH_FIELDS,
        "limit": page_size,
        "mature": str(include_mature).lower(),
        "offset": 0,
    }

    next_url = SEARCH_ENDPOINT
    first_request = True
    seen: dict[str, Dict[str, Any]] = {}
    total = None

    while next_url:
        if first_request:
            response = session.get(next_url, params=params, timeout=30)
            first_request = False
        else:
            response = session.get(next_url, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if total is None:
            total = int(payload.get("total") or 0)

        for raw in payload.get("stories", []):
            story = normalize_story(raw)
            story_id = str(story["id"])
            if not story_id or story_id in seen:
                continue
            if not include_paywalled and story.get("isPaywalled"):
                continue
            seen[story_id] = story

        if len(seen) >= max_results:
            break
        next_url = payload.get("nextUrl")

    stories = sorted(seen.values(), key=popularity_key, reverse=True)[:max_results]
    return {
        "keyword": keyword,
        "total": total if total is not None else len(stories),
        "returned": len(stories),
        "stories": stories,
    }


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, stories: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "rank",
        "title",
        "author",
        "readCount",
        "voteCount",
        "commentCount",
        "numParts",
        "completed",
        "mature",
        "isPaywalled",
        "paidModel",
        "lastPublishedPart",
        "url",
        "tags",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for idx, story in enumerate(stories, start=1):
            row = {
                "rank": idx,
                "title": story.get("title", ""),
                "author": story.get("author", ""),
                "readCount": story.get("readCount", 0),
                "voteCount": story.get("voteCount", 0),
                "commentCount": story.get("commentCount", 0),
                "numParts": story.get("numParts", 0),
                "completed": story.get("completed", False),
                "mature": story.get("mature", False),
                "isPaywalled": story.get("isPaywalled", False),
                "paidModel": story.get("paidModel"),
                "lastPublishedPart": story.get("lastPublishedPart"),
                "url": story.get("url", ""),
                "tags": ", ".join(story.get("tags", [])),
            }
            writer.writerow(row)


def trim(value: str, max_len: int) -> str:
    if len(value) <= max_len:
        return value
    return value[: max_len - 1] + "…"


def print_search_results(payload: Dict[str, Any]) -> None:
    stories = payload["stories"]
    if not stories:
        print("No stories found.")
        return

    print(
        f"Keyword: {payload['keyword']} | total matches: {payload['total']} | returned: {payload['returned']}"
    )
    print(
        f"{'#':>2}  {'Reads':>10}  {'Votes':>8}  {'Parts':>5}  {'Type':<5}  {'Author':<20}  Title"
    )
    for idx, story in enumerate(stories, start=1):
        story_type = "paid" if story["isPaywalled"] else "free"
        print(
            f"{idx:>2}  "
            f"{story['readCount']:>10}  "
            f"{story['voteCount']:>8}  "
            f"{story['numParts']:>5}  "
            f"{story_type:<5}  "
            f"{trim(story['author'], 20):<20}  "
            f"{trim(story['title'], 70)}"
        )
        print(f"    {story['url']}")


def safe_story_basename(story: Dict[str, Any], custom: str | None = None) -> str:
    if custom:
        return slugify(custom)
    return slugify(story.get("title", "")) or "story"


def export_authorized_story(
    session: requests.Session,
    story_url: str,
    output_dir: Path,
    basename: str | None,
    translate_to_chinese: bool,
    cookies_path: Optional[Path] = None,
) -> Dict[str, Path]:
    if cookies_path is not None:
        load_wattpad_cookies(session, cookies_path)

    story = fetch_story(session, story_url)
    if story.get("isPaywalled"):
        if cookies_path is None:
            raise RuntimeError(
                "Paywalled stories require a browser cookie file from an account that owns this story. "
                "Export cookies while logged into Wattpad in your browser, then pass --cookies /path/to/cookies.txt"
            )
        viewer = fetch_logged_in_user(session)
        if not viewer:
            raise RuntimeError(
                "Could not detect a logged-in Wattpad user from the cookie file. "
                "Log in on wattpad.com in your browser, export fresh cookies, and try again."
            )
        author = story.get("user") or {}
        author_username = (author.get("username") or "").strip()
        my_username = (viewer.get("username") or "").strip()
        if not author_username or not my_username:
            raise RuntimeError(
                "Could not compare author identity (missing username in story metadata or session user). "
                "Ensure your cookie export is from a logged-in account."
            )
        if author_username.lower() != my_username.lower():
            raise RuntimeError(
                f"Paywalled export is only allowed when the logged-in account matches the story author "
                f"(story author: {author_username!r}, session user: {my_username!r})."
            )

    base = safe_story_basename(story, basename)
    english_base = f"{base}-en"
    chinese_html = output_dir / f"{base}-zh-cn.html"
    chinese_docx = output_dir / f"{base}-zh-cn.docx"
    metadata_json = output_dir / f"{base}-metadata.json"

    english = export_story_assets(
        story_url=story_url,
        output_dir=output_dir,
        basename=english_base,
        session=session,
        progress=True,
    )

    metadata = {
        "title": story.get("title"),
        "author": (story.get("user") or {}).get("username") or (story.get("user") or {}).get("name"),
        "story_url": story_url,
        "numParts": story.get("numParts"),
        "completed": story.get("completed"),
        "readCount": story.get("readCount"),
        "voteCount": story.get("voteCount"),
        "commentCount": story.get("commentCount"),
        "tags": story.get("tags", []),
        "english_html": str(english["html_path"]),
        "english_docx": str(english["docx_path"]),
    }

    result = {
        "metadata_json": metadata_json,
        "english_html": english["html_path"],
        "english_docx": english["docx_path"],
    }

    if translate_to_chinese:
        chinese = translate_html_document(
            input_html=english["html_path"],
            output_html=chinese_html,
            output_docx=chinese_docx,
            author=(story.get("user") or {}).get("username", "unknown"),
            progress=True,
        )
        result["chinese_html"] = chinese["html_path"]
        result["chinese_docx"] = chinese["docx_path"]
        metadata["chinese_html"] = str(chinese["html_path"])
        metadata["chinese_docx"] = str(chinese["docx_path"])

    write_json(metadata_json, metadata)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Wattpad helper CLI. Search can rank public metadata by popularity. "
            "Export requires a direct story URL plus an explicit authorization confirmation."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser(
        "search",
        help="Search Wattpad stories by keyword and rank returned public metadata by popularity.",
    )
    search_parser.add_argument("keyword", help="Keyword to search on Wattpad.")
    search_parser.add_argument("--max-results", type=int, default=20, help="Maximum stories to return after sorting.")
    search_parser.add_argument("--page-size", type=int, default=50, help="Number of results requested per API page.")
    search_parser.add_argument("--include-mature", action="store_true", help="Request mature results from the search API.")
    search_parser.add_argument(
        "--include-paywalled",
        action="store_true",
        help="Include paid/paywalled stories in search output. They are excluded by default.",
    )
    search_parser.add_argument("--json-out", type=Path, help="Optional path to write the search result payload as JSON.")
    search_parser.add_argument("--csv-out", type=Path, help="Optional path to write the ranked stories as CSV.")

    export_parser = subparsers.add_parser(
        "export",
        help=(
            "Export a single authorized free story URL to English DOCX and, by default, a Chinese DOCX. "
            "This command does not accept a keyword and does not export directly from search results."
        ),
    )
    export_parser.add_argument("story_url", help="Direct Wattpad story URL.")
    export_parser.add_argument(
        "--authorized",
        action="store_true",
        help="Required acknowledgment that you own this story or have explicit permission to archive and translate it.",
    )
    export_parser.add_argument("--output-dir", type=Path, default=Path("wattpad_tool_output"))
    export_parser.add_argument("--basename", help="Optional custom file stem for exported files.")
    export_parser.add_argument(
        "--skip-translation",
        action="store_true",
        help="Only generate the English HTML/DOCX pair.",
    )
    export_parser.add_argument(
        "--cookies",
        type=Path,
        help=(
            "Path to Netscape cookies.txt or JSON cookie export while logged into Wattpad. "
            "Required for paywalled stories; cookies must be from the account that owns the story."
        ),
    )

    return parser


def run_search(args: argparse.Namespace) -> int:
    session = build_session()
    try:
        payload = search_stories(
            session=session,
            keyword=args.keyword,
            max_results=args.max_results,
            page_size=args.page_size,
            include_mature=args.include_mature,
            include_paywalled=args.include_paywalled,
        )
    finally:
        session.close()

    print_search_results(payload)

    if args.json_out:
        write_json(args.json_out.expanduser().resolve(), payload)
        print(f"JSON: {args.json_out.expanduser().resolve()}")
    if args.csv_out:
        write_csv(args.csv_out.expanduser().resolve(), payload["stories"])
        print(f"CSV: {args.csv_out.expanduser().resolve()}")
    return 0


def run_export(args: argparse.Namespace) -> int:
    if not args.authorized:
        raise SystemExit(
            "Refusing export without explicit authorization. Re-run with --authorized only for stories you own or have permission to archive."
        )

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    session = build_session()
    try:
        result = export_authorized_story(
            session=session,
            story_url=args.story_url,
            output_dir=output_dir,
            basename=args.basename,
            translate_to_chinese=not args.skip_translation,
            cookies_path=args.cookies.expanduser().resolve() if args.cookies else None,
        )
    finally:
        session.close()

    print(f"Metadata: {result['metadata_json']}")
    print(f"English HTML: {result['english_html']}")
    print(f"English DOCX: {result['english_docx']}")
    if "chinese_html" in result:
        print(f"Chinese HTML: {result['chinese_html']}")
        print(f"Chinese DOCX: {result['chinese_docx']}")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "search":
        return run_search(args)
    if args.command == "export":
        return run_export(args)

    raise RuntimeError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
