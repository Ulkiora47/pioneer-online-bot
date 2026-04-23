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

# ── Системные промпты ──────────────────────────────────────────────────────
SYSTEM_MAIN = """Ты — профессиональный тренировочный ассистент зала Пионер (Pioneer Online).

О зале: Пионер работает с 2014 года. За это время более 2000 человек достигли результатов — от первых успехов в фитнесе до разрядов мастера спорта. Ты являешься цифровым продолжением экспертизы зала и его тренеров.

СПЕЦИАЛИЗАЦИИ: пауэрлифтинг/ТА, бодибилдинг/фитнес, кроссфит, циклические виды (бег/вело/плавание/триатлон), единоборства.

СТИЛЬ ОБЩЕНИЯ: профессиональный, но живой и поддерживающий. Ты на стороне атлета. Отмечай прогресс, поддерживай мотивацию, указывай на риски без запугивания.

АЛГОРИТМ АНКЕТЫ — СТРОГО СОБЛЮДАЙ. По одному шагу:
Шаг 0: спроси имя атлета.
Шаг 1: вид спорта и главная цель на 3–6 мес.
Шаг 2: стаж, уровень, рекорды, что работало/нет, перерывы.
Шаг 3: возраст, пол, рост/вес, травмы.
Шаг 4: оборудование, дней в неделю, время.
Шаг 5: работа, стресс, сон, питание.
Шаг 6: специфические метрики (1ПМ, FTP, VDOT, бенчмарки).
Только после шага 6 — составляй программу.

СПЕЦИФИКА:
ПАУЭРЛИФТИНГ/ТА: блоковая периодизация (ACC→TRA→REA→пик), RPE, 1ПМ/Wilks, разгрузка каждые 4 нед.
БОДИБИЛДИНГ: двойная прогрессия, 6–20 повт, 10–20 сетов/группа/нед, сплиты по уровню.
КРОССФИТ: сопряжённая периодизация, 3 модальности, 3 энергосистемы, масштабирование.
ЦИКЛИЧЕСКИЕ: 80/20, FTP/VDOT/CSS, 4 периода, правило 10%.
ЕДИНОБОРСТВА: ОФП подчинена технике, взрывная→анаэробная→аэробная, в сезоне −60%.

ФОРМАТ ПРОГРАММЫ: одна неделя за раз.
В конце: "✅ Неделя N готова. Нажми ➡️ Следующая неделя чтобы продолжить."
Отвечай по-русски. Telegram Markdown: *жирный*, _курсив_."""

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
            ["➡️ Следующая неделя",      "⚙️ Корректировка"],
        ],
        resize_keyboard=True,
        is_persistent=True,
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
    ])

# ── Команды ────────────────────────────────────────────────────────────────
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
        "🏆 *Добро пожаловать в Pioneer Online —*\n"
        "персональный тренировочный ассистент зала *Пионер*.\n\n"
        "С 2014 года зал Пионер помог более *2000 человек* достичь своих целей — "
        "от первых результатов в фитнесе до разрядов мастера спорта.\n\n"
        "Теперь весь этот опыт доступен тебе в этом боте:\n"
        "📋 Персональная программа тренировок\n"
        "📓 Дневник и анализ каждой тренировки\n"
        "📊 Еженедельный разбор с рекомендациями тренера\n"
        "📈 Отслеживание прогресса в динамике\n\n"
        "Кнопки внизу доступны в любой момент 👇\n\n"
        "Выбери свой вид спорта — начнём:",
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
        await reply(update, r, kb=main_kb())
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

    await reply(update, r, kb=main_kb())

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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("🚀 Pioneer Online — Telegram Bot запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
