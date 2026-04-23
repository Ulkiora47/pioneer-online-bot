import os
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from anthropic import Anthropic
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, filters, ContextTypes
)
from telegram.constants import ParseMode, ChatAction
try:
    from PIL import Image, ImageDraw, ImageFont
    PILLOW_OK = True
except ImportError:
    PILLOW_OK = False

# ── Настройки ──────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "ВАШ_ТОКЕН_TELEGRAM")
ANTHROPIC_KEY  = os.getenv("ANTHROPIC_KEY",  "ВАШ_КЛЮЧ_ANTHROPIC")
ADMIN_ID       = int(os.getenv("ADMIN_ID", "0"))
DATA_FILE      = Path("/app/data/clients.json")
Path("/app/data").mkdir(parents=True, exist_ok=True)

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ── Состояния ConversationHandler ─────────────────────────────────────────
LOG_DAY, LOG_FEEL, LOG_RPE, LOG_WEIGHTS, LOG_NOTES = range(5)
NUT_GOAL, NUT_DATA, NUT_PREFS = range(5, 8)
NUT_SEX, NUT_AGE, NUT_WEIGHT, NUT_HEIGHT, NUT_ACTIVITY, NUT_GOAL, NUT_PREFS = range(10, 17)

# ── Системные промпты ──────────────────────────────────────────────────────
SYSTEM_MAIN = """Ты — ассистент тренерской команды зала Пионер (Pioneer Online). Зал работает с 2014 года, 2000+ результатов от фитнеса до МС.
СТИЛЬ: короткие сообщения, живой язык, как пишет тренер другу. Никакой воды и длинных объяснений. Один вопрос — одно сообщение. Поддерживай, но без пафоса.

АНКЕТА — строго по одному шагу, жди ответа:
0. Имя
1. Вид спорта + цель
2. Стаж, уровень, рекорды
3. Возраст, вес, травмы
4. Оборудование, дней/нед, время на тренировку
5. Работа, сон, стресс
6. Ключевые метрики (1ПМ / FTP / VDOT / бенчмарки)
→ Только после шага 6 составляй программу.

СПЕЦИФИКА:
ПЛ/ТА: блоки ACC→TRA→REA→пик, RPE, разгрузка каждые 4 нед.
ББ: двойная прогрессия, 6–20 повт, сплиты по уровню.
CF: 3 модальности, 3 энергосистемы, масштабирование.
Цикл: 80/20, FTP/VDOT/CSS, правило 10%.
Единоборства: ОФП после техники, в сезоне объём −60%.

ПРОГРАММА: одна неделя за раз. После каждой: "✅ Неделя N готова. Пиши *далее* — следующая."

ВАЖНО: пиши кратко. Максимум 5–7 строк на сообщение. Никаких длинных списков без запроса. Русский язык, Telegram Markdown."""
SYSTEM_NUTRITION = """Ты — спортивный нутрициолог с 12+ годами практики. Работаешь с атлетами от любителей до профессионалов.
БАЗА ЗНАНИЙ: спортивная диетология, нутрициология, биохимия питания, периодизация питания под тренировочный цикл. Знаешь работы Лайла Макдональда, Алана Арагона, позиции ISSN, ADA, современные исследования по композиции тела.

РАСЧЁТ КБЖУ:
- Базовый обмен: формула Миффлина-Сан Жеора (точнее для спортсменов чем Харриса-Бенедикта)
- Коэффициенты активности: сидячий 1.2, лёгкая 1.375, умеренная 1.55, высокая 1.725, очень высокая 1.9
- Для набора: +10-20% к TDEE (0.5-1 кг/мес — чистый набор)
- Для похудения: -20% от TDEE (не более -500 ккал/день)
- Для рекомпозиции: TDEE ±0, высокий белок
- Белок: 1.6-2.2 г/кг для силовых, 1.4-1.7 г/кг для циклических, 2.2-3.0 г/кг при дефиците
- Жиры: минимум 0.8-1.0 г/кг (гормоны, витамины), обычно 25-30% калорий
- Углеводы: остаток калорий
- Учитывай: тип спорта, фазу цикла (набор/сушка/поддержание), время тренировок, пищевые предпочтения

МЕНЮ:
- Строй меню на реальных продуктах, доступных в России
- Учитывай вкусовые предпочтения и аллергии
- Разбивай на 3-5 приёмов пищи
- Указывай граммовку каждого продукта
- Давай варианты замены продуктов
- Учитывай пери-тренировочное питание (пред- и пост-тренировка)

СТИЛЬ: короткие сообщения. Конкретные цифры. Без воды. Как говорит хороший специалист — по делу.
Отвечай по-русски. Telegram Markdown: *жирный*, _курсив_."""
SYSTEM_NUTRITION = """Ты — спортивный нутрициолог и диетолог высокого уровня. Работаешь в команде зала Пионер.
ЭКСПЕРТИЗА: спортивное питание, периодизация нутриции, работа с весом (набор/сушка/рекомпозиция), питание для силовых и циклических видов спорта, микронутриенты, спортивные добавки.

БАЗА ЗНАНИЙ: Лайл МакДональд, Алан Арагон, Eric Helms (The Muscle and Strength Nutrition Pyramid), Israetel (Renaissance Periodization), ISSN guidelines 2023.

РАСЧЁТ КБЖУ — СТРОГИЙ АЛГОРИТМ:
1. Базовый обмен (BMR) по формуле Миффлина-Сан Жеора:
   Муж: 10×вес + 6.25×рост − 5×возраст + 5
   Жен: 10×вес + 6.25×рост − 5×возраст − 161
2. TDEE = BMR × коэффициент активности:
   1.2 — сидячий | 1.375 — лёгкая (1-3 трен/нед) | 1.55 — умеренная (3-5) | 1.725 — высокая (6-7) | 1.9 — очень высокая (2× в день)
3. Коррекция под цель:
   Набор: +200..+350 ккал (чистый набор) или +400..+500 (агрессивный)
   Сушка: −300..−500 ккал (умеренный дефицит) или до −750 (быстрая, с риском потери мышц)
   Рекомпозиция: ±0..±100 ккал (только для начинающих или после перерыва)
4. Распределение БЖУ:
   БЕЛОК: 1.6–2.2 г/кг (силовые), 1.4–1.8 г/кг (циклические), 2.2–2.6 г/кг (сушка)
   ЖИРЫ: минимум 0.8–1.0 г/кг, оптимум 25–30% от ккал
   УГЛЕВОДЫ: остаток калорий (4 ккал/г)
5. Тайминг нутриции (если спрашивают):
   — Белок равномерно 3–5 приёмов, порция 30–50 г
   — Углеводы акцент до и после тренировки
   — Жиры подальше от тренировки

МЕНЮ НА НЕДЕЛЮ:
— Реальные блюда из доступных продуктов
— Указывай граммовку каждого ингредиента
— КБЖУ каждого приёма и итог дня
— Учитывай пищевые предпочтения и ограничения
— Одна неделя за раз, 5–7 дней
— Делай меню разнообразным, не повторяй одни блюда каждый день

СТИЛЬ: короткий живой язык, как эксперт который говорит с клиентом. Никакой воды. Цифры — точные. Русский язык."""
SYSTEM_ANALYSIS = """Ты — профессиональный спортивный аналитик и тренер.
Анализируй дневник тренировок, давай конкретные экспертные рекомендации.
Используй периодизацию, RPE, спортивную физиологию. Отвечай по-русски.
Telegram Markdown: *жирный*, _курсив_."""
# ── Псевдонимы кнопок → команды ───────────────────────────────────────────
ALIASES = {
    "записать тренировку":  "log",
    "анализ недели":        "week",
    "мой прогресс":         "progress",
    "мои записи":           "logview",
    "следующая неделя":     "nextweek",
    "далее":                "nextweek",
    "шпаргалка":            "card",
    "карточка недели":      "card",
    "питание":              "nutrition",
    "кбжу":                 "nutrition",
    "нутрициолог":          "nutrition",
    "меню на неделю":       "nutmenu",
    "питание и кбжу":      "nutrition",
    "питание":              "nutrition",
    "нутрициолог":          "nutrition",
    "кбжу":                 "nutrition",
    "меню на неделю":       "nutmenu",
    "меню":                 "nutmenu",
    "корректировка":        "adjust",
    "начать заново":        "reset",
    "помощь":               "help",
}

def resolve_alias(text: str):
    clean = re.sub(r"[^\w\s]", "", text.lower()).strip()
    for key, cmd in ALIASES.items():
        if key in clean:
            return cmd
    return None

# ── Клавиатуры ────────────────────────────────────────────────────────────
def main_kb() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура — видна всегда, не скрывается."""
    return ReplyKeyboardMarkup(
        [
            ["📓 Записать тренировку",  "📊 Анализ недели"],
            ["📈 Мой прогресс",          "📋 Мои записи"],
            ["🥗 Питание и КБЖУ",        "🍽 Меню на неделю"],
            ["🃏 Шпаргалка недели",      "➡️ Следующая неделя"],
            ["⚙️ Корректировка",         "❓ Помощь"],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

def sport_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["🏋 Пауэрлифтинг / ТА",  "💪 Бодибилдинг"],
            ["⚡ Кроссфит",            "🚴 Бег / Вело / Плавание"],
            ["🥊 Единоборства"],
        ],
        resize_keyboard=True, one_time_keyboard=True
    )

def feel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["1","2","3","4","5"],["6","7","8","9","10"]],
        resize_keyboard=True, one_time_keyboard=True
    )

def rpe_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["5","6","7"],["8","9","10"]],
        resize_keyboard=True, one_time_keyboard=True
    )

def day_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["Понедельник","Вторник","Среда"],
         ["Четверг","Пятница","Суббота"],
         ["Воскресенье","❌ Отмена"]],
        resize_keyboard=True, one_time_keyboard=True
    )

# ── Хранилище ──────────────────────────────────────────────────────────────
def load_data() -> dict:
    if DATA_FILE.exists():
        try: return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except: pass
    return {}

def save_data(data: dict):
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def get_client(uid: int) -> dict:
    data = load_data()
    key  = str(uid)
    if key not in data:
        data[key] = {"name":"Новый клиент","history":[],"step":0,
                     "program":None,"current_week":1,"log":[],
                     "created_at":datetime.now().isoformat()}
        save_data(data)
    return data[key]

def save_client(uid: int, client: dict):
    data = load_data()
    data[str(uid)] = client
    save_data(data)

# ── Утилиты ────────────────────────────────────────────────────────────────
def chunks(text: str, size=4000) -> list[str]:
    if len(text) <= size: return [text]
    result = []
    while text:
        if len(text) <= size: result.append(text); break
        i = text.rfind("\n", 0, size)
        if i == -1: i = size
        result.append(text[:i])
        text = text[i:].lstrip("\n")
    return result

async def reply(update: Update, text: str, kb=None):
    for i, chunk in enumerate(chunks(text)):
        kw = {"parse_mode": ParseMode.MARKDOWN}
        if kb and i == len(chunks(text)) - 1:
            kw["reply_markup"] = kb
        try:
            await update.message.reply_text(chunk, **kw)
        except:
            kw.pop("parse_mode", None)
            await update.message.reply_text(chunk, **kw)

def claude(history: list, system=SYSTEM_MAIN) -> str:
    c = Anthropic(api_key=ANTHROPIC_KEY)
    r = c.messages.create(
        model="claude-sonnet-4-5", max_tokens=6000,
        system=system, messages=history
    )
    return r.content[0].text

def fmt(e: dict) -> str:
    return (
        f"📅 *{e.get('date','—')}* | Нед.{e.get('week','—')} | {e.get('day_name','—')}\n"
        f"😴 Самочувствие: {e.get('feeling','—')}/10  💪 RPE: {e.get('rpe','—')}/10\n"
        f"🏋 {e.get('weights','—')}\n"
        f"📝 {e.get('notes','—')}"
    )

# ── Настройка меню команд ──────────────────────────────────────────────────
async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start",    "🏁 Начать / новая анкета"),
        BotCommand("log",      "📓 Записать тренировку"),
        BotCommand("week",     "📊 Анализ недели"),
        BotCommand("progress", "📈 Мой прогресс"),
        BotCommand("logview",  "📋 Последние записи"),
        BotCommand("nextweek", "➡️ Следующая неделя"),
        BotCommand("adjust",   "⚙️ Корректировка программы"),
        BotCommand("reset",    "🔄 Начать заново"),
        BotCommand("help",     "❓ Помощь"),
        BotCommand("clients",  "👥 Список клиентов (тренер)"),
        BotCommand("stats",    "📊 Статистика бота (тренер)"),
        BotCommand("client",   "👤 Карточка клиента (тренер)"),
        BotCommand("card",     "🃏 Шпаргалка недели — сохранить фото"),
        BotCommand("nutrition", "🥗 Питание и расчёт КБЖУ"),
        BotCommand("nutmenu",  "📋 Меню на неделю"),
        BotCommand("nutrition", "🥗 Расчёт КБЖУ и питание"),
        BotCommand("nutmenu",  "🍽 Меню на неделю"),
    ])

# ── Команды ────────────────────────────────────────────────────────────────

def make_week_card(client: dict) -> bytes | None:
    """Генерирует PNG-шпаргалку 9:16."""
    if not PILLOW_OK:
        return None
    program = client.get("program", "")
    if not program:
        return None

    import re, os, io

    base_dir = os.path.dirname(os.path.abspath(__file__))
    font_bold_path = os.path.join(base_dir, "fonts", "DejaVuSans-Bold.ttf")
    font_reg_path  = os.path.join(base_dir, "fonts", "DejaVuSans.ttf")

    def gf(path, size):
        try: return ImageFont.truetype(path, size)
        except: return ImageFont.load_default()

    def clean(s):
        """Remove markdown and extra symbols."""
        s = re.sub(r'[*]+', '', s)
        s = re.sub(r'[_]+', '', s)
        s = re.sub(r'[\[\]()]', '', s)
        s = re.sub(r'^[|#\-\s]+', '', s)
        s = re.sub(r'[|]+$', '', s)
        return s.strip()

    # ── Parse days ──
    day_keys = {
        "ПОНЕДЕЛЬНИК":"ПН","ВТОРНИК":"ВТ","СРЕДА":"СР","ЧЕТВЕРГ":"ЧТ",
        "ПЯТНИЦА":"ПТ","СУББОТА":"СБ","ВОСКРЕСЕНЬЕ":"ВС",
    }
    days = []
    current = None
    for line in program.split("\n"):
        s = line.strip()
        if not s: continue
        up = s.upper()

        # Check day headers
        matched = None
        for key, short in day_keys.items():
            if key in up:
                matched = (short, clean(s)[:40])
                break
        # Also catch "Тренировка N" and "День N"
        if not matched:
            m = re.search(r'(ТРЕНИРОВКА|ДЕНЬ)\s*(\d+)', up)
            if m:
                matched = (f"Т{m.group(2)}", clean(s)[:40])

        if matched:
            if current and current["lines"]:
                days.append(current)
            current = {"short": matched[0], "title": matched[1], "lines": []}
        elif current:
            c = clean(s)
            # Skip separator lines and empty
            if c and len(c) > 4 and not re.match(r'^[-=_]+$', c):
                current["lines"].append(c[:80])

    if current and current["lines"]:
        days.append(current)

    if not days:
        return None

    days = days[:7]

    # ── Layout ──
    W, H = 1080, 1920
    RED   = (220, 16, 16)
    BLACK = (13, 13, 13)
    WHITE = (255, 255, 255)
    DARK  = (22, 22, 22)
    CARD1 = (32, 32, 32)
    CARD2 = (38, 38, 38)
    GRAY  = (160, 160, 160)
    LGRAY = (220, 220, 220)
    PAD   = 52

    img = Image.new("RGB", (W, H), DARK)
    draw = ImageDraw.Draw(img)

    # Fonts
    f_header = gf(font_bold_path, 58)
    f_sub    = gf(font_reg_path,  30)
    f_badge  = gf(font_bold_path, 32)
    f_title  = gf(font_bold_path, 26)
    f_line   = gf(font_reg_path,  25)
    f_footer = gf(font_reg_path,  23)

    # ── HEADER ──
    HDR = 170
    draw.rectangle([0, 0, W, HDR], fill=BLACK)
    draw.rectangle([0, 0, 10, HDR], fill=RED)
    name = client.get("name", "Атлет")
    week = client.get("current_week", 1)
    draw.text((PAD, 30), "PIONEER ONLINE", font=f_header, fill=RED)
    draw.text((PAD, 110), f"{name}  ·  Неделя {week}", font=f_sub, fill=GRAY)

    # ── DAYS ──
    FOOTER_H = 80
    available = H - HDR - FOOTER_H - PAD
    n = len(days)
    # Dynamic card height - fill all space
    CARD_H = (available - (n - 1) * 14) // n
    CARD_H = max(CARD_H, 180)

    y = HDR + PAD // 2

    for i, day in enumerate(days):
        bg = CARD1 if i % 2 == 0 else CARD2
        cx1, cy1, cx2, cy2 = PAD, y, W - PAD, y + CARD_H

        # Card background
        draw.rectangle([cx1, cy1, cx2, cy2], fill=bg)
        # Red left stripe
        draw.rectangle([cx1, cy1, cx1 + 8, cy2], fill=RED)

        # Badge (fixed width 110px)
        BW = 110
        draw.rectangle([cx1 + 14, cy1 + 12, cx1 + 14 + BW, cy1 + 58], fill=RED)
        draw.text((cx1 + 22, cy1 + 16), day["short"], font=f_badge, fill=WHITE)

        # Day title (next to badge)
        title = day["title"]
        # Remove the short name from title if it starts with it
        for key in day_keys:
            if key in title.upper():
                idx = title.upper().find(key)
                title = title[idx + len(key):].strip(" —:-")
                break
        title = re.sub(r'^(ТРЕНИРОВКА|ДЕНЬ)\s*\d+', '', title, flags=re.I).strip(" :—-")
        if title:
            draw.text((cx1 + 14 + BW + 16, cy1 + 22), title[:35], font=f_title, fill=LGRAY)

        # Exercises
        max_lines = min(len(day["lines"]), (CARD_H - 80) // 36)
        ey = cy1 + 70
        for j, ln in enumerate(day["lines"][:max_lines]):
            draw.ellipse([cx1 + 24, ey + 9, cx1 + 34, ey + 19], fill=RED)
            draw.text((cx1 + 44, ey), ln[:75], font=f_line, fill=LGRAY)
            ey += 36

        if len(day["lines"]) > max_lines:
            draw.text((cx1 + 24, ey), f"+ ещё {len(day['lines']) - max_lines} упр.", font=f_footer, fill=GRAY)

        y += CARD_H + 14

    # ── FOOTER ──
    draw.rectangle([0, H - FOOTER_H, W, H], fill=BLACK)
    draw.rectangle([0, H - FOOTER_H, 10, H], fill=RED)
    draw.text((PAD, H - FOOTER_H + 24), "Зал Пионер · с 2014 · 2000+ результатов", font=f_footer, fill=GRAY)
    draw.text((W - PAD - 240, H - FOOTER_H + 24), "pioneer-online.ru", font=f_footer, fill=(60,60,60))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.read()


async def cmd_card(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Отправляет шпаргалку текущей недели как фото."""
    uid = update.effective_user.id
    c = get_client(uid)

    if not c.get("program"):
        await update.message.reply_text(
            "Программы пока нет — сначала пройди анкету и получи план.",
            reply_markup=main_kb()
        )
        return

    await ctx.bot.send_chat_action(chat_id=uid, action=ChatAction.UPLOAD_PHOTO)

    card = make_week_card(c)
    if card:
        week = c.get("current_week", 1)
        name = c.get("name", "Атлет")
        await update.message.reply_photo(
            photo=card,
            caption=f"📋 *{name} · Неделя {week}*\n\nСохрани и тренируйся без чата 💪",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            "Не смог создать карточку — попробуй /card чуть позже.",
        )

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    tg    = update.effective_user
    data  = load_data()
    is_new = str(uid) not in data

    c = get_client(uid)
    c["history"] = []; c["step"] = 0
    c["tg_name"]     = tg.full_name or ""
    c["tg_username"] = f"@{tg.username}" if tg.username else "нет username"
    if is_new:
        c["joined_at"] = datetime.now().isoformat()
    save_client(uid, c)

    name = tg.first_name or ""
    greeting = f"Привет, {name}! 👋" if name else "Привет! 👋"

    await update.message.reply_text(
        f"{greeting}\n\n"
        "Это *Pioneer Online* — команда зала Пионер в твоём телефоне.\n\n"
        "Составлю программу, буду вести дневник и разбирать каждую неделю вместе с тобой.\n\n"
        "С какого вида спорта начнём?",
        parse_mode=ParseMode.MARKDOWN, reply_markup=sport_kb()
    )

    # Уведомление владельцу о новом пользователе
    if is_new and ADMIN_ID:
        total = len(load_data())
        try:
            await ctx.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"🆕 *Новый пользователь Pioneer Online!*\n\n"
                    f"👤 {tg.full_name or '—'}\n"
                    f"🔗 {'@'+tg.username if tg.username else 'нет username'}\n"
                    f"🆔 `{uid}`\n"
                    f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
                    f"👥 Всего пользователей: *{total}*"
                ),
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            log.warning(f"Уведомление админу не отправлено: {e}")

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏆 *Pioneer Online — справка*\n\n"
        "📓 *Записать тренировку* — фиксируй после каждой: самочувствие, RPE, веса, заметки\n\n"
        "📊 *Анализ недели* — в конце недели получи разбор и рекомендации тренера\n\n"
        "📈 *Мой прогресс* — динамика за всё время: нагрузки, усталость, тренды\n\n"
        "📋 *Мои записи* — последние 5 записей дневника\n\n"
        "➡️ *Следующая неделя* — получить план следующей недели\n\n"
        "⚙️ *Корректировка* — сообщи что изменилось, скорректирую программу\n\n"
        "🔄 *Начать заново* — новая анкета и новая программа\n\n"
        "─────────────────\n"
        "💡 *Совет:* записывай тренировку сразу после — занимает 1 минуту, "
        "а анализ в конце недели будет точнее.\n\n"
        "🏅 Зал Пионер · с 2014 года · 2000+ результатов",
        parse_mode=ParseMode.MARKDOWN, reply_markup=main_kb()
    )

async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    c   = get_client(uid)
    c["history"] = []; c["step"] = 0; c["program"] = None; c["current_week"] = 1
    save_client(uid, c)
    await update.message.reply_text(
        "🔄 *Программа очищена. Начинаем заново!*\n\nВыбери вид спорта:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=sport_kb()
    )

async def cmd_adjust(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚙️ *Корректировка программы*\n\n"
        "Расскажи что изменилось:\n"
        "• Травма или боль?\n"
        "• Изменился график?\n"
        "• Программа слишком лёгкая или тяжёлая?\n"
        "• Не растут веса?\n"
        "• Что-то ещё?\n\n"
        "Опиши — скорректирую программу.",
        parse_mode=ParseMode.MARKDOWN, reply_markup=ReplyKeyboardRemove()
    )

async def cmd_logview(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    c = get_client(update.effective_user.id)
    entries = c.get("log", [])
    if not entries:
        await update.message.reply_text(
            "📋 Дневник пустой.\n\nПосле тренировки нажми 📓 *Записать тренировку*.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=main_kb()
        )
        return
    text = "📋 *Последние тренировки:*\n\n" + "\n\n─────────────\n\n".join(
        fmt(e) for e in entries[-5:][::-1]
    )
    await reply(update, text, kb=main_kb())

async def cmd_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    c    = get_client(uid)
    week = c.get("current_week", 1)
    logs = [e for e in c.get("log", []) if e.get("week") == week]
    if not logs:
        await update.message.reply_text(
            f"📊 За неделю {week} нет записей.\n\n"
            "После каждой тренировки нажимай 📓 *Записать тренировку*.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=main_kb()
        )
        return
    await ctx.bot.send_chat_action(chat_id=uid, action=ChatAction.TYPING)
    feels = [float(e["feeling"]) for e in logs if str(e.get("feeling","")).replace(".","").isdigit()]
    rpes  = [float(e["rpe"])     for e in logs if str(e.get("rpe","")).replace(".","").isdigit()]
    summary = (
        f"📊 *Сводка недели {week}* | {c.get('name','Атлет')}\n\n" +
        "\n\n".join(fmt(e) for e in logs) +
        f"\n\n{'─'*16}\n"
        f"📈 Тренировок: *{len(logs)}*\n" +
        (f"📈 Ср. самочувствие: *{sum(feels)/len(feels):.1f}/10*\n" if feels else "") +
        (f"📈 Ср. RPE: *{sum(rpes)/len(rpes):.1f}/10*" if rpes else "")
    )
    await reply(update, summary)
    await update.message.reply_text("🔍 _Анализирую дневник..._", parse_mode=ParseMode.MARKDOWN)
    prog = f"\nПрограмма:\n{c['program'][:1500]}" if c.get("program") else ""
    prompt = (
        f"Атлет: {c.get('name','—')}\nНеделя: {week}{prog}\n\n"
        f"Дневник:\n" + "\n\n".join(fmt(e) for e in logs) +
        "\n\nАнализ:\n1. *Общая оценка* — выполнение плана\n"
        "2. *Позитивные моменты*\n3. *Зоны внимания*\n"
        "4. *Рекомендации на следующую неделю* с конкретными цифрами\n"
        "5. *Один вопрос* атлету"
    )
    try:
        analysis = claude([{"role":"user","content":prompt}], system=SYSTEM_ANALYSIS)
        await reply(update, f"🤖 *Анализ тренера:*\n\n{analysis}", kb=main_kb())
    except Exception as e:
        log.error(f"Week analysis error: {e}")
        await update.message.reply_text("⚠️ Ошибка анализа. Попробуй позже.", reply_markup=main_kb())

async def cmd_nextweek(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    c    = get_client(uid)
    old  = c.get("current_week", 1)
    c["current_week"] = old + 1
    save_client(uid, c)
    await update.message.reply_text(
        f"➡️ *Неделя {old+1}!* Составляю план...",
        parse_mode=ParseMode.MARKDOWN, reply_markup=main_kb()
    )
    await ctx.bot.send_chat_action(chat_id=uid, action=ChatAction.TYPING)
    history = c.get("history", [])
    history.append({"role":"user","content":f"Составь план недели {old+1}."})
    try:
        r = claude(history)
        history.append({"role":"assistant","content":r})
        c["history"] = history[-40:]
        save_client(uid, c)
        await reply(update, r)
    except Exception as e:
        log.error(f"Next week error: {e}")
        await update.message.reply_text("⚠️ Ошибка. Попробуй ещё раз.", reply_markup=main_kb())

async def cmd_progress(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    c    = get_client(uid)
    logs = c.get("log", [])
    if len(logs) < 2:
        await update.message.reply_text(
            "📈 Пока мало данных (нужно минимум 2 записи).\n\n"
            "После каждой тренировки нажимай 📓 *Записать тренировку*.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=main_kb()
        )
        return
    await ctx.bot.send_chat_action(chat_id=uid, action=ChatAction.TYPING)
    by_week: dict[int,list] = {}
    for e in logs:
        by_week.setdefault(e.get("week",1),[]).append(e)
    lines = [f"📈 *Прогресс* | {c.get('name','Атлет')}\n"]
    for w in sorted(by_week.keys()):
        es    = by_week[w]
        feels = [float(e["feeling"]) for e in es if str(e.get("feeling","")).replace(".","").isdigit()]
        rpes  = [float(e["rpe"])     for e in es if str(e.get("rpe","")).replace(".","").isdigit()]
        lines.append(
            f"*Неделя {w}:* {len(es)} трен. | "
            f"😴 {f'{sum(feels)/len(feels):.1f}' if feels else '—'} | "
            f"💪 RPE {f'{sum(rpes)/len(rpes):.1f}' if rpes else '—'}"
        )
    await reply(update, "\n".join(lines))
    await update.message.reply_text("🔍 _Анализирую прогресс..._", parse_mode=ParseMode.MARKDOWN)
    prog = f"\nПрограмма:\n{c['program'][:1000]}" if c.get("program") else ""
    prompt = (
        f"Атлет: {c.get('name','—')}\nНеделя: {c.get('current_week',1)}{prog}\n\n"
        f"Все записи:\n" + "\n\n".join(fmt(e) for e in logs[-20:]) +
        "\n\nАнализ прогресса:\n1. *Динамика самочувствия и RPE*\n"
        "2. *Прогресс в нагрузках*\n3. *Качество восстановления*\n"
        "4. *Оценка адаптации*\n5. *Рекомендации*"
    )
    try:
        analysis = claude([{"role":"user","content":prompt}], system=SYSTEM_ANALYSIS)
        await reply(update, f"🤖 *Анализ прогресса:*\n\n{analysis}", kb=main_kb())
    except Exception as e:
        log.error(f"Progress error: {e}")
        await update.message.reply_text("⚠️ Ошибка анализа. Попробуй позже.", reply_markup=main_kb())

async def cmd_clients(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Команда только для тренера.")
        return
    data = load_data()
    if not data:
        await update.message.reply_text("Клиентов пока нет.")
        return
    lines = [f"*👥 Пользователи Pioneer Online* — {len(data)} чел.\n"]
    for uid, c in data.items():
        name     = c.get("name","—")
        tg_user  = c.get("tg_username","")
        joined   = c.get("joined_at","")[:10] if c.get("joined_at") else "—"
        msgs     = len(c.get("history",[]))
        logs     = len(c.get("log",[]))
        week     = c.get("current_week",1)
        sport_map = {"pl":"ПЛ","bb":"ББ","cf":"CF","cy":"Цикл","ma":"Едино"}
        sport    = sport_map.get(c.get("sport",""),"—")
        lines.append(
            f"• *{name}* {tg_user}\n"
            f"  {sport} | сообщ: {msgs} | трен: {logs} | нед: {week} | с {joined}"
        )
    # Разбиваем на чанки если много пользователей
    text = "\n\n".join(lines)
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Команда только для тренера.")
        return
    data = load_data()
    total = len(data)
    if not total:
        await update.message.reply_text("Пользователей пока нет.")
        return

    # Подсчёт статистики
    active   = sum(1 for c in data.values() if len(c.get("history",[])) > 2)
    with_log = sum(1 for c in data.values() if len(c.get("log",[])) > 0)
    total_tr = sum(len(c.get("log",[])) for c in data.values())
    total_ms = sum(len(c.get("history",[])) for c in data.values())

    sports = {"pl":0,"bb":0,"cf":0,"cy":0,"ma":0}
    for c in data.values():
        s = c.get("sport","")
        if s in sports: sports[s] += 1

    sport_names = {"pl":"Пауэрлифтинг","bb":"Бодибилдинг","cf":"Кроссфит",
                   "cy":"Циклические","ma":"Единоборства"}

    sport_lines = "\n".join(
        f"  {sport_names[k]}: {v}" for k,v in sports.items() if v > 0
    )

    # Новые за последние 7 дней
    from datetime import timedelta
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    new_week = sum(
        1 for c in data.values()
        if c.get("joined_at","") > week_ago
    )

    await update.message.reply_text(
        f"📊 *Pioneer Online — Статистика*\n"
        f"_{datetime.now().strftime('%d.%m.%Y %H:%M')}_\n\n"
        f"👥 *Пользователей всего:* {total}\n"
        f"✅ *Активных (прошли анкету):* {active}\n"
        f"📓 *Ведут дневник:* {with_log}\n"
        f"🆕 *Новых за 7 дней:* {new_week}\n\n"
        f"💬 *Всего сообщений:* {total_ms}\n"
        f"🏋 *Всего тренировок записано:* {total_tr}\n\n"
        f"🏅 *По видам спорта:*\n{sport_lines}",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_client(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Просмотр карточки конкретного клиента. Только для тренера.
    Использование: /client 123456789
    Или без ID — показывает список с ID для выбора."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Команда только для тренера.")
        return

    data = load_data()
    args = ctx.args  # аргументы после команды

    # Без аргумента — показать список с ID
    if not args:
        if not data:
            await update.message.reply_text("Клиентов пока нет.")
            return
        lines = ["👤 *Выбери клиента:*\n", "Напиши `/client ID` чтобы открыть карточку\n"]
        for uid, c in data.items():
            name    = c.get("name", "Новый клиент")
            tg_user = c.get("tg_username", "")
            sport_map = {"pl":"🏋 ПЛ/ТА","bb":"💪 ББ","cf":"⚡ CF","cy":"🚴 Цикл","ma":"🥊 Едино"}
            sport   = sport_map.get(c.get("sport",""), "—")
            logs    = len(c.get("log", []))
            week    = c.get("current_week", 1)
            lines.append(f"• *{name}* {tg_user}\n  ID: `{uid}` | {sport} | трен: {logs} | нед: {week}")
        text = "\n\n".join(lines)
        for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
            await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
        return

    # С аргументом — показать карточку клиента
    target_id = args[0].strip()
    c = data.get(target_id)
    if not c:
        await update.message.reply_text(
            f"❌ Клиент с ID `{target_id}` не найден.\n\nНапиши /client чтобы увидеть список.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    sport_map = {"pl":"🏋 Пауэрлифтинг/ТА","bb":"💪 Бодибилдинг","cf":"⚡ Кроссфит",
                 "cy":"🚴 Циклические","ma":"🥊 Единоборства"}
    sport    = sport_map.get(c.get("sport",""), "—")
    name     = c.get("name", "—")
    tg_user  = c.get("tg_username", "нет username")
    joined   = c.get("joined_at","")[:10] if c.get("joined_at") else "—"
    week     = c.get("current_week", 1)
    logs     = c.get("log", [])
    history  = c.get("history", [])
    program  = c.get("program", "")

    # Считаем средние показатели
    all_feels = [float(e["feeling"]) for e in logs if str(e.get("feeling","")).replace(".","").isdigit()]
    all_rpes  = [float(e["rpe"])     for e in logs if str(e.get("rpe","")).replace(".","").isdigit()]
    avg_feel  = f"{sum(all_feels)/len(all_feels):.1f}" if all_feels else "—"
    avg_rpe   = f"{sum(all_rpes)/len(all_rpes):.1f}"   if all_rpes  else "—"

    # Карточка клиента
    card = (
        f"👤 *Карточка клиента*\n\n"
        f"*Имя:* {name}\n"
        f"*Telegram:* {tg_user}\n"
        f"*ID:* `{target_id}`\n"
        f"*В боте с:* {joined}\n\n"
        f"*Вид спорта:* {sport}\n"
        f"*Текущая неделя:* {week}\n"
        f"*Сообщений:* {len(history)}\n\n"
        f"📓 *Дневник тренировок:* {len(logs)} записей\n"
        f"😴 Ср. самочувствие: {avg_feel}/10\n"
        f"💪 Ср. RPE: {avg_rpe}/10\n"
    )
    await update.message.reply_text(card, parse_mode=ParseMode.MARKDOWN)

    # Последние 3 тренировки
    if logs:
        last3 = logs[-3:][::-1]
        trn_text = "📋 *Последние тренировки:*\n\n" + "\n\n".join(fmt(e) for e in last3)
        for chunk in [trn_text[i:i+4000] for i in range(0, len(trn_text), 4000)]:
            try:
                await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
            except:
                await update.message.reply_text(chunk)

    # Текущая программа
    if program:
        prog_preview = program[:3000] + ("..." if len(program) > 3000 else "")
        prog_text = f"📋 *Программа тренировок:*\n\n{prog_preview}"
        for chunk in [prog_text[i:i+4000] for i in range(0, len(prog_text), 4000)]:
            try:
                await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
            except:
                await update.message.reply_text(chunk)
    else:
        await update.message.reply_text("📋 Программа ещё не составлена.")

    # AI-анализ клиента если есть данные
    if logs:
        await update.message.reply_text("🔍 _Формирую тренерский анализ..._", parse_mode=ParseMode.MARKDOWN)
        prog_ctx = f"\nПрограмма:\n{program[:800]}" if program else ""
        prompt = (
            f"Атлет: {name} | Вид спорта: {sport} | Неделя: {week}{prog_ctx}\n\n"
            f"Дневник тренировок (последние записи):\n" +
            "\n\n".join(fmt(e) for e in logs[-10:]) +
            "\n\nДай краткий тренерский анализ:\n"
            "1. *Общий прогресс* — как идут дела\n"
            "2. *На что обратить внимание* — риски, проблемы\n"
            "3. *Рекомендация тренеру* — что скорректировать в программе"
        )
        try:
            analysis = claude([{"role":"user","content":prompt}], system=SYSTEM_ANALYSIS)
            await update.message.reply_text(
                f"🤖 *Тренерский анализ:*\n\n{analysis}",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            log.error(f"Client analysis error: {e}")

# ── NUTRITION ConversationHandler ─────────────────────────────────────────────
def activity_kb():
    return ReplyKeyboardMarkup([
        ["🪑 Сидячая работа, мало движения"],
        ["🚶 1–3 тренировки в неделю"],
        ["🏃 3–5 тренировок в неделю"],
        ["💪 6–7 тренировок или тяжёлый физтруд"],
        ["🔥 2× в день, профи уровень"],
    ], resize_keyboard=True, one_time_keyboard=True)

def goal_kb():
    return ReplyKeyboardMarkup([
        ["📉 Похудение / сушка"],
        ["📈 Набор мышечной массы"],
        ["⚖️ Рекомпозиция (масса без жира)"],
        ["🔄 Поддержание формы"],
        ["🏆 Подготовка к соревнованиям"],
    ], resize_keyboard=True, one_time_keyboard=True)

def sex_kb():
    return ReplyKeyboardMarkup([
        ["👨 Мужчина", "👩 Женщина"]
    ], resize_keyboard=True, one_time_keyboard=True)

async def cmd_nutrition(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    kb = ReplyKeyboardMarkup(
        [["Набор массы", "Сушка / похудение"],
         ["Рекомпозиция", "Поддержание формы"],
         ["Отмена"]],
        resize_keyboard=True, one_time_keyboard=True
    )
    await update.message.reply_text(
        "Нутрициолог Pioneer Online\n\nКакая цель по питанию?",
        reply_markup=kb
    )
    return NUT_GOAL

async def nut_goal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "Отмена":
        await update.message.reply_text("Отменено.", reply_markup=main_kb())
        return ConversationHandler.END
    ctx.user_data["nut_goal"] = update.message.text
    await update.message.reply_text(
        "Цель: " + update.message.text + "\n\n"
        "Данные для расчёта — напиши через запятую:\n"
        "возраст, пол (м/ж), рост (см), вес (кг), тренировок в неделю\n\n"
        "Пример: 28, м, 180, 85, 4",
        reply_markup=ReplyKeyboardRemove()
    )
    return NUT_DATA

async def nut_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    import re as re2
    text = update.message.text.strip()
    parts = [p.strip() for p in re2.split(r'[,;]', text)]
    if len(parts) < 5:
        await update.message.reply_text(
            "Нужно 5 значений: возраст, пол, рост, вес, тренировок/нед\n"
            "Пример: 28, м, 180, 85, 4"
        )
        return NUT_DATA
    ctx.user_data["nut_params"] = text
    await update.message.reply_text(
        "Почти готово!\n\n"
        "Есть ли аллергии, непереносимость продуктов или ограничения в питании?\n\n"
        "Если нет — напиши «нет»"
    )
    return NUT_PREFS

async def nut_prefs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    c = get_client(uid)
    await ctx.bot.send_chat_action(chat_id=uid, action=ChatAction.TYPING)

    goal   = ctx.user_data.get("nut_goal", "")
    params = ctx.user_data.get("nut_params", "")
    prefs  = update.message.text

    if "nutrition" not in c:
        c["nutrition"] = {}
    c["nutrition"] = {"goal": goal, "params": params, "prefs": prefs}

    sport_map = {"pl":"пауэрлифтинг/ТА","bb":"бодибилдинг","cf":"кроссфит",
                 "cy":"бег/вело/плавание","ma":"единоборства"}
    sport_ctx = sport_map.get(c.get("sport",""), "не указан")

    prompt = (
        "Рассчитай КБЖУ и составь рекомендации по питанию.\n\n"
        "Цель: " + goal + "\n"
        "Данные: " + params + " (возраст, пол, рост, вес, тренировок/нед)\n"
        "Ограничения: " + prefs + "\n"
        "Вид спорта: " + sport_ctx + "\n\n"
        "Сделай:\n"
        "1. Расчёт BMR и TDEE с формулой и цифрами\n"
        "2. Целевой калораж с обоснованием\n"
        "3. КБЖУ в граммах\n"
        "4. Распределение по приёмам пищи\n"
        "5. 3-5 ключевых правил для этой цели\n"
        "6. Добавки с доказательной базой\n\n"
        "Конкретно, с цифрами. Без воды."
    )

    try:
        hist = [{"role":"user","content":prompt}]
        result = claude(hist, system=SYSTEM_NUTRITION)
        c["nutrition"]["kbju_result"] = result
        c["nutrition_history"] = [{"role":"user","content":prompt},{"role":"assistant","content":result}]
        save_client(uid, c)

        for chunk in [result[i:i+4000] for i in range(0, len(result), 4000)]:
            try:
                await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
            except:
                await update.message.reply_text(chunk)

        await update.message.reply_text(
            "Хочешь меню на неделю под этот КБЖУ?\nНапиши /nutmenu",
            reply_markup=ReplyKeyboardMarkup(
                [["Меню на неделю", "Пересчитать КБЖУ"]],
                resize_keyboard=True, one_time_keyboard=True
            )
        )
    except Exception as e:
        log.error(f"Nutrition error: {e}")
        await update.message.reply_text("Ошибка расчёта. Попробуй ещё раз.", reply_markup=main_kb())

    return ConversationHandler.END

async def nut_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.", reply_markup=main_kb())
    return ConversationHandler.END


async def cmd_nutmenu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    c = get_client(uid)
    nut = c.get("nutrition", {})

    if not nut.get("kbju_result"):
        await update.message.reply_text(
            "Сначала рассчитай КБЖУ — используй /nutrition",
            reply_markup=main_kb()
        )
        return

    await ctx.bot.send_chat_action(chat_id=uid, action=ChatAction.TYPING)
    await update.message.reply_text("Составляю меню на неделю...")

    kbju  = nut.get("kbju_result","")[:1500]
    goal  = nut.get("goal","")
    prefs = nut.get("prefs","нет")
    sport_map = {"pl":"пауэрлифтинг/ТА","bb":"бодибилдинг","cf":"кроссфит",
                 "cy":"бег/вело/плавание","ma":"единоборства"}
    sport = sport_map.get(c.get("sport",""), "")

    prompt = (
        "Составь подробное меню на 7 дней.\n\n"
        "Цель: " + goal + "\n"
        "Ограничения: " + prefs + "\n"
        "Вид спорта: " + sport + "\n\n"
        "Расчёт КБЖУ:\n" + kbju + "\n\n"
        "Требования:\n"
        "- Каждый день: завтрак, обед, ужин, 1-2 перекуса\n"
        "- Каждое блюдо: название, граммовка ингредиентов, КБЖУ\n"
        "- Итог дня: суммарные К/Б/Ж/У\n"
        "- Не повторять блюда больше 2 раз за неделю\n"
        "- Реальные доступные продукты\n"
        "- В тренировочные дни больше углеводов до/после тренировки\n\n"
        "Формат: День 1...День 7. Компактно но полно."
    )

    try:
        hist = c.get("nutrition_history", [])
        hist.append({"role":"user","content":prompt})
        result = claude(hist[-10:], system=SYSTEM_NUTRITION)
        hist.append({"role":"assistant","content":result})
        c["nutrition_history"] = hist[-20:]
        c["nutrition"]["menu"] = result
        save_client(uid, c)

        for chunk in [result[i:i+4000] for i in range(0, len(result), 4000)]:
            try:
                await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
            except:
                await update.message.reply_text(chunk)

        await update.message.reply_text(
            "Меню на неделю готово! Скажи если хочешь скорректировать любой день или блюдо.",
            reply_markup=main_kb()
        )
    except Exception as e:
        log.error(f"Menu error: {e}")
        await update.message.reply_text("Ошибка при составлении меню. Попробуй позже.", reply_markup=main_kb())



# ── /log — ConversationHandler ─────────────────────────────────────────────
async def cmd_log_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    week = get_client(update.effective_user.id).get("current_week", 1)
    await update.message.reply_text(
        f"📓 *Запись тренировки* (Неделя {week})\n\nКакой сегодня день?",
        parse_mode=ParseMode.MARKDOWN, reply_markup=day_kb()
    )
    return LOG_DAY

async def log_day(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in ("❌ Отмена","отмена"):
        await update.message.reply_text("Отменено.", reply_markup=main_kb())
        return ConversationHandler.END
    ctx.user_data["log_day"] = update.message.text
    await update.message.reply_text(
        "😴 *Самочувствие перед тренировкой* (1–10)\n\n1–3 плохо · 4–6 нормально · 7–10 хорошо",
        parse_mode=ParseMode.MARKDOWN, reply_markup=feel_kb()
    )
    return LOG_FEEL

async def log_feel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["log_feel"] = update.message.text
    await update.message.reply_text(
        "💪 *Средний RPE тренировки* (5–10)\n\n5–6 лёгкая · 7–8 умеренная · 9–10 очень тяжёлая",
        parse_mode=ParseMode.MARKDOWN, reply_markup=rpe_kb()
    )
    return LOG_RPE

async def log_rpe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["log_rpe"] = update.message.text
    await update.message.reply_text(
        "🏋 *Ключевые нагрузки*\n\n"
        "Напиши упражнения и веса:\n"
        "_Присед 3×5 × 140 кг, Жим 4×6 × 90 кг_\n\n"
        "Для кардио: _Бег 12 км, темп 5:10_",
        parse_mode=ParseMode.MARKDOWN, reply_markup=ReplyKeyboardRemove()
    )
    return LOG_WEIGHTS

async def log_weights(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["log_weights"] = update.message.text
    await update.message.reply_text(
        "📝 *Заметки* — что получилось, что нет, боли:\n\n_Если всё стандартно — напиши «нет»_",
        parse_mode=ParseMode.MARKDOWN
    )
    return LOG_NOTES

async def log_notes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    c   = get_client(uid)
    entry = {
        "date":     datetime.now().strftime("%d.%m.%Y"),
        "time":     datetime.now().strftime("%H:%M"),
        "week":     c.get("current_week", 1),
        "day_name": ctx.user_data.get("log_day","—"),
        "feeling":  ctx.user_data.get("log_feel","—"),
        "rpe":      ctx.user_data.get("log_rpe","—"),
        "weights":  ctx.user_data.get("log_weights","—"),
        "notes":    update.message.text,
    }
    c.setdefault("log",[]).append(entry)
    save_client(uid, c)
    await update.message.reply_text(
        f"✅ *Записано!*\n\n{fmt(entry)}\n\n"
        f"В конце недели нажми 📊 *Анализ недели* — получишь разбор и рекомендации.",
        parse_mode=ParseMode.MARKDOWN, reply_markup=main_kb()
    )
    return ConversationHandler.END

async def log_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.", reply_markup=main_kb())
    return ConversationHandler.END

# ── Главный обработчик сообщений ───────────────────────────────────────────
async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    text = update.message.text.strip()
    c    = get_client(uid)

    # Проверяем нажатие кнопки
    cmd = resolve_alias(text)
    if cmd == "log":      return await cmd_log_start(update, ctx)
    if cmd == "card":     return await cmd_card(update, ctx)
    if cmd == "nutrition": return await cmd_nutrition(update, ctx)
    if cmd == "nutmenu":  return await cmd_nutmenu(update, ctx)
    if cmd == "nutrition": return await cmd_nutrition(update, ctx)
    if cmd == "nutmenu":   return await cmd_nutmenu(update, ctx)
    if cmd == "week":     return await cmd_week(update, ctx)
    if cmd == "progress": return await cmd_progress(update, ctx)
    if cmd == "logview":  return await cmd_logview(update, ctx)
    if cmd == "nextweek": return await cmd_nextweek(update, ctx)
    if cmd == "adjust":   return await cmd_adjust(update, ctx)
    if cmd == "reset":    return await cmd_reset(update, ctx)
    if cmd == "help":     return await cmd_help(update, ctx)

    await ctx.bot.send_chat_action(chat_id=uid, action=ChatAction.TYPING)

    history = c.get("history", [])
    prefix  = (
        "[Это первое сообщение. Поприветствуй и начни анкету — спроси имя.]\n\n"
        if not history else ""
    )
    history.append({"role":"user","content": prefix + text})

    try:
        r = claude(history)
    except Exception as e:
        log.error(f"Claude error {uid}: {e}")
        await update.message.reply_text("⚠️ Ошибка соединения. Попробуй ещё раз.", reply_markup=main_kb())
        return

    history.append({"role":"assistant","content":r})

    # Сохранить имя
    m = re.search(r"меня зовут\s+([А-ЯЁа-яё][а-яё]{2,15})\b", text, re.I)
    if m and c.get("name") == "Новый клиент":
        if not re.search(r"весов|категор|спорт|программ", m.group(1), re.I):
            c["name"] = m.group(1)

    # Сохранить первую программу
    if not c.get("program") and len(r) > 500 and "неделя" in r.lower():
        c["program"] = r

    c["step"]    = min(len([h for h in history if h["role"]=="user"]), 6)
    c["history"] = history[-40:]
    save_client(uid, c)

    await reply(update, r)

# ── Запуск ─────────────────────────────────────────────────────────────────
def main():
    if "ВАШ" in TELEGRAM_TOKEN:
        print("❌ Укажи TELEGRAM_TOKEN в .env!"); return
    if "ВАШ" in ANTHROPIC_KEY:
        print("❌ Укажи ANTHROPIC_KEY в .env!"); return

    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    log_conv = ConversationHandler(
        entry_points=[
            CommandHandler("log", cmd_log_start),
            MessageHandler(filters.Regex(r"(?i)записать"), cmd_log_start),
        ],
        states={
            LOG_DAY:     [MessageHandler(filters.TEXT & ~filters.COMMAND, log_day)],
            LOG_FEEL:    [MessageHandler(filters.TEXT & ~filters.COMMAND, log_feel)],
            LOG_RPE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, log_rpe)],
            LOG_WEIGHTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, log_weights)],
            LOG_NOTES:   [MessageHandler(filters.TEXT & ~filters.COMMAND, log_notes)],
        },
        fallbacks=[
            CommandHandler("cancel", log_cancel),
            MessageHandler(filters.Regex(r"❌"), log_cancel),
        ],
    )

    app.add_handler(log_conv)
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("reset",    cmd_reset))
    app.add_handler(CommandHandler("week",     cmd_week))
    app.add_handler(CommandHandler("progress", cmd_progress))
    app.add_handler(CommandHandler("logview",  cmd_logview))
    app.add_handler(CommandHandler("nextweek", cmd_nextweek))
    app.add_handler(CommandHandler("adjust",   cmd_adjust))
    app.add_handler(CommandHandler("clients",  cmd_clients))
    app.add_handler(CommandHandler("stats",    cmd_stats))
    app.add_handler(CommandHandler("client",   cmd_client))
    app.add_handler(CommandHandler("card",     cmd_card))
    app.add_handler(CommandHandler("nutmenu",  cmd_nutmenu))

    nut_conv = ConversationHandler(
        entry_points=[
            CommandHandler("nutrition", cmd_nutrition),
            MessageHandler(filters.Regex(r"(?i)(питание|кбжу|нутрицио|меню на неделю|пересчитать кбжу)"), cmd_nutrition),
        ],
        states={
            NUT_GOAL:  [MessageHandler(filters.TEXT & ~filters.COMMAND, nut_goal)],
            NUT_DATA:  [MessageHandler(filters.TEXT & ~filters.COMMAND, nut_data)],
            NUT_PREFS: [MessageHandler(filters.TEXT & ~filters.COMMAND, nut_prefs)],
        },
        fallbacks=[CommandHandler("cancel", nut_cancel)],
    )
    app.add_handler(nut_conv)
    app.add_handler(CommandHandler("nutrition", cmd_nutrition))
    app.add_handler(CommandHandler("nutmenu",   cmd_nutmenu))

    nut_conv = ConversationHandler(
        entry_points=[
            CommandHandler("nutrition", cmd_nutrition),
            MessageHandler(filters.Regex(r"(?i)(питание|кбжу|нутрициолог|🥗)"), cmd_nutrition),
        ],
        states={
            NUT_SEX:      [MessageHandler(filters.TEXT & ~filters.COMMAND, nut_sex)],
            NUT_AGE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, nut_age)],
            NUT_WEIGHT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, nut_weight)],
            NUT_HEIGHT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, nut_height)],
            NUT_ACTIVITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, nut_activity)],
            NUT_GOAL:     [MessageHandler(filters.TEXT & ~filters.COMMAND, nut_goal)],
            NUT_PREFS:    [MessageHandler(filters.TEXT & ~filters.COMMAND, nut_prefs)],
        },
        fallbacks=[CommandHandler("cancel", nut_cancel)],
    )
    app.add_handler(nut_conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("🚀 Pioneer Online — Telegram Bot запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
