from collections import Counter
from pathlib import Path

import pytest

from app.kb.chunker import load_all_docs

DOCS = Path(__file__).resolve().parents[2] / "docs"


@pytest.fixture(scope="module")
def chunks():
    return load_all_docs(DOCS)


def _by_point(chunks, no):
    found = [c for c in chunks if c.doc_slug == "pvtr" and c.point_no == no]
    assert len(found) == 1, f"п. {no}: {len(found)}"
    return found[0]


def test_counts_per_document(chunks):
    cnt = Counter(c.doc_slug for c in chunks)
    # ПВТР: 131 пункт + вводные разделы 2, 3, 4 (+ общее число с запасом на правки разметки)
    assert 125 <= cnt["pvtr"] <= 145
    assert cnt["anticorruption-policy"] == 14
    assert cnt["trade-secret"] == 14
    assert cnt["quality-ecology-policy"] == 5


def test_pvtr_points_have_page(chunks):
    for c in chunks:
        if c.doc_slug == "pvtr":
            assert c.page is not None and 1 <= c.page <= 23, c.source_label
            assert "стр." in c.source_label


def test_kt_flag_only_trade_secret(chunks):
    assert all(c.is_kt for c in chunks if c.doc_slug == "trade-secret")
    assert not any(c.is_kt for c in chunks if c.doc_slug != "trade-secret")


@pytest.mark.parametrize(
    "no,label,needle",
    [
        ("6.6.1", "ПВТР, п. 6.6.1, стр. 15", "Перерыв для отдыха и питания: 13:00 – 13:48"),  # рабочее время, обед
        ("6.5.2", "ПВТР, п. 6.5.2, стр. 15", "29-го числа текущего месяца"),  # зарплата
        ("6.7.4", "ПВТР, п. 6.7.4, стр. 17", "28 календарных дней"),  # отпуск
        ("6.7.3", "ПВТР, п. 6.7.3, стр. 17", "праздничн"),  # время отдыха
    ],
)
def test_known_points(chunks, no, label, needle):
    c = _by_point(chunks, no)
    assert c.source_label == label
    assert needle in c.text
    assert c.text.startswith(f"{no}.")
    assert "Раздел 6" in c.path and f"п. {no}" in c.path


def test_point_text_is_verbatim(chunks):
    src = (DOCS / "pvtr.md").read_text(encoding="utf-8")
    c = _by_point(chunks, "6.6.13")
    # перечисление осталось с родителем, и каждая строка есть в исходнике дословно
    assert "- инвалидов;" in c.text
    for line in c.text.splitlines():
        assert line.strip() in src


def test_slide_labels(chunks):
    labels = {c.source_label for c in chunks}
    assert "Антикоррупционная политика, слайд 8" in labels
    assert "Положение о коммерческой тайне, слайд 5" in labels
