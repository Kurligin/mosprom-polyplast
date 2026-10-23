"""Нарезка Markdown-документов базы знаний на дословные фрагменты с источником.

ПВТР режется по пунктам (6.6.1, 1.1, 7.2 …), страница — из маркеров <!-- стр. N -->.
Презентации (антикоррупция, КТ, качество) — слайд = фрагмент.
Текст фрагмента дословный: только снимаем служебные HTML-комментарии.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# slug файла → (короткое имя для подписи, тип нарезки, пометка КТ)
DOCS: dict[str, tuple[str, str, bool]] = {
    "pvtr": ("ПВТР", "points", False),
    "anticorruption-policy": ("Антикоррупционная политика", "slides", False),
    "trade-secret": ("Положение о коммерческой тайне", "slides", True),
    "quality-ecology-policy": ("Политика в области качества и экологии", "slides", False),
}


@dataclass
class Chunk:
    doc_slug: str
    doc_title: str
    path: str  # «Раздел 6 → 6.6. Рабочее время → п. 6.6.1»
    point_no: str | None  # «6.6.1»; у слайдов — None
    page: int | None  # страница (ПВТР) или номер слайда
    text: str  # дословный текст пункта
    is_kt: bool = False
    source_label: str = ""  # «ПВТР, п. 6.6.1, стр. 15»
    page_end: int | None = field(default=None, repr=False)

    @property
    def key(self) -> str:
        """Стабильный ключ фрагмента внутри базы (для логов и эталона)."""
        if self.point_no:
            return f"{self.doc_slug}:{self.point_no}"
        return f"{self.doc_slug}:{self.page}"


_PAGE_RE = re.compile(r"<!--\s*стр\.\s*(\d+)\s*-->")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_H1_RE = re.compile(r"^#\s+(.+)$", re.M)
_SECTION_RE = re.compile(r"^##\s+(\d+)\.\s+(.+)$")  # ## 6. ПРАВИЛА …
_SUBSECTION_RE = re.compile(r"^###\s+(\d+\.\d+)\.?\s+(.+)$")  # ### 6.6. Рабочее время
_POINT_RE = re.compile(r"^(\d+\.\d+(?:\.\d+)*)\.?\s+\S")  # 6.6.1. … / 1.1 …
_SLIDE_RE = re.compile(r"^##\s+Слайд\s+(\d+)\.?\s*(.*)$")


def _clean(lines: list[str]) -> str:
    """Склеить строки, убрать HTML-комментарии и лишние пустые строки."""
    text = _COMMENT_RE.sub("", "\n".join(lines))
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _pages_label(start: int | None, end: int | None) -> str:
    if start is None:
        return ""
    if end and end != start:
        return f"стр. {start}–{end}"
    return f"стр. {start}"


def chunk_points(md: str, doc_slug: str, short: str, is_kt: bool = False) -> list[Chunk]:
    """Нарезка документа с нумерованными пунктами (ПВТР)."""
    title_m = _H1_RE.search(md)
    doc_title = title_m.group(1).strip() if title_m else short
    # убираем шапку-комментарий в начале, маркеры страниц обрабатываем построчно
    lines = md.splitlines()

    chunks: list[Chunk] = []
    page: int | None = None
    section: tuple[str, str] | None = None  # (номер, название)
    subsection: tuple[str, str] | None = None
    cur: dict | None = None  # открытый фрагмент
    in_comment = False

    def close():
        nonlocal cur
        if cur is None:
            return
        text = _clean(cur["lines"])
        if text:
            if cur["point_no"]:
                parts = [f"Раздел {cur['section'][0]}"] if cur["section"] else []
                if cur["subsection"]:
                    parts.append(f"{cur['subsection'][0]}. {cur['subsection'][1]}")
                parts.append(f"п. {cur['point_no']}")
                path = " → ".join(parts)
                label = f"{short}, п. {cur['point_no']}, {_pages_label(cur['page'], cur['page_end'])}"
            else:
                sec_no, sec_title = cur["section"]
                path = f"Раздел {sec_no}. {sec_title.capitalize()}"
                label = f"{short}, разд. {sec_no}, {_pages_label(cur['page'], cur['page_end'])}"
            chunks.append(
                Chunk(
                    doc_slug=doc_slug,
                    doc_title=doc_title,
                    path=path,
                    point_no=cur["point_no"],
                    page=cur["page"],
                    page_end=cur["page_end"],
                    text=text,
                    is_kt=is_kt,
                    source_label=label.rstrip(", "),
                )
            )
        cur = None

    for line in lines:
        s = line.strip()
        # многострочный комментарий-шапка в начале файла
        if in_comment:
            if "-->" in s:
                in_comment = False
            continue
        pm = _PAGE_RE.fullmatch(s)
        if pm:
            page = int(pm.group(1))
            if cur is not None:
                cur["pending_page"] = page  # страница сменилась; засчитаем, если пойдёт текст
            continue
        if s.startswith("<!--") and "-->" not in s:
            in_comment = True
            continue

        m = _SECTION_RE.match(s)
        if m:
            close()
            section = (m.group(1), m.group(2).strip())
            subsection = None
            # вводный текст раздела без пунктов (термины, ссылки) — отдельный фрагмент
            cur = _new(None, page, section, subsection)
            continue
        if s.startswith("## "):  # СОДЕРЖАНИЕ и прочие ненумерованные заголовки
            close()
            section = None
            continue
        m = _SUBSECTION_RE.match(s)
        if m:
            close()
            subsection = (m.group(1), m.group(2).strip())
            continue
        if s.startswith("#"):
            close()
            continue

        m = _POINT_RE.match(s)
        if m and section is not None:
            close()
            cur = _new(m.group(1), page, section, subsection)
            cur["lines"].append(line)
            continue

        if cur is not None:
            if s and cur.get("pending_page"):
                cur["page_end"] = cur.pop("pending_page")
            cur["lines"].append(line)
    close()
    return chunks


def _new(point_no, page, section, subsection) -> dict:
    return {
        "point_no": point_no,
        "page": page,
        "page_end": page,
        "section": section,
        "subsection": subsection,
        "lines": [],
    }


def chunk_slides(md: str, doc_slug: str, short: str, is_kt: bool = False) -> list[Chunk]:
    """Нарезка презентации: один слайд — один фрагмент. Пустые слайды пропускаем."""
    title_m = _H1_RE.search(md)
    doc_title = title_m.group(1).strip() if title_m else short
    chunks: list[Chunk] = []
    cur: tuple[int, str] | None = None
    buf: list[str] = []

    def close():
        if cur is None:
            return
        no, head = cur
        body = _clean(buf)
        if not body:
            return
        text = f"{head}\n\n{body}" if head else body
        path = f"Слайд {no}" + (f". {head}" if head else "")
        chunks.append(
            Chunk(
                doc_slug=doc_slug,
                doc_title=doc_title,
                path=path,
                point_no=None,
                page=no,
                page_end=no,
                text=text,
                is_kt=is_kt,
                source_label=f"{short}, слайд {no}",
            )
        )

    for line in md.splitlines():
        m = _SLIDE_RE.match(line.strip())
        if m:
            close()
            cur = (int(m.group(1)), m.group(2).strip())
            buf = []
            continue
        if cur is not None:
            buf.append(line)
    close()
    return chunks


def chunk_document(path: Path) -> list[Chunk]:
    slug = path.stem
    short, mode, is_kt = DOCS[slug]
    md = path.read_text(encoding="utf-8")
    if mode == "points":
        return chunk_points(md, slug, short, is_kt)
    return chunk_slides(md, slug, short, is_kt)


def load_all_docs(docs_dir: str | Path = "docs") -> list[Chunk]:
    """Все фрагменты базы знаний из известных документов в docs_dir."""
    docs_dir = Path(docs_dir)
    out: list[Chunk] = []
    for slug in DOCS:
        p = docs_dir / f"{slug}.md"
        if p.exists():
            out.extend(chunk_document(p))
    return out
