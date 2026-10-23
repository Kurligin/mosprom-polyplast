"""Макеты вложений сценария (assets/): карточки 1080×1080 и PDF «где пообедать» / «карта».

Настоящих картинок и файлов заказчика у нас нет, поэтому каждый файл помечен крупно «МАКЕТ»,
а данные в нём синтетические. Где в ТЗ есть содержимое (список документов), оно перенесено в макет.

Запуск: python -m app.scenario.mockups [--force]  — создаёт недостающие файлы (с --force — все).
Движок вызывает ensure_asset(name) перед отправкой: если файла нет (например, food_<новая площадка>.pdf),
макет создаётся на лету.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"
FONT_DIR = Path("/usr/share/fonts/truetype/noto")

# Спокойная корпоративная палитра
BG = (243, 246, 250)
CARD = (255, 255, 255)
BLUE = (27, 95, 170)
BLUE_LIGHT = (229, 238, 250)
GREEN = (59, 170, 92)
INK = (26, 36, 51)
MUTED = (96, 108, 124)
LINE = (220, 226, 234)

SITE_NAMES = {"GrPP": "GrPP", "Luzhniki": "Лужники", "GTP": "GTP"}

DOCS = [
    "Бумажная трудовая книжка и сведения о трудовой деятельности (можно выгрузить с сайта госуслуг)",
    "Паспорт",
    "СНИЛС",
    "ИНН",
    "Документ об образовании (диплом, свидетельства, удостоверения, допуски)",
    "Свидетельство о браке/разводе (в случае смены ФИО)",
    "Свидетельство о рождении ребенка (до 18 лет)",
    "Документы воинского учета",
    "Для GTP: фотографии 3×4, 2 шт. (цвет/ЧБ)",
]

# Карточки: файл → (надзаголовок, заголовок, текст или список)
CARDS: dict[str, tuple[str, str, str | list[str]]] = {
    "img_01.png": ("Знакомство", "Корсик — твой помощник", "Помогу освоиться в первые три месяца работы"),
    "img_02.png": ("Приветствие", "Добро пожаловать в команду!", "Группа ПОЛИПЛАСТИК рада новому коллеге"),
    "img_03.png": ("Перед выходом", "Документы для оформления", DOCS),
    "img_04.png": ("Первый рабочий день", "С первым рабочим днём!", "Доступы к системам и рабочее место"),
    "img_05.png": ("Обед", "Где пообедать", "Места рядом с работой — в файле твоей площадки"),
    "img_06.png": ("Контакты", "Справочник контактов",
                   "Доступен с рабочих устройств во внутренней сети"),
    "img_07.png": ("Адаптация", "Адаптационный план", "Задачи на испытательный срок от руководителя"),
    "img_08.png": ("Обратная связь", "Поделись впечатлениями", "Короткий опрос после двух месяцев работы"),
    "img_09.png": ("Два месяца", "Финишная прямая", "Испытательный срок скоро закончится"),
    "img_10.png": ("Поздравляем", "Так держать!", "Ещё один важный этап позади"),
    "img_11.png": ("Жизнь компании", "Корпоративные мероприятия", "Праздники, спорт и традиции"),
    "img_12.png": ("Напоминание", "Ждём ответы на опрос", "Это займёт 1–2 минуты"),
    "img_13.png": ("Сложный вопрос", "Передам коллегам", "Посоветуюсь с подбором персонала и вернусь с ответом"),
    "img_14.png": ("Корсик", "Что умеет Корсик",
                   ["Пишет по плану адаптации", "Отвечает по документам компании", "Передаёт вопросы HR"]),
    "img_15.png": ("Встреча", "Встреча по адаптационному плану", "Детали пришлёт специалист по подбору"),
    "img_16.png": ("Прощание", "До встречи!", "Спасибо, что был на связи с Корсиком"),
    "card_01.png": ("Открытка", "С началом работы!", "Пусть всё получается"),
    "card_02.png": ("Открытка", "Ты на финишной прямой!", "Удачи и новых результатов"),
}

# Синтетические места для обеда: условные названия
FOOD = {
    "GTP": [("Столовая предприятия", "Административный корпус, 1 этаж", "11:30–15:00"),
            ("Кафе у проходной", "5 минут пешком от проходной", "08:00–20:00"),
            ("Буфет в цехе", "Производственный корпус, 2 этаж", "10:00–16:00")],
    "Luzhniki": [("Кафе в бизнес-центре", "1 этаж, у ресепшена", "09:00–19:00"),
                 ("Кухня офиса", "Микроволновки, кофемашина, холодильник", "весь день"),
                 ("Бизнес-ланч рядом", "Около 7 минут пешком", "12:00–16:00")],
    "GrPP": [("Столовая в офисе", "2 этаж, рядом с переговорными", "12:00–15:00"),
             ("Кофейня напротив", "3 минуты пешком", "08:00–21:00"),
             ("Кафе с бизнес-ланчем", "Около 10 минут пешком", "12:00–16:00")],
}
FOOD_DEFAULT = [("Столовая на площадке", "Уточните у руководителя", "в обед"),
                ("Кафе поблизости", "Рядом с работой", "днём")]

# Схема карты: блоки (x, y, w, h, подпись) в долях страницы
MAPS = {
    "GTP": ("Карта территории", [
        (0.06, 0.06, 0.30, 0.16, "Проходная"), (0.42, 0.06, 0.52, 0.30, "Производственный корпус"),
        (0.06, 0.28, 0.30, 0.22, "Административный корпус"), (0.42, 0.42, 0.24, 0.18, "Столовая"),
        (0.70, 0.42, 0.24, 0.18, "Спортзал"), (0.06, 0.56, 0.30, 0.20, "Склад"),
        (0.42, 0.66, 0.52, 0.12, "Парковка")]),
    "Luzhniki": ("Схема этажа офиса", [
        (0.06, 0.06, 0.26, 0.18, "Ресепшен"), (0.36, 0.06, 0.58, 0.40, "Open space"),
        (0.06, 0.28, 0.26, 0.18, "HR и подбор"), (0.06, 0.50, 0.26, 0.26, "Переговорные"),
        (0.36, 0.50, 0.28, 0.26, "Кухня"), (0.68, 0.50, 0.26, 0.26, "Зона отдыха")]),
}
MAP_DEFAULT = ("Схема площадки", [
    (0.06, 0.06, 0.40, 0.30, "Вход"), (0.52, 0.06, 0.42, 0.30, "Рабочие места"),
    (0.06, 0.42, 0.40, 0.34, "Столовая"), (0.52, 0.42, 0.42, 0.34, "Переговорные")])


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf"
    for p in (FONT_DIR / name, Path("/usr/share/fonts/truetype/dejavu") /
              ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")):
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        line = ""
        for word in para.split():
            test = f"{line} {word}".strip()
            if draw.textlength(test, font=font) <= width:
                line = test
            else:
                if line:
                    lines.append(line)
                line = word
        lines.append(line)
    return lines


def _badge_size(draw: ImageDraw.ImageDraw, scale: float = 1.0) -> tuple[int, int]:
    f = _font(int(54 * scale), bold=True)
    return int(draw.textlength("МАКЕТ", font=f)) + int(68 * scale), int(54 * scale * 1.25) + int(32 * scale)


def _badge(draw: ImageDraw.ImageDraw, x: int, y: int, scale: float = 1.0) -> None:
    """Крупная пометка «МАКЕТ»."""
    f = _font(int(54 * scale), bold=True)
    w, h = _badge_size(draw, scale)
    draw.rounded_rectangle((x, y, x + w, y + h), radius=int(22 * scale),
                           fill=BLUE_LIGHT, outline=BLUE, width=max(2, int(4 * scale)))
    draw.text((x + int(34 * scale), y + int(12 * scale)), "МАКЕТ", font=f, fill=BLUE)


def _footer(draw: ImageDraw.ImageDraw, x: int, y: int, scale: float = 1.0) -> None:
    f = _font(int(24 * scale))
    draw.text((x, y), "Корсик · Группа ПОЛИПЛАСТИК · макет, данные синтетические", font=f, fill=MUTED)


def card(kicker: str, title: str, body: str | list[str], size: int = 1080) -> Image.Image:
    img = Image.new("RGB", (size, size), BG)
    m = 56
    box = (m, m, size - m, size - m)
    # карточка с мягкими кругами в правом нижнем углу (круги обрезаны по карточке)
    layer = Image.new("RGB", (size, size), CARD)
    ld = ImageDraw.Draw(layer)
    ld.ellipse((size - 470, size - 470, size + 170, size + 170), fill=BLUE_LIGHT)
    ld.ellipse((size - 330, size - 330, size + 30, size + 30), fill=(214, 228, 246))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=44, fill=255)
    img.paste(layer, (0, 0), mask)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius=44, outline=LINE, width=2)
    d.rounded_rectangle((m, m + 40, m + 16, size - m - 40), radius=8, fill=BLUE)

    x, width = m + 70, size - 2 * m - 140
    bw, _ = _badge_size(d)
    _badge(d, size - m - 50 - bw, m + 50)
    y = m + 76
    d.ellipse((x, y + 8, x + 22, y + 30), fill=GREEN)
    d.text((x + 36, y), kicker.upper(), font=_font(30, bold=True), fill=GREEN)
    y = m + 190
    is_list = isinstance(body, list)
    tf = _font(56 if is_list else 66, bold=True)
    for line in _wrap(d, title, tf, width):
        d.text((x, y), line, font=tf, fill=INK)
        y += 72 if is_list else 84
    y += 24
    if isinstance(body, list):
        n = len(body)
        bf = _font(30 if n > 6 else 38)
        step = 40 if n > 6 else 54
        for i, item in enumerate(body, 1):
            d.text((x, y), f"{i}.", font=bf, fill=BLUE)
            for line in _wrap(d, item, bf, width - 60):
                d.text((x + 52, y), line, font=bf, fill=INK)
                y += step
            y += 8
    else:
        bf = _font(42)
        for line in _wrap(d, body, bf, width - 40):
            d.text((x, y), line, font=bf, fill=MUTED)
            y += 58
    _footer(d, x, size - m - 70)
    return img


def _page(title: str, subtitle: str) -> tuple[Image.Image, ImageDraw.ImageDraw, int]:
    """Страница A4 (150 dpi) с шапкой; возвращает картинку, рисовалку и y под шапкой."""
    w, h = 1240, 1754
    img = Image.new("RGB", (w, h), CARD)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, w, 220), fill=BLUE)
    d.text((80, 56), title, font=_font(64, bold=True), fill=CARD)
    d.text((80, 146), subtitle, font=_font(32), fill=BLUE_LIGHT)
    bw, _ = _badge_size(d, 0.95)
    _badge(d, w - 80 - bw, 60, scale=0.95)
    _footer(d, 80, h - 90, scale=1.1)
    return img, d, 300


def food_pdf(site: str) -> Image.Image:
    name = SITE_NAMES.get(site, site)
    img, d, y = _page("Где пообедать", f"Площадка {name}")
    places = FOOD.get(site, FOOD_DEFAULT)
    for title, where, hours in places:
        d.rounded_rectangle((80, y, 1160, y + 230), radius=28, fill=BG, outline=LINE, width=2)
        d.text((120, y + 34), title, font=_font(46, bold=True), fill=INK)
        d.text((120, y + 104), where, font=_font(34), fill=MUTED)
        d.text((120, y + 156), f"Часы работы: {hours}", font=_font(34), fill=BLUE)
        y += 270
    d.text((80, y + 20), "Названия и адреса условные. Актуальный список пришлёт HR площадки.",
           font=_font(30), fill=MUTED)
    return img


def map_pdf(site: str) -> Image.Image:
    name = SITE_NAMES.get(site, site)
    title, blocks = MAPS.get(site, MAP_DEFAULT)
    img, d, y = _page(title, f"Площадка {name}")
    x0, y0, mw, mh = 80, y, 1080, 1250
    d.rounded_rectangle((x0, y0, x0 + mw, y0 + mh), radius=24, fill=BG, outline=LINE, width=2)
    colors = [BLUE_LIGHT, (232, 246, 236), (250, 243, 228), (241, 236, 250)]
    for i, (bx, by, bw, bh, label) in enumerate(blocks):
        r = (x0 + bx * mw, y0 + by * mh, x0 + (bx + bw) * mw, y0 + (by + bh) * mh)
        d.rounded_rectangle(r, radius=18, fill=colors[i % len(colors)], outline=BLUE, width=3)
        size = 32
        while size > 20 and max(d.textlength(wd, font=_font(size, bold=True)) for wd in label.split()) > r[2] - r[0] - 30:
            size -= 2
        f = _font(size, bold=True)
        lines = _wrap(d, label, f, int(r[2] - r[0] - 30))
        ty = (r[1] + r[3]) / 2 - len(lines) * 21
        for line in lines:
            tw = d.textlength(line, font=f)
            d.text(((r[0] + r[2] - tw) / 2, ty), line, font=f, fill=INK)
            ty += 42
    d.text((80, y0 + mh + 30), "Схема условная, без масштаба.", font=_font(30), fill=MUTED)
    return img


def plan_pdf() -> Image.Image:
    img, d, y = _page("Адаптационный план", "Пример, заполняет руководитель")
    rows = [("1-я неделя", "Знакомство с командой, доступы, вводные инструктажи"),
            ("2–4-я недели", "Первые задачи под присмотром наставника"),
            ("2-й месяц", "Самостоятельные задачи, встреча по плану с подбором"),
            ("3-й месяц", "Итоги испытательного срока, обратная связь")]
    for period, task in rows:
        d.rounded_rectangle((80, y, 1160, y + 200), radius=24, fill=BG, outline=LINE, width=2)
        d.text((120, y + 30), period, font=_font(42, bold=True), fill=BLUE)
        for i, line in enumerate(_wrap(d, task, _font(34), 980)):
            d.text((120, y + 96 + i * 46), line, font=_font(34), fill=INK)
        y += 230
    return img


def events_photos() -> Image.Image:
    size = 1080
    img = Image.new("RGB", (size, size), BG)
    d = ImageDraw.Draw(img)
    tiles = [((48, 48, 528, 528), BLUE_LIGHT, "Спартакиада"), ((552, 48, 1032, 528), (232, 246, 236), "Новый год"),
             ((48, 552, 528, 1032), (250, 243, 228), "День химика"), ((552, 552, 1032, 1032), (241, 236, 250),
                                                                       "Субботник")]
    for box, color, label in tiles:
        d.rounded_rectangle(box, radius=36, fill=color, outline=LINE, width=2)
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        d.ellipse((cx - 70, cy - 120, cx + 70, cy + 20), outline=BLUE, width=6)
        f = _font(40, bold=True)
        tw = d.textlength(label, font=f)
        d.text((cx - tw / 2, cy + 60), label, font=f, fill=INK)
        sub = "фото с мероприятия"
        sf = _font(28)
        d.text((cx - d.textlength(sub, font=sf) / 2, cy + 116), sub, font=sf, fill=MUTED)
    bw, bh = _badge_size(d)
    _badge(d, (size - bw) // 2, (size - bh) // 2)
    return img


def render(name: str, title: str | None = None) -> Image.Image:
    """Картинка для файла name. Неизвестное имя — карточка с заголовком title."""
    if name in CARDS:
        return card(*CARDS[name])
    if name == "events_photos.jpg":
        return events_photos()
    if name == "plan_example.pdf":
        return plan_pdf()
    m = re.fullmatch(r"(food|map)_(\w+)\.pdf", name)
    if m:
        return food_pdf(m.group(2)) if m.group(1) == "food" else map_pdf(m.group(2))
    return card("Корсик", title or "Материал для новичка", "Файл появится позже")


def save(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".pdf":
        img.save(path, "PDF", resolution=150.0)
    elif path.suffix.lower() in (".jpg", ".jpeg"):
        img.save(path, "JPEG", quality=88)
    else:
        img.save(path, "PNG", optimize=True)


def ensure_asset(name: str, title: str | None = None) -> Path:
    """Путь к файлу в assets/; если файла нет — создаём макет."""
    path = ASSETS_DIR / Path(name).name
    if not path.exists():
        save(render(path.name, title), path)
    return path


ALL = [*CARDS, "events_photos.jpg", "plan_example.pdf",
       *(f"food_{s}.pdf" for s in FOOD), *(f"map_{s}.pdf" for s in MAPS)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Макеты вложений сценария → assets/")
    ap.add_argument("--force", action="store_true", help="перерисовать существующие")
    args = ap.parse_args(argv)
    for name in ALL:
        path = ASSETS_DIR / name
        if args.force or not path.exists():
            save(render(name), path)
            print(f"  {name}")
    print(f"Готово: {ASSETS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
