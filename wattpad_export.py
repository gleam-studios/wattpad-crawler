#!/usr/bin/env python3

import argparse
import html as html_lib
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import requests
from bs4 import BeautifulSoup

from docx_renderer import convert_html_file_to_docx


USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return slug or "story"


def extract_json_blob(page: str, marker: str) -> Dict:
    anchor = page.find(marker)
    if anchor == -1:
        raise RuntimeError(f"Could not find JSON marker: {marker}")

    start = page.find("{", anchor)
    if start == -1:
        raise RuntimeError(f"Could not find JSON start for marker: {marker}")

    depth = 0
    in_string = False
    escaped = False

    for idx in range(start, len(page)):
        ch = page[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(page[start : idx + 1])

    raise RuntimeError(f"Could not parse JSON blob for marker: {marker}")


def clean_fragment(fragment_html: str) -> Tuple[str, int]:
    soup = BeautifulSoup(fragment_html, "html.parser")

    allowed_tags = {
        "p",
        "div",
        "span",
        "strong",
        "b",
        "em",
        "i",
        "u",
        "br",
        "blockquote",
        "hr",
    }

    for tag in soup.find_all(True):
        if tag.name not in allowed_tags:
            tag.unwrap()
            continue

        attrs = {}
        if tag.name in {"p", "div"} and tag.get("style"):
            attrs["style"] = tag["style"]
        tag.attrs = attrs

    paragraphs: List[str] = []
    visible_text: List[str] = []

    for node in soup.contents:
        if getattr(node, "name", None) not in {"p", "div", "blockquote", "hr"}:
            if getattr(node, "strip", None):
                text = node.strip()
                if text:
                    paragraphs.append(f"<p>{html_lib.escape(text)}</p>")
                    visible_text.append(text)
            continue

        if node.name == "hr":
            paragraphs.append("<hr />")
            continue

        text = node.get_text(" ", strip=True)
        if not text:
            continue

        paragraphs.append(str(node))
        visible_text.append(text)

    word_count = len(re.findall(r"\b[\w'-]+\b", " ".join(visible_text)))
    return "\n".join(paragraphs), word_count


def fetch_story(session: requests.Session, story_url: str) -> Dict:
    response = session.get(story_url, timeout=30)
    response.raise_for_status()
    data = extract_json_blob(response.text, "window.__remixContext = ")
    loader = data["state"]["loaderData"]["routes/story.$storyid"]
    return loader["story"]


def fetch_part_html(session: requests.Session, part: Dict) -> Tuple[str, int]:
    pages: List[str] = []
    total_words = 0
    page_no = 1

    while True:
        text_url = (
            f"https://www.wattpad.com/apiv2/?m=storytext&id={part['id']}&page={page_no}"
        )
        response = session.get(
            text_url,
            headers={
                "Referer": part["url"],
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=30,
        )
        response.raise_for_status()
        fragment = response.text.strip()
        if not fragment:
            break

        cleaned_html, word_count = clean_fragment(fragment)
        if cleaned_html:
            pages.append(cleaned_html)
            total_words += word_count
        page_no += 1

    if not pages:
        raise RuntimeError(f"No text pages returned for part {part['id']} ({part['title']})")

    return "\n".join(pages), total_words


def render_story_html(story: Dict, chapters: List[Dict]) -> str:
    total_words = sum(chapter["word_count"] for chapter in chapters)
    description = html_lib.escape(story.get("description", "")).replace("\n", "<br />\n")
    tags = ", ".join(story.get("tags", []))
    toc_items = "\n".join(
        f'<li>{html_lib.escape(chapter["display_title"])}</li>' for chapter in chapters
    )

    chapter_sections = []
    for idx, chapter in enumerate(chapters, start=1):
        chapter_sections.append(
            f"""
            <section class="chapter">
              <h2>Chapter {idx}: {html_lib.escape(chapter["title"])}</h2>
              <div class="chapter-meta">Part ID: {chapter["id"]} | Approx. {chapter["word_count"]} words</div>
              {chapter["html"]}
            </section>
            """
        )

    body = "\n".join(chapter_sections)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{html_lib.escape(story["title"])}</title>
  <style>
    @page {{
      margin: 1in;
    }}
    body {{
      font-family: Georgia, serif;
      color: #1f1f1f;
      line-height: 1.55;
      font-size: 12pt;
    }}
    h1, h2, h3 {{
      font-family: "Helvetica Neue", Arial, sans-serif;
      color: #111;
    }}
    .title-page {{
      page-break-after: always;
    }}
    .eyebrow {{
      font-size: 11pt;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: #666;
      margin-bottom: 0.4rem;
    }}
    .meta {{
      margin: 0.35rem 0;
    }}
    .summary, .toc {{
      margin-top: 1.2rem;
    }}
    .toc ul {{
      padding-left: 1.2rem;
    }}
    .chapter {{
      page-break-before: always;
    }}
    .chapter-meta {{
      color: #666;
      font-size: 10pt;
      margin-bottom: 1rem;
    }}
    p {{
      margin: 0 0 0.72rem 0;
      text-align: justify;
    }}
    blockquote {{
      margin: 0.8rem 1.5rem;
      color: #444;
    }}
    hr {{
      border: 0;
      border-top: 1px solid #bbb;
      margin: 1.2rem 0;
    }}
  </style>
</head>
<body>
  <section class="title-page">
    <div class="eyebrow">Wattpad Export</div>
    <h1>{html_lib.escape(story["title"])}</h1>
    <div class="meta"><strong>Author:</strong> {html_lib.escape(story["user"]["username"])}</div>
    <div class="meta"><strong>Status:</strong> {"Completed" if story.get("completed") else "Ongoing"}</div>
    <div class="meta"><strong>Chapters:</strong> {story.get("numParts", len(chapters))}</div>
    <div class="meta"><strong>Total Estimated Words:</strong> {total_words}</div>
    <div class="meta"><strong>Tags:</strong> {html_lib.escape(tags)}</div>
    <div class="summary">
      <h2>Story Summary</h2>
      <p>{description}</p>
    </div>
    <div class="toc">
      <h2>Contents</h2>
      <ul>
        {toc_items}
      </ul>
    </div>
  </section>
  {body}
</body>
</html>
"""
def export_story_assets(
    story_url: str,
    output_dir: str | Path = "wattpad_exports",
    basename: str | None = None,
    session: requests.Session | None = None,
    progress: bool = True,
) -> Dict[str, object]:
    own_session = session is None
    active_session = session or requests.Session()
    active_session.headers.update({"User-Agent": USER_AGENT})

    try:
        story = fetch_story(active_session, story_url)
        out_dir = Path(output_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        chapters = []
        total_parts = len(story["parts"])

        for idx, part in enumerate(story["parts"], start=1):
            if progress:
                print(
                    f"[{idx}/{total_parts}] Fetching {part['title']} ({part['id']})...",
                    file=sys.stderr,
                )
            chapter_html, word_count = fetch_part_html(active_session, part)
            chapters.append(
                {
                    "id": part["id"],
                    "title": part["title"],
                    "display_title": f"Chapter {idx}: {part['title']}",
                    "html": chapter_html,
                    "word_count": word_count,
                }
            )

        safe_name = basename or slugify(story["title"])
        html_path = out_dir / f"{safe_name}.html"
        docx_path = out_dir / f"{safe_name}.docx"

        story_html = render_story_html(story, chapters)
        html_path.write_text(story_html, encoding="utf-8")
        convert_html_file_to_docx(
            html_path,
            docx_path,
            title=story["title"],
            author=story["user"]["username"],
        )

        return {
            "story": story,
            "chapters": chapters,
            "html_path": html_path,
            "docx_path": docx_path,
        }
    finally:
        if own_session:
            active_session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a public Wattpad story to DOCX.")
    parser.add_argument("story_url", help="Wattpad story URL, e.g. https://www.wattpad.com/story/123-title")
    parser.add_argument(
        "--output-dir",
        default="wattpad_exports",
        help="Directory where HTML and DOCX files will be written",
    )
    args = parser.parse_args()

    result = export_story_assets(
        story_url=args.story_url,
        output_dir=args.output_dir,
    )

    print(f"HTML: {result['html_path']}")
    print(f"DOCX: {result['docx_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
