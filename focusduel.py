#!/usr/bin/env python3
"""
Focus Duel (соло-версия) — личная статистика работы поверх ActivityWatch.

Команды:
  python3 focusduel.py             — из любой папки, кроме ~/.focusduel: мастер установки (то же, что install)
  python3 focusduel.py install     — установить и запустить в фоне (автозапуск при входе)
      без вопросов: install --name "Имя" [--lang ru|kk] [--goal 6] [--hours 9-19] [--saturday yes|no] [--code КОД]
  python3 focusduel.py check       — проверить, что ActivityWatch видит окна (после «Универсального доступа»)
  python3 focusduel.py status      — самопроверка: что работает, что нет и как починить
  python3 focusduel.py update      — обновиться с GitHub сейчас; updates on|off — включить/выключить автообновление
  python3 focusduel.py work Telegram figma.com — отметить программы/сайты как работу (notwork — как не работу)
  python3 focusduel.py uninstall   — удалить
  python3 focusduel.py serve       — запустить вручную (без установки)
  python3 focusduel.py preview     — показать цифры за сегодня (то, что считается)
  python3 focusduel.py telegram-test — отправить тестовую сводку в Telegram
  python3 focusduel.py duel-create — создать группу для дуэли, напечатать код для друга
  python3 focusduel.py duel-join КОД — вступить в группу друга
  python3 focusduel.py telegram-setup — подключить бота: сводка дуэли в общий чат в 21:00
  python3 focusduel.py duel-page   — выгрузить страницу дуэли для хостинга (смотреть с телефона)

Дашборд: http://localhost:5700
Данные никуда не отправляются. Исключение — если вы сами включите Telegram-сводку:
тогда раз в день в Telegram уходят только итоговые цифры.
Только стандартная библиотека Python 3.8+.
"""
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

APP_DIR = os.path.expanduser("~/.focusduel")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
STATE_PATH = os.path.join(APP_DIR, "state.json")
HISTORY_PATH = os.path.join(APP_DIR, "history.json")  # дни до ActivityWatch (например, из Rize): дата → часы работы
PLIST_PATH = os.path.expanduser("~/Library/LaunchAgents/com.focusduel.plist")
LABEL = "com.focusduel"
VERSION = "2026.10.17"  # меняется с каждым выпуском на GitHub
APPS_DIR = "/Applications"
AW_DOWNLOAD_URL = "https://activitywatch.net/downloads/"
AW_RELEASES_API = "https://api.github.com/repos/ActivityWatch/activitywatch/releases/latest"
ACCESSIBILITY_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
# Браузер без адреса сайта — непонятно, работа это или нет, такое не спрашиваем
BROWSERS = {"Safari", "Google Chrome", "Arc", "Firefox", "Yandex", "Opera", "Brave Browser",
            "Microsoft Edge", "Chromium", "Vivaldi", "Orion", "Comet", "Dia"}

DEFAULT_CONFIG = {
    "user_name": "",  # спрашивается при установке
    "lang": "ru",  # язык программы и страниц: "ru" или "kk"
    "aw_url": "http://localhost:5600",
    "port": 5700,
    "focus_gap_seconds": 120,
    "goal_hours": 6,
    # Рабочие часы: дни недели (0 = понедельник … 6 = воскресенье) и время.
    "work_schedule": {"days": [0, 1, 2, 3, 4, 5], "start": "06:00", "end": "19:00"},
    # Правила проверяются сверху вниз, срабатывает первое совпадение.
    # Ищется в названии приложения, заголовке окна и адресе сайта (без учёта регистра).
    # type:
    #   work        — работа
    #   rest        — отдых всегда (кино, сериалы, музыка, игры)
    #   fun         — соцсети/YouTube: в рабочие часы = отвлечение, вне их = отдых
    #   distraction — отвлечение всегда
    #   neutral     — прочее
    # afk_minutes: сколько минут без клавиатуры/мыши ещё засчитывать (смотришь видео).
    # Дольше — считается, что ты отошёл. По умолчанию 0 (только активное время).
    # Здесь только то, что у всех одинаково. Что считать работой, человек отмечает сам
    # на дашборде («Это работа?» → Да/Нет), ответы ложатся в "answers".
    "rules": [
        {"name": "Кино и сериалы", "type": "rest", "afk_minutes": 180, "regex": "kinopoisk|кинопоиск|netflix|ivi\\.ru|okko|rezka|kion|start\\.ru|wink\\.ru|amediateka|primevideo|disneyplus|hbomax|max\\.com|anime|IINA|VLC|Infuse|^TV$|сериал|смотреть онлайн|watch online"},
        {"name": "Музыка", "type": "rest", "regex": "music\\.youtube|spotify|soundcloud|yandex\\.\\w+/music|^Music$|Музыка"},
        {"name": "Игры", "type": "rest", "regex": "^steam$|steampowered|epic games|chess\\.com|lichess|crazygames|poki\\.com"},
        {"name": "Соцсети и YouTube", "type": "fun", "afk_minutes": 20, "regex": "instagram|youtube\\.com|youtu\\.be|tiktok|twitch|vk\\.com|pinterest|reddit|x\\.com|twitter|threads"},
    ],
    # Ответы на «Это работа?»: программа или сайт → "work" / "no". Важнее правил.
    "answers": {},
    "ignore_apps": ["loginwindow", "ScreenSaverEngine"],
    "telegram": {"enabled": False, "bot_token": "", "chat_id": "", "send_at": "21:00"},
    # Дуэль: общая база в Supabase. Адрес и publishable-ключ публичные, они и так видны на странице дуэли.
    # invite_code / member_id / token появляются после создания группы или вступления в неё.
    "duel": {"supabase_url": "https://gcbxwggscuutktzetrrt.supabase.co",
             "supabase_key": "sb_publishable_nrOX7qiOldp7TX0PrC2Dfg_JSnQ0voG",
             "page_url": "https://daniyalkay.github.io/focusduel/",
             "invite_code": "", "member_id": "", "token": ""},
    # Автообновление: свежий focusduel.py с GitHub. Включается только с согласия человека при установке.
    # У автора (там, где программу правят) выключено — иначе GitHub затрёт свежие правки.
    "updates": {"auto": False, "url": "https://raw.githubusercontent.com/daniyalkay/focusduel/main/focusduel.py"},
}


# ---------------------------------------------------------------- config

def load_config():
    if not os.path.exists(CONFIG_PATH):
        os.makedirs(APP_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    # пустые поля дуэли берём из программы: так у друга сразу правильный адрес базы
    cfg["duel"] = dict(DEFAULT_CONFIG["duel"], **{k: v for k, v in cfg["duel"].items() if v})
    cfg["updates"] = dict(DEFAULT_CONFIG["updates"], **cfg["updates"])
    defaults = {r["name"]: r for r in DEFAULT_CONFIG["rules"]}
    for r in cfg["rules"]:
        if "afk_minutes" not in r and r.get("name") in defaults:
            r["afk_minutes"] = defaults[r["name"]].get("afk_minutes", 0)
        r["_re"] = re.compile(r["regex"], re.I)
    return cfg


_cfg_lock = threading.RLock()
_state_lock = threading.Lock()


def update_config(change):
    """Программная правка config.json: читаем файл как есть, меняем нужное, пишем атомарно."""
    with _cfg_lock:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        change(raw)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def update_state(**kv):
    with _state_lock:
        s = load_state()
        s.update(kv)
        os.makedirs(APP_DIR, exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(s, f)


# ---------------------------------------------------------------- ActivityWatch

def parse_ts(s):
    s = s.replace("Z", "+00:00")
    # Python 3.8 fromisoformat не любит больше 6 знаков после точки
    m = re.match(r"(.*\.\d{6})\d*(.*)", s)
    if m:
        s = m.group(1) + m.group(2)
    return datetime.fromisoformat(s)


def aw_get(cfg, path):
    with urllib.request.urlopen(cfg["aw_url"] + path, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def aw_alive(cfg):
    try:
        aw_get(cfg, "/api/0/info")
        return True
    except Exception:
        return False


def aw_app():
    """Где лежит ActivityWatch.app (обычно в «Программах»), или None."""
    for d in (APPS_DIR, os.path.expanduser("~/Applications")):
        p = os.path.join(d, "ActivityWatch.app")
        if os.path.exists(p):
            return p
    return None


def find_buckets(cfg):
    buckets = aw_get(cfg, "/api/0/buckets/")
    win = [b for b in buckets if b.startswith("aw-watcher-window")]
    afk = [b for b in buckets if b.startswith("aw-watcher-afk")]
    if not win or not afk:
        raise RuntimeError("Не найдены бакеты aw-watcher-window / aw-watcher-afk. ActivityWatch запущен?")
    # если устройств несколько — берём самые свежие
    pick = lambda xs: max(xs, key=lambda b: buckets[b].get("last_updated") or "")
    return pick(win), pick(afk)


def fetch_events(cfg, bucket, start, end):
    q = urllib.parse.urlencode({"start": start.isoformat(), "end": end.isoformat(), "limit": -1})
    return aw_get(cfg, "/api/0/buckets/%s/events?%s" % (urllib.parse.quote(bucket), q))


# ---------------------------------------------------------------- analysis

def item_of(data):
    """То, про что спрашиваем «это работа?»: сайт, если это браузер с адресом, иначе программа."""
    url = data.get("url") or ""
    if url.startswith(("http://", "https://")):
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host:
            return host
    return data.get("app", "")


def answer_for(cfg, item):
    """Ответ «это работа?» без учёта регистра; для сайта годится и ответ про домен выше: figma.com → app.figma.com."""
    answers = dict((k.lower(), v) for k, v in cfg["answers"].items())
    key = item.lower()
    while key not in answers and key.count(".") >= 2:
        key = key.split(".", 1)[1]
    return answers.get(key)


def classify(cfg, data):
    item = item_of(data)
    answer = answer_for(cfg, item)
    if answer == "work":
        return item, "work", 0
    if answer == "no":
        return "Прочее", "neutral", 0
    hay = " ".join([data.get("app", ""), data.get("title", ""), data.get("url", "")])
    app = data.get("app", "")
    for r in cfg["rules"]:
        if r["_re"].search(app) or r["_re"].search(hay):
            return r["name"], r["type"], r.get("afk_minutes", 0)
    return "Прочее", "neutral", 0


def site_key(data):
    url = data.get("url") or ""
    if url:
        try:
            return data.get("app", "") + "|" + urllib.parse.urlparse(url).netloc
        except Exception:
            pass
    return data.get("app", "")


def merge(intervals):
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def in_work_hours(cfg, dt):
    ws = cfg["work_schedule"]
    hm = dt.strftime("%H:%M")
    return dt.weekday() in ws["days"] and ws["start"] <= hm < ws["end"]


def compute_segments(cfg, win_events, afk_events, local_tz):
    """Окна, обрезанные по времени, когда человек за компьютером. Только локально.
    Для видео засчитываются ещё afk_minutes после последнего касания клавиатуры/мыши:
    фильм смотрят без ввода, но окно с YouTube, забытое на ночь, не превращается в 8 часов."""
    active = merge([
        (parse_ts(e["timestamp"]), parse_ts(e["timestamp"]) + timedelta(seconds=e["duration"]))
        for e in afk_events if e["data"].get("status") == "not-afk" and e["duration"] > 0
    ])
    allowed_cache = {}

    def allowed(minutes):
        if minutes <= 0:
            return active
        if minutes not in allowed_cache:
            ext = [(x, y + timedelta(minutes=minutes)) for x, y in active]
            allowed_cache[minutes] = merge(ext)
        return allowed_cache[minutes]

    segs = []
    ignore = set(cfg["ignore_apps"])
    for e in win_events:
        d = e["data"]
        if d.get("app") in ignore or e["duration"] <= 0:
            continue
        a = parse_ts(e["timestamp"])
        b = a + timedelta(seconds=e["duration"])
        name, typ, afk_min = classify(cfg, d)
        key = site_key(d)
        item = item_of(d)
        for x, y in allowed(afk_min):
            if y <= a:
                continue
            if x >= b:
                break
            s, f = max(a, x), min(b, y)
            if f > s:
                ls = s.astimezone(local_tz)
                t, n = typ, name
                if typ == "fun":
                    t = "distraction" if in_work_hours(cfg, ls) else "rest"
                    n = name + (" (в работу)" if t == "distraction" else "")
                segs.append((ls, f.astimezone(local_tz), n, t, key, item))
    segs.sort(key=lambda z: z[0])
    return segs


def split_by_day(segs):
    out = []
    for s, f, n, t, k, item in segs:
        while s.date() != f.date():
            midnight = datetime.combine(s.date() + timedelta(days=1), datetime.min.time(), tzinfo=s.tzinfo)
            out.append((s, midnight, n, t, k, item))
            s = midnight
        if f > s:
            out.append((s, f, n, t, k, item))
    return out


def day_stats(cfg, segs):
    if not segs:
        return None
    tot = {"work": 0.0, "rest": 0.0, "distraction": 0.0, "neutral": 0.0}
    cats = {}
    hourly = [0.0] * 24
    switches = 0
    prev_key = None
    longest = 0.0
    cur_block = 0.0
    last_end = None
    gap_limit = cfg["focus_gap_seconds"]
    pending_break = 0.0
    for s, f, n, t, k, _ in segs:
        sec = (f - s).total_seconds()
        tot[t] += sec
        c = cats.setdefault(n, {"sec": 0.0, "type": t})
        c["sec"] += sec
        if t == "work":
            # раскладываем по часам
            cur = s
            while cur < f:
                nxt = min(f, cur.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
                hourly[cur.hour] += (nxt - cur).total_seconds()
                cur = nxt
        if sec >= 1:
            if prev_key is not None and k != prev_key:
                switches += 1
            prev_key = k
        # самый длинный фокус-блок
        idle = (s - last_end).total_seconds() if last_end else 0
        if t == "work":
            if idle + pending_break > gap_limit:
                cur_block = 0.0
            pending_break = 0.0
            cur_block += sec
            longest = max(longest, cur_block)
        else:
            pending_break += sec + idle
            if pending_break > gap_limit:
                cur_block = 0.0
        last_end = f
    active = sum(tot.values())
    return {
        "active_sec": round(active),
        "work_sec": round(tot["work"]),
        "distraction_sec": round(tot["distraction"]),
        "rest_sec": round(tot["rest"]),
        "neutral_sec": round(tot["neutral"]),
        "categories": sorted(
            [{"name": n, "type": v["type"], "sec": round(v["sec"])} for n, v in cats.items()],
            key=lambda x: -x["sec"]),
        "hourly_work": [round(h) for h in hourly],
        "switches": switches,
        "longest_focus_sec": round(longest),
        "first_active": segs[0][0].strftime("%H:%M"),
        "last_active": segs[-1][1].strftime("%H:%M"),
    }


def compute(cfg, days=35, win_events=None, afk_events=None, now=None):
    local_tz = datetime.now().astimezone().tzinfo
    now = now or datetime.now(local_tz)
    start_day = (now - timedelta(days=days - 1)).date()
    start = datetime.combine(start_day, datetime.min.time(), tzinfo=local_tz)
    if win_events is None:
        wb, ab = find_buckets(cfg)
        win_events = fetch_events(cfg, wb, start.astimezone(timezone.utc), now.astimezone(timezone.utc))
        afk_events = fetch_events(cfg, ab, start.astimezone(timezone.utc), now.astimezone(timezone.utc))
    segs = split_by_day(compute_segments(cfg, win_events, afk_events, local_tz))
    by_day = {}
    for seg in segs:
        if seg[0].date() >= start_day:
            by_day.setdefault(seg[0].date().isoformat(), []).append(seg)
    result = []
    for i in range(days):
        d = (start_day + timedelta(days=i)).isoformat()
        st = day_stats(cfg, by_day.get(d, []))
        result.append(dict({"date": d}, **(st or {
            "active_sec": 0, "work_sec": 0, "distraction_sec": 0, "rest_sec": 0, "neutral_sec": 0,
            "categories": [], "hourly_work": [0] * 24, "switches": 0, "longest_focus_sec": 0,
            "first_active": None, "last_active": None})))
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            history = json.load(f)
    except Exception:
        history = {}
    for day in result:
        h = history.get(day["date"])
        if h and not day["active_sec"]:  # ActivityWatch за этот день ничего не знает — берём перенесённое
            w = int(h["work_sec"])
            day.update(work_sec=w, active_sec=w, imported=h.get("source", "импорт"),
                       categories=[{"name": "%s (импорт)" % h.get("source", "История"), "type": "work", "sec": w}])
    return {"user": cfg["user_name"], "goal_hours": cfg["goal_hours"],
            "generated_at": now.strftime("%H:%M"), "today": now.date().isoformat(), "days": result,
            "suggestions": suggestions(cfg, segs, (now - timedelta(days=6)).date()),
            "answers": [{"item": k, "answer": v} for k, v in sorted(cfg["answers"].items(), key=lambda x: x[0].lower())]}


def suggestions(cfg, segs, since):
    """Программы и сайты без категории, на которые ушло больше всего времени, — их спрашиваем «это работа?».
    Только для дашборда на этом Mac."""
    tot = {}
    for s, f, n, t, k, item in segs:
        if n == "Прочее" and s.date() >= since and item and answer_for(cfg, item) is None and item not in BROWSERS:
            tot[item] = tot.get(item, 0) + (f - s).total_seconds()
    top = sorted(tot.items(), key=lambda x: -x[1])
    return [{"item": i, "sec": round(v)} for i, v in top if v >= 180][:8]


# ---------------------------------------------------------------- telegram

def fmt_h(sec):
    h, m = divmod(int(sec) // 60, 60)
    return ("%d ч %02d мин" % (h, m)) if h else ("%d мин" % m)


def summary_text(data):
    days = data["days"]
    t = days[-1]
    week = days[-7:]
    base = t["work_sec"] + t["distraction_sec"]
    pct = round(100 * t["distraction_sec"] / base) if base else 0
    avg7 = sum(d["work_sec"] for d in days[-8:-1]) / 7
    diff = t["work_sec"] - avg7
    sign = "+" if diff >= 0 else "−"
    lines = [
        "📊 Итоги дня — %s" % data["user"],
        "Работа: %s (%s%s к среднему за неделю)" % (fmt_h(t["work_sec"]), sign, fmt_h(abs(diff))),
        "Самый длинный фокус: %s" % fmt_h(t["longest_focus_sec"]),
        "Отвлечения в рабочее время: %d%% (%s) · переключений: %d" % (pct, fmt_h(t["distraction_sec"]), t["switches"]),
        "Отдых: %s" % fmt_h(t["rest_sec"]),
    ]
    top = [c for c in t["categories"] if c["type"] == "work"][:3]
    if top:
        lines.append("Топ: " + ", ".join("%s %s" % (c["name"], fmt_h(c["sec"])) for c in top))
    lines.append("За 7 дней: %s" % fmt_h(sum(d["work_sec"] for d in week)))
    return "\n".join(lines)


def tg_api(bot_token, method, params=None):
    body = urllib.parse.urlencode(params or {}).encode()
    try:
        with urllib.request.urlopen("https://api.telegram.org/bot%s/%s" % (bot_token, method), data=body, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))["result"]
    except urllib.error.HTTPError as ex:
        err = json.loads(ex.read().decode("utf-8") or "{}")
        raise TelegramError(err.get("description") or str(ex), err.get("parameters") or {})


class TelegramError(Exception):
    def __init__(self, msg, params):
        Exception.__init__(self, msg)
        self.params = params


def telegram_send(cfg, text):
    tg = cfg["telegram"]
    tg_api(tg["bot_token"], "sendMessage", {"chat_id": tg["chat_id"], "text": text})
    return True


def telegram_loop():
    while True:
        try:
            cfg = load_config()
            tg = cfg["telegram"]
            if tg.get("enabled") and tg.get("bot_token") and tg.get("chat_id"):
                now = datetime.now()
                if now.strftime("%H:%M") >= tg.get("send_at", "21:00") and load_state().get("tg_sent") != now.date().isoformat():
                    if telegram_send(cfg, summary_text(compute(cfg, days=8))):
                        update_state(tg_sent=now.date().isoformat())
        except Exception as ex:
            log("telegram: %s" % ex)
        time.sleep(60)


# ---------------------------------------------------------------- duel (Supabase)

MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
              "сентября", "октября", "ноября", "декабря"]
DUEL_FIELDS = ("date", "active_sec", "work_sec", "distraction_sec", "rest_sec", "neutral_sec",
               "longest_focus_sec", "switches", "hourly_work", "first_active", "last_active")


def duel_ready(cfg):
    d = cfg["duel"]
    return bool(d["supabase_url"] and d["supabase_key"])


def sb_rpc(cfg, fn, params):
    """Вызов функции из supabase.sql. Ключ publishable — публичный, права дают только сами функции."""
    d = cfg["duel"]
    headers = {"Content-Type": "application/json", "apikey": d["supabase_key"]}
    if d["supabase_key"].startswith("eyJ"):  # старый anon-ключ — это JWT, его шлют ещё и так
        headers["Authorization"] = "Bearer " + d["supabase_key"]
    req = urllib.request.Request(d["supabase_url"].rstrip("/") + "/rest/v1/rpc/" + fn,
                                 data=json.dumps(params, ensure_ascii=False).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8")
    except urllib.error.HTTPError as ex:
        err = json.loads(ex.read().decode("utf-8") or "{}")
        raise RuntimeError(err.get("message") or str(ex))
    return json.loads(body) if body else None


def duel_save(info):
    """Запоминаем группу и личный токен после создания/вступления."""
    def change(raw):
        raw.setdefault("duel", {}).update(invite_code=info["invite_code"], member_id=info["member_id"],
                                          token=info["token"])
    update_config(change)


def duel_sync(cfg, days):
    """Отправляем в общую базу только цифры по дням. Категории не шлём: в них бывают названия сайтов."""
    data = compute(cfg, days=days)
    payload = [{k: d[k] for k in DUEL_FIELDS} for d in data["days"]]
    sb_rpc(cfg, "submit_stats", {"p_token": cfg["duel"]["token"], "p_name": cfg["user_name"],
                                 "p_goal": cfg["goal_hours"], "p_days": payload})
    update_state(duel_synced=data["today"], duel_ok_at=time.time(), duel_error="")


def ru_date(iso):
    d = datetime.strptime(iso, "%Y-%m-%d")
    return "%d %s" % (d.day, MONTHS_GEN[d.month - 1])


def duel_summary(data, day, page_url=""):
    members = data["members"]
    by = dict(((s["member_id"], s["date"]), s) for s in data["stats"])
    get = lambda m, d: by.get((m["id"], d)) or {}
    order = sorted(members, key=lambda m: -get(m, day).get("work_sec", 0))
    lines = ["📊 Итоги %s" % ru_date(day)]
    for i, m in enumerate(order):
        s = get(m, day)
        w, dist = s.get("work_sec", 0), s.get("distraction_sec", 0)
        pct = round(100 * dist / (w + dist)) if w + dist else 0
        goal = " ✅" if w >= float(m["goal_hours"]) * 3600 else ""
        if not s:
            lines.append("%s %s — нет данных за сегодня (программа не на связи)" % ((["🥇", "🥈", "🥉"] + ["▫️"] * 5)[i], m["name"]))
            continue
        lines.append("%s %s — %s%s · фокус %s · отвлечения %d%%" % (
            (["🥇", "🥈", "🥉"] + ["▫️"] * 5)[i], m["name"], fmt_h(w), goal, fmt_h(s.get("longest_focus_sec", 0)), pct))
    # неделя с понедельника: кто сколько дней выиграл и сколько часов
    d0 = datetime.strptime(day, "%Y-%m-%d")
    week = [(d0 - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(d0.weekday() + 1)]
    wins = dict((m["id"], 0) for m in members)
    for d in week:
        top = sorted(members, key=lambda m: -get(m, d).get("work_sec", 0))
        if len(top) > 1 and get(top[0], d).get("work_sec", 0) - get(top[1], d).get("work_sec", 0) >= 60:
            wins[top[0]["id"]] += 1
    total = lambda m: sum(get(m, d).get("work_sec", 0) for d in week)
    if len(members) == 2:
        a, b = members
        lines.append("Неделя: %s %d : %d %s (%s / %s)" % (a["name"], wins[a["id"]], wins[b["id"]], b["name"],
                                                          fmt_h(total(a)), fmt_h(total(b))))
    elif len(members) > 2:
        lines.append("Неделя: " + " · ".join("%s %d" % (m["name"], wins[m["id"]]) for m in members))
    if page_url:
        lines.append(page_url.split("#")[0] + "#g=" + data["group"]["invite_code"])
    return "\n".join(lines)


def duel_telegram(cfg, now):
    """В 21:00 первый, чей Mac включён, забирает день в базе и шлёт сводку в общий чат."""
    today = now.date().isoformat()
    if now.strftime("%H:%M") < cfg["telegram"].get("send_at", "21:00") or load_state().get("duel_tg") == today:
        return
    token = cfg["duel"]["token"]
    s = sb_rpc(cfg, "get_group_settings", {"p_token": token})
    st = s["settings"]
    if st.get("tg_bot_token") and st.get("tg_chat_id") and sb_rpc(cfg, "claim_telegram", {"p_token": token, "p_date": today}):
        try:
            duel_sync(cfg, 1)
            data = sb_rpc(cfg, "get_group_stats", {"p_code": s["invite_code"], "p_from": (now - timedelta(days=7)).date().isoformat(),
                                                   "p_to": today})
            text = duel_summary(data, today, st.get("page_url") or cfg["duel"]["page_url"])
            try:
                tg_api(st["tg_bot_token"], "sendMessage", {"chat_id": st["tg_chat_id"], "text": text})
            except TelegramError as ex:
                new_id = ex.params.get("migrate_to_chat_id")  # группа стала супергруппой — у неё новый id
                if not new_id:
                    raise
                sb_rpc(cfg, "set_group_settings", {"p_token": token, "p_settings": {"tg_chat_id": str(new_id)}})
                tg_api(st["tg_bot_token"], "sendMessage", {"chat_id": new_id, "text": text})
            log("duel: summary sent to Telegram")
        except Exception:
            sb_rpc(cfg, "claim_telegram", {"p_token": token, "p_date": today, "p_release": True})
            raise
    update_state(duel_tg=today)


def duel_loop():
    """Раз в 15 минут — свои цифры в общую базу (после сна Mac — с досылкой до 8 дней), в 21:00 — сводка."""
    last = 0
    while True:
        try:
            cfg = load_config()
            if duel_ready(cfg) and cfg["duel"]["token"]:
                if time.time() - last >= 900:
                    synced = load_state().get("duel_synced")
                    gap = (datetime.now().date() - datetime.strptime(synced, "%Y-%m-%d").date()).days if synced else 7
                    duel_sync(cfg, min(8, gap + 2))
                    last = time.time()
                duel_telegram(cfg, datetime.now())
        except Exception as ex:
            log("duel: %s" % ex)
            update_state(duel_error=str(ex)[:200])
            last = time.time() - 600  # повторим минут через 5
        time.sleep(60)


# ---------------------------------------------------------------- web

_cache = {"t": 0, "data": None}
_lock = threading.Lock()


def get_data():
    with _lock:
        if _cache["data"] is None or time.time() - _cache["t"] > 60:
            _cache["data"] = compute(load_config())
            _cache["t"] = time.time()
        return _cache["data"]


def dashboard_page(cfg):
    d = cfg["duel"]
    duel = ("%s#g=%s&me=%s" % (d["page_url"], d["invite_code"], d["member_id"])
            if d["page_url"] and d["invite_code"] else "/duel")
    return PAGE.replace('/*LANG*/"ru"', json.dumps(cfg["lang"])).replace('/*DUEL*/"/duel"', json.dumps(duel))


def duel_page(cfg):
    """Страница дуэли с настройками Supabase этого Mac. Без них — демо."""
    d = cfg.get("duel") or {}
    boot = {"url": d.get("supabase_url"), "key": d.get("supabase_key"), "code": d.get("invite_code"),
            "me": d.get("member_id"), "lang": cfg["lang"]} if d.get("invite_code") else None
    return DUEL_PAGE.replace("/*BOOT*/null", json.dumps(boot, ensure_ascii=False).replace("</", "<\\/"))


def set_answer(item, answer):
    """Ответ с дашборда: "work", "no" или None (забыть ответ). Меняем в config.json только "answers"."""
    def change(raw):
        answers = raw.setdefault("answers", {})
        if answer:
            answers[item] = answer
        else:
            answers.pop(item, None)
    update_config(change)
    with _lock:
        _cache["data"] = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/api/stats"):
            try:
                self._send(200, json.dumps(get_data(), ensure_ascii=False), "application/json; charset=utf-8")
            except Exception as ex:
                self._send(503, json.dumps({"error": str(ex)}, ensure_ascii=False), "application/json; charset=utf-8")
        elif self.path in ("/", "/index.html"):
            self._send(200, dashboard_page(load_config()), "text/html; charset=utf-8")
        elif self.path == "/duel":
            self._send(200, duel_page(load_config()), "text/html; charset=utf-8")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        # Только JSON и только со своей страницы: чужой сайт в браузере не сможет поменять ответы
        origin = self.headers.get("Origin", "")
        if (self.path != "/api/answer" or not self.headers.get("Content-Type", "").startswith("application/json")
                or (origin and urllib.parse.urlparse(origin).hostname not in ("localhost", "127.0.0.1"))):
            self._send(403, "forbidden", "text/plain")
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8"))
            item, answer = str(body.get("item") or "")[:200], body.get("answer")
            if not item or answer not in ("work", "no", None):
                raise ValueError("bad answer")
            set_answer(item, answer)
            self._send(200, json.dumps(get_data(), ensure_ascii=False), "application/json; charset=utf-8")
        except Exception as ex:
            self._send(400, json.dumps({"error": str(ex)}, ensure_ascii=False), "application/json; charset=utf-8")


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def log(msg):
    os.makedirs(APP_DIR, exist_ok=True)
    p = os.path.join(APP_DIR, "focusduel.log")
    if os.path.exists(p) and os.path.getsize(p) > 1_000_000:
        os.replace(p, p + ".old")
    with open(p, "a", encoding="utf-8") as f:
        f.write("%s %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))


def self_reload_loop():
    """Если файл программы изменился — перезапускаемся сами. Переустановка не нужна."""
    path = os.path.abspath(__file__)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return
    while True:
        time.sleep(10)
        try:
            m = os.path.getmtime(path)
            if m != mtime:
                time.sleep(2)  # даём файлу дописаться
                compile(open(path, encoding="utf-8").read(), path, "exec")  # битый файл — не перезапускаемся
                log("program file changed, restarting")
                os.execv(sys.executable, [sys.executable, path] + sys.argv[1:])
        except SyntaxError as ex:
            log("new version has a syntax error, keeping old one: %s" % ex)
            mtime = m
        except Exception as ex:
            log("reload: %s" % ex)


def aw_keepalive_loop():
    """ActivityWatch не запущен (перезагрузка, случайно закрыли) — запускаем сами, иначе день не посчитается."""
    misses = 0
    while True:
        time.sleep(60)
        try:
            if aw_alive(load_config()):
                misses = 0
                continue
            misses += 1
            app = aw_app()
            if misses >= 2 and app:
                log("ActivityWatch is not running, starting it")
                subprocess.call(["open", "-g", "-a", app])
                misses = 0
        except Exception as ex:
            log("aw keepalive: %s" % ex)


UPDATE_MARK = 'APP_DIR = os.path.expanduser("~/.focusduel")'


def update_check(cfg):
    """Скачиваем focusduel.py с GitHub. Ставим, только если это наша программа и она компилируется;
    прошлую версию кладём рядом (focusduel.py.prev). Перезапуск сделает self_reload_loop."""
    url = cfg["updates"]["url"]
    if not url.startswith("https://raw.githubusercontent.com/"):
        raise RuntimeError("неизвестный адрес обновлений: %s" % url)
    with urllib.request.urlopen(url + "?t=%d" % time.time(), timeout=30) as r:
        new = r.read()
    target = os.path.join(APP_DIR, "focusduel.py")
    with open(target, "rb") as f:
        if f.read() == new:
            return False
    text = new.decode("utf-8")
    if UPDATE_MARK not in text or "def serve():" not in text:
        raise RuntimeError("скачанный файл не похож на Focus Duel")
    compile(text, target, "exec")
    shutil.copy(target, target + ".prev")
    with open(target + ".new", "wb") as f:
        f.write(new)
    os.replace(target + ".new", target)
    log("updated from GitHub")
    return True


def update_loop():
    """Через 5 минут после запуска и дальше раз в 6 часов — если человек согласился на автообновление."""
    time.sleep(300)
    while True:
        try:
            cfg = load_config()
            if cfg["updates"]["auto"] and os.path.realpath(__file__) == os.path.realpath(os.path.join(APP_DIR, "focusduel.py")):
                update_check(cfg)
            update_state(update_checked=time.time())
        except Exception as ex:
            log("update: %s" % ex)
        time.sleep(6 * 3600)


def serve():
    cfg = load_config()
    threading.Thread(target=telegram_loop, daemon=True).start()
    threading.Thread(target=self_reload_loop, daemon=True).start()
    threading.Thread(target=aw_keepalive_loop, daemon=True).start()
    threading.Thread(target=duel_loop, daemon=True).start()
    threading.Thread(target=update_loop, daemon=True).start()
    srv = Server(("127.0.0.1", cfg["port"]), Handler)
    log("started on http://localhost:%d" % cfg["port"])
    print("Focus Duel: http://localhost:%d" % cfg["port"])
    srv.serve_forever()


# ---------------------------------------------------------------- install

PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>%(label)s</string>
  <key>ProgramArguments</key>
  <array><string>%(py)s</string><string>%(script)s</string><string>serve</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
  <key>Nice</key><integer>10</integer>
  <key>StandardOutPath</key><string>%(dir)s/stdout.log</string>
  <key>StandardErrorPath</key><string>%(dir)s/stderr.log</string>
</dict>
</plist>
"""


UI_LANG = "ru"  # язык сообщений установщика; выставляется из config.json
# Установка без вопросов (например, через Claude Code): ответы приходят флагами --name, --lang …
OPTS = {}
INTERACTIVE = True
MSG = {
    "name": ("Как тебя зовут? ", "Атың кім? "),
    "goal": ("Цель: сколько часов работы в день? [6]: ", "Мақсат: күніне неше сағат жұмыс? [6]: "),
    "goal_bad": ("Напиши число часов, например 6.", "Сағат санын жаз, мысалы 6."),
    "fun_note": ("\nСоцсети и YouTube в рабочее время считаются отвлечением, а после — отдыхом.",
                 "\nЖұмыс уақытында әлеуметтік желілер мен YouTube алаңдау болып саналады, одан кейін — демалыс."),
    "hours": ("Рабочее время: с какого по какой час? [9-19]: ", "Жұмыс уақыты: сағат нешеден нешеге дейін? [9-19]: "),
    "hours_bad": ("Напиши два числа через дефис, например 9-19.", "Екі санды сызықшамен жаз, мысалы 9-19."),
    "saturday": ("Суббота рабочая? (да/нет) [нет]: ", "Сенбі жұмыс күні ме? (иә/жоқ) [жоқ]: "),
    "code": ("\nКод группы, который прислал друг (Enter — пропустить): ",
             "\nДосың жіберген топ коды (өткізу үшін — Enter): "),
    "code_bad": ("Не получилось: %s. Проверь код и попробуй ещё раз.", "Болмады: %s. Кодты тексеріп, қайта көр."),
    "joined": ("✅ Ты в группе: %s", "✅ Сен топтасың: %s"),
    "aw_need": ("\nНужен ActivityWatch — бесплатная программа, которая записывает, чем занят Mac. Ставлю.",
                "\nActivityWatch керек — Mac-та немен айналысқаныңды жазатын тегін бағдарлама. Орнатып жатырмын."),
    "aw_dl": ("Скачиваю ActivityWatch %s (%d МБ)…", "ActivityWatch %s жүктеліп жатыр (%d МБ)…"),
    "aw_dl_fail": ("\nНе получилось скачать автоматически (%s).", "\nАвтоматты түрде жүктеу болмады (%s)."),
    "aw_manual": ("Открываю страницу загрузки: скачай версию для macOS и перетащи ActivityWatch в «Программы».",
                  "Жүктеу бетін ашамын: macOS нұсқасын жүктеп, ActivityWatch-ты «Программы» (Applications) қалтасына сүйреп апар."),
    "aw_wait": ("Жду ActivityWatch… (прервать: Ctrl+C)", "ActivityWatch-ты күтіп тұрмын… (тоқтату: Ctrl+C)"),
    "aw_start": ("Запускаю ActivityWatch. Если macOS спросит разрешения — разреши.",
                 "ActivityWatch іске қосылып жатыр. macOS рұқсат сұраса — рұқсат бер."),
    "aw_dead": ("⚠️  ActivityWatch не отвечает. Открой его вручную из «Программ» и запусти установку ещё раз.",
                "⚠️  ActivityWatch жауап бермей тұр. Оны «Программы» қалтасынан өзің аш та, орнатуды қайта іске қос."),
    "perm_need": ("\nActivityWatch нужно разрешение «Универсальный доступ», иначе он не видит окна и сайты.",
                  "\nActivityWatch-қа «Универсальный доступ» (Accessibility) рұқсаты керек, әйтпесе ол терезелер мен сайттарды көрмейді."),
    "perm_open": ("Открываю настройки: включи переключатель у ActivityWatch.",
                  "Баптауларды ашамын: ActivityWatch-тың қосқышын қос."),
    "perm_plus": ("Если его нет в списке — нажми «+» внизу и выбери ActivityWatch в «Программах».",
                  "Егер тізімде жоқ болса — төмендегі «+» батырмасын басып, «Программы» ішінен ActivityWatch-ты таңда."),
    "perm_enter": ("\nКогда включишь — вернись сюда и нажми Enter (или напиши «потом»): ",
                   "\nҚосқан соң осында қайтып, Enter бас (немесе «кейін» деп жаз): "),
    "perm_restart": ("Перезапускаю ActivityWatch…", "ActivityWatch қайта іске қосылып жатыр…"),
    "perm_ok": ("Отлично, ActivityWatch видит окна.", "Керемет, ActivityWatch терезелерді көріп тұр."),
    "perm_still": ("Пока не вижу окон. Проверь, что переключатель у ActivityWatch включён.",
                   "Әзірге терезелер көрінбейді. ActivityWatch қосқышы қосулы екенін тексер."),
    "done": ("\n✅ Готово, %s! Focus Duel работает в фоне и сам запускается после перезагрузки.",
             "\n✅ Дайын, %s! Focus Duel фонда жұмыс істейді және компьютер қайта қосылғанда өзі іске қосылады."),
    "done_url": ("   Дашборд: %s (открываю)", "   Дашборд: %s (ашып жатырмын)"),
    "done_ask": ("   Поработай час-другой и загляни туда: в блоке «Это работа?» отметь Да/Нет —\n"
                 "   так программа узнает, что у тебя работа, а что нет.",
                 "   Бір-екі сағат жұмыс істеп, сонда кір: «Бұл жұмыс па?» блогында Иә/Жоқ белгіле —\n"
                 "   осылай бағдарлама сен үшін не жұмыс, не жұмыс емес екенін біледі."),
    "done_perm": ("\n⚠️  Пока ActivityWatch не видит окна. Включи его в Системных настройках →\n"
                  "   Конфиденциальность и безопасность → Универсальный доступ, потом закрой и открой ActivityWatch.",
                  "\n⚠️  Әзірге ActivityWatch терезелерді көрмейді. Системные настройки → Конфиденциальность и\n"
                  "   безопасность → Универсальный доступ ішінде оны қос, содан кейін ActivityWatch-ты жауып, қайта аш."),
    "perm_later": ("Когда включишь — запусти: python3 ~/.focusduel/focusduel.py check",
                   "Қосқан соң мынаны іске қос: python3 ~/.focusduel/focusduel.py check"),
    "upd_ask": ("\nПолучать обновления автоматически? Новые версии будут сами скачиваться\n"
                "с github.com/daniyalkay/focusduel и запускаться на этом Mac. (да/нет) [да]: ",
                "\nЖаңартуларды автоматты түрде алу керек пе? Жаңа нұсқалар github.com/daniyalkay/focusduel\n"
                "сайтынан өздігінен жүктеліп, осы Mac-та іске қосылады. (иә/жоқ) [иә]: "),
    "aborted": ("\nУстановка прервана. Чтобы продолжить, запусти ту же команду ещё раз.",
                "\nОрнату тоқтатылды. Жалғастыру үшін сол команданы қайта іске қос."),
}


def tr(key, *args):
    text = MSG[key][1 if UI_LANG == "kk" else 0]
    return text % args if args else text


def ask(question, default=""):
    try:
        answer = input(question).strip()
    except EOFError:
        answer = ""
    return answer or default


def wait_until(check, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return True
        time.sleep(2)
    return check()


def setup_config():
    """Первая установка: язык, имя, цель, рабочее время. Если настройки уже есть (обновление) — не трогаем."""
    global UI_LANG
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        if cfg.get("user_name"):
            return
    if not INTERACTIVE:
        UI_LANG = "kk" if OPTS.get("lang", "ru").lower() in ("kk", "kz", "2") else "ru"
        name = OPTS.get("name", "").strip()
        hours = [int(x) for x in re.findall(r"\d+", OPTS.get("hours", "9-19"))]
        try:
            goal = float(OPTS.get("goal", "6").replace(",", "."))
        except ValueError:
            goal = 0
        if not name or not 0.5 <= goal <= 16 or len(hours) != 2 or not 0 <= hours[0] < hours[1] <= 24:
            sys.exit('Нужно: --name "Имя" [--lang ru|kk] [--goal 6] [--hours 9-19] [--saturday yes|no] [--code КОД] [--updates yes|no]')
        saturday = OPTS.get("saturday", "no").lower().startswith(("y", "д", "и", "1", "t"))
    else:
        UI_LANG = "kk" if ask("Язык / Тіл: 1 — Русский, 2 — Қазақша [1]: ", "1").strip().lower() in ("2", "kk", "kz", "қазақша") else "ru"
        name = ""
        while not name:
            name = ask(tr("name"))
        goal = None
        while goal is None:
            raw = ask(tr("goal"), "6").replace(",", ".")
            try:
                goal = float(raw)
                if not 0.5 <= goal <= 16:
                    raise ValueError
            except ValueError:
                print(tr("goal_bad"))
                goal = None
        print(tr("fun_note"))
        hours = None
        while hours is None:
            nums = [int(x) for x in re.findall(r"\d+", ask(tr("hours"), "9-19"))]
            if len(nums) == 2 and 0 <= nums[0] < nums[1] <= 24:
                hours = nums
            else:
                print(tr("hours_bad"))
        saturday = ask(tr("saturday"), "нет").lower().startswith(("д", "y", "и"))
    if INTERACTIVE:
        auto = ask(tr("upd_ask"), "да").lower().startswith(("д", "y", "и"))
    else:
        auto = OPTS.get("updates", "no").lower().startswith(("y", "д", "и", "1", "t"))
    cfg["updates"] = {"auto": auto}
    cfg["lang"] = UI_LANG
    cfg["user_name"] = name
    cfg["goal_hours"] = int(goal) if goal == int(goal) else goal
    cfg["work_schedule"] = {"days": list(range(6 if saturday else 5)),
                            "start": "%02d:00" % hours[0], "end": "%02d:00" % hours[1]}
    os.makedirs(APP_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def download_activitywatch():
    """Скачиваем официальный ActivityWatch с GitHub и кладём в «Программы». Не вышло — None."""
    try:
        arm = subprocess.run(["sysctl", "-n", "hw.optional.arm64"], capture_output=True, text=True).stdout.strip() == "1"
    except Exception:
        arm = platform.machine() == "arm64"
    tmp = tempfile.mkdtemp(prefix="focusduel-")
    mnt = os.path.join(tmp, "mnt")
    try:
        with urllib.request.urlopen(AW_RELEASES_API, timeout=30) as r:
            rel = json.loads(r.read().decode("utf-8"))
        name_re = r"activitywatch-v[\d.]+-macos-%s\.dmg$" % ("arm64" if arm else "x86_64")
        asset = [a for a in rel["assets"] if re.match(name_re, a["name"])][0]
        print(tr("aw_dl", rel["tag_name"], asset["size"] // 1000000))
        dmg = os.path.join(tmp, "activitywatch.dmg")
        with urllib.request.urlopen(asset["browser_download_url"], timeout=60) as r, open(dmg, "wb") as f:
            done = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                print("\r  %d%%" % (100 * done // max(1, asset["size"])), end="", flush=True)
        print()
        subprocess.check_call(["hdiutil", "attach", dmg, "-nobrowse", "-readonly", "-noautoopen", "-quiet",
                               "-mountpoint", mnt])
        try:
            src = [n for n in os.listdir(mnt) if n.endswith(".app") and "activitywatch" in n.lower()][0]
            dest_dir = APPS_DIR if os.access(APPS_DIR, os.W_OK) else os.path.expanduser("~/Applications")
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, "ActivityWatch.app")
            subprocess.check_call(["ditto", os.path.join(mnt, src), dest])
        finally:
            subprocess.call(["hdiutil", "detach", mnt, "-quiet"])
        return dest
    except Exception as ex:
        print(tr("aw_dl_fail", ex))
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ensure_activitywatch(cfg):
    """Ставим ActivityWatch, если его нет, и запускаем, если не запущен. Возвращает путь к приложению."""
    if aw_alive(cfg):
        return aw_app()
    app = aw_app()
    if not app:
        print(tr("aw_need"))
        app = download_activitywatch()
    if not app:
        print(tr("aw_manual"))
        subprocess.call(["open", AW_DOWNLOAD_URL])
        print(tr("aw_wait"))
        while not aw_app():
            time.sleep(3)
        time.sleep(5)  # даём докопироваться
        app = aw_app()
    print(tr("aw_start"))
    subprocess.call(["open", "-a", app])
    if not wait_until(lambda: aw_alive(cfg), 180):
        sys.exit(tr("aw_dead"))
    return app


def watching_windows(cfg):
    """ActivityWatch видит заголовки окон. Без «Универсального доступа» не видит, и YouTube не отличить от работы.
    Пока идёт установка, человек сидит в Терминале — у его окна заголовок есть всегда."""
    try:
        win, _ = find_buckets(cfg)
        now = datetime.now(timezone.utc)
        return any(e["data"].get("title") for e in fetch_events(cfg, win, now - timedelta(minutes=10), now))
    except Exception:
        return False


def restart_activitywatch(cfg, app):
    """После выдачи разрешения ActivityWatch надо перезапустить, иначе он его не заметит."""
    subprocess.call(["pkill", "-f", "ActivityWatch.app/Contents/MacOS/"])
    wait_until(lambda: not aw_alive(cfg), 15)
    subprocess.call(["open", "-a", app])
    wait_until(lambda: aw_alive(cfg), 60)


def ensure_permission(cfg, app):
    if wait_until(lambda: watching_windows(cfg), 20):
        return True
    print(tr("perm_need"))
    print(tr("perm_open"))
    print(tr("perm_plus"))
    subprocess.call(["open", ACCESSIBILITY_URL])
    if not INTERACTIVE:
        print(tr("perm_later"))
        return False
    while True:
        if ask(tr("perm_enter")).lower().startswith(("п", "к")):
            return False
        if app:
            print(tr("perm_restart"))
            restart_activitywatch(cfg, app)
        if wait_until(lambda: watching_windows(cfg), 30):
            print(tr("perm_ok"))
            return True
        print(tr("perm_still"))


def install():
    global UI_LANG
    print("Focus Duel — установка / орнату\n")
    os.makedirs(APP_DIR, exist_ok=True)
    target = os.path.join(APP_DIR, "focusduel.py")
    if os.path.realpath(__file__) != os.path.realpath(target):
        shutil.copy(os.path.abspath(__file__), target)
    setup_config()
    cfg = load_config()
    UI_LANG = cfg["lang"]
    setup_duel(cfg)
    cfg = load_config()
    app = ensure_activitywatch(cfg)
    os.makedirs(os.path.dirname(PLIST_PATH), exist_ok=True)
    with open(PLIST_PATH, "w") as f:
        f.write(PLIST % {"label": LABEL, "py": sys.executable, "script": target, "dir": APP_DIR})
    subprocess.call(["launchctl", "unload", PLIST_PATH], stderr=subprocess.DEVNULL)
    subprocess.check_call(["launchctl", "load", PLIST_PATH])
    sees_windows = ensure_permission(cfg, app)
    url = "http://localhost:%d" % cfg["port"]

    def dashboard_up():
        try:
            urllib.request.urlopen(url, timeout=2).close()
            return True
        except Exception:
            return False

    wait_until(dashboard_up, 15)
    print(tr("done", cfg["user_name"]))
    print(tr("done_url", url))
    print(tr("done_ask"))
    if not sees_windows:
        print(tr("done_perm"))
    subprocess.call(["open", url])


def check():
    """После «Универсального доступа»: перезапускаем ActivityWatch и проверяем, что он видит окна."""
    global UI_LANG
    cfg = load_config()
    UI_LANG = cfg["lang"]
    app = aw_app()
    if not aw_alive(cfg) and app:
        subprocess.call(["open", "-a", app])
        wait_until(lambda: aw_alive(cfg), 60)
    if not wait_until(lambda: watching_windows(cfg), 10) and app:
        print(tr("perm_restart"))
        restart_activitywatch(cfg, app)
        wait_until(lambda: watching_windows(cfg), 30)
    ok = watching_windows(cfg)
    print(tr("perm_ok") if ok else tr("perm_still"))
    sys.exit(0 if ok else 1)


def uninstall():
    if os.path.exists(PLIST_PATH):
        subprocess.call(["launchctl", "unload", PLIST_PATH])
        os.remove(PLIST_PATH)
    print("Удалено из автозапуска. Папка с настройками: %s (удали вручную, если не нужна)." % APP_DIR)


def duel_create():
    cfg = load_config()
    if not duel_ready(cfg):
        sys.exit("В программе не задан адрес общей базы (duel.supabase_url).")
    if cfg["duel"]["token"]:
        print("Ты уже в группе. Код для друга: %s" % cfg["duel"]["invite_code"])
        return
    info = sb_rpc(cfg, "create_group", {"p_member_name": cfg["user_name"], "p_goal": cfg["goal_hours"]})
    duel_save(info)
    duel_sync(load_config(), 8)
    print("✅ Группа создана. Код для друга: %s" % info["invite_code"])


def duel_join(code):
    cfg = load_config()
    info = sb_rpc(cfg, "join_group", {"p_code": code, "p_member_name": cfg["user_name"], "p_goal": cfg["goal_hours"]})
    duel_save(info)
    try:
        duel_sync(load_config(), 8)
    except Exception as ex:
        log("duel: first sync failed: %s" % ex)
    print(tr("joined", ", ".join(info.get("members") or [])))


def setup_duel(cfg):
    """Код группы от друга. Без него всё работает, просто без дуэли."""
    if not duel_ready(cfg) or cfg["duel"]["token"]:
        return
    if not INTERACTIVE:
        if OPTS.get("code"):
            try:
                duel_join(OPTS["code"])
            except Exception as ex:
                print(tr("code_bad", ex))
        return
    while True:
        code = ask(tr("code"))
        if not code:
            return
        try:
            duel_join(code)
            return
        except Exception as ex:
            print(tr("code_bad", ex))


def telegram_setup():
    """Бот для общего чата: человек сам вставляет токен от @BotFather, чат находим сами."""
    cfg = load_config()
    if not cfg["duel"]["token"]:
        sys.exit("Сначала нужна группа: python3 focusduel.py duel-create")
    print("1. В Telegram открой @BotFather → /newbot → придумай имя и username бота.")
    print("   В ответ он пришлёт токен вида 123456789:AAH…")
    bot = None
    while not bot:
        token = ask("Вставь токен бота: ")
        try:
            bot = tg_api(token, "getMe")
        except Exception as ex:
            print("Токен не подошёл (%s)." % ex)
    print("\n2. Создай в Telegram группу, добавь туда друга и бота @%s." % bot["username"])
    print("   Жду, пока бот окажется в группе… (прервать: Ctrl+C)")
    chat, offset = None, 0
    while not chat:
        for u in tg_api(token, "getUpdates", {"offset": offset, "timeout": 25}):
            offset = u["update_id"] + 1
            for key in ("my_chat_member", "message"):
                c = (u.get(key) or {}).get("chat") or {}
                if c.get("type") in ("group", "supergroup"):
                    chat = c
    tg_api(token, "sendMessage", {"chat_id": chat["id"], "text": "Focus Duel подключён ✅ Каждый день в 21:00 здесь будут итоги дня."})
    sb_rpc(cfg, "set_group_settings", {"p_token": cfg["duel"]["token"],
                                       "p_settings": {"tg_bot_token": token, "tg_chat_id": str(chat["id"])}})
    print("✅ Готово: бот пишет в «%s», сводка каждый день в 21:00." % chat.get("title", "группу"))


def icon_png(size):
    """Иконка для экрана «Домой»: два столбика — ты и друг. PNG без сторонних библиотек."""
    import struct
    import zlib
    bg, c1, c2 = (0x0F, 0x11, 0x15), (0x4C, 0x9A, 0xFF), (0xB7, 0x94, 0xF6)
    bars = [(0.25, 0.44, 0.22, c1), (0.56, 0.75, 0.40, c2)]  # x0, x1, верх столбика, цвет
    rows = []
    for y in range(size):
        row = bytearray(b"\x00")
        for x in range(size):
            px = bg
            for x0, x1, top, col in bars:
                if x0 * size <= x < x1 * size and top * size <= y < 0.78 * size:
                    px = col
            row += bytes(px)
        rows.append(bytes(row))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def duel_export():
    """Папка для GitHub: страница дуэли (телефон; код группы — в ссылке после #), иконки и сама программа (автообновление)."""
    cfg = load_config()
    out = os.path.join(APP_DIR, "site")
    os.makedirs(out, exist_ok=True)
    boot = {"url": cfg["duel"]["supabase_url"], "key": cfg["duel"]["supabase_key"], "code": None, "me": None}
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(DUEL_PAGE.replace("/*BOOT*/null", json.dumps(boot)))
    for size in (180, 512):
        with open(os.path.join(out, "icon-%d.png" % size), "wb") as f:
            f.write(icon_png(size))
    with open(os.path.join(out, "manifest.webmanifest"), "w", encoding="utf-8") as f:
        json.dump({"name": "Focus Duel", "short_name": "Focus Duel", "display": "standalone", "start_url": "./",
                   "background_color": "#0F1115", "theme_color": "#0F1115",
                   "icons": [{"src": "icon-512.png", "sizes": "512x512", "type": "image/png"},
                             {"src": "icon-180.png", "sizes": "180x180", "type": "image/png"}]}, f)
    shutil.copy(os.path.abspath(__file__), os.path.join(out, "focusduel.py"))  # для автообновления у друзей
    print("Готово: %s — эти файлы выкладываются в github.com/daniyalkay/focusduel" % out)


def mark(items, answer):
    """«Это работа?» из терминала: python3 focusduel.py work Telegram figma.com — то же, что кнопки на дашборде."""
    done = []
    for raw in items:
        it = raw.strip()
        if "://" in it:
            it = urllib.parse.urlparse(it).hostname or it
        if it.lower().startswith("www."):
            it = it[4:]
        if it:
            set_answer(it, answer)
            done.append(it)
    print(("✅ Работа: %s" if answer == "work" else "✅ Не работа: %s") % ", ".join(done))


def status():
    """Самопроверка: что работает, что нет и как починить. Понятна и человеку, и Claude Code."""
    cfg = load_config()
    problems = []

    def line(good, text, fix=""):
        print(("✅ " if good else "⚠️  ") + text)
        if not good:
            problems.append(text)
            if fix:
                print("   → " + fix)

    line(bool(cfg["user_name"]), "Имя: %s, язык: %s" % (cfg["user_name"] or "—", cfg["lang"]),
         "запусти установку: python3 focusduel.py install --name \"Имя\"")
    app = aw_app()
    line(bool(app), "ActivityWatch установлен", "python3 ~/.focusduel/focusduel.py install")
    alive = aw_alive(cfg)
    line(alive, "ActivityWatch запущен", "открой ActivityWatch из «Программ» или: python3 ~/.focusduel/focusduel.py check")
    if alive:
        line(watching_windows(cfg), "ActivityWatch видит окна",
             "Системные настройки → Конфиденциальность и безопасность → Универсальный доступ → включи ActivityWatch, "
             "потом: python3 ~/.focusduel/focusduel.py check")
    loaded = subprocess.run(["launchctl", "list", LABEL], capture_output=True).returncode == 0
    line(os.path.exists(PLIST_PATH) and loaded, "Автозапуск Focus Duel включён", "python3 ~/.focusduel/focusduel.py install")
    try:
        urllib.request.urlopen("http://localhost:%d" % cfg["port"], timeout=5).close()
        up = True
    except Exception:
        up = False
    line(up, "Дашборд http://localhost:%d отвечает" % cfg["port"], "launchctl kickstart -k gui/$(id -u)/%s" % LABEL)
    works = sum(1 for v in cfg["answers"].values() if v == "work") + sum(1 for r in cfg["rules"] if r.get("type") == "work")
    line(works > 0, "Отмечено рабочих программ и сайтов: %d" % works,
         "отметь на дашборде в блоке «Это работа?» или: python3 ~/.focusduel/focusduel.py work Telegram figma.com")
    if duel_ready(cfg):
        d, st = cfg["duel"], load_state()
        if not d["token"]:
            line(False, "Дуэль: не в группе", "python3 ~/.focusduel/focusduel.py duel-join КОД")
        else:
            ok_at = st.get("duel_ok_at")
            ago = "%d мин назад" % ((time.time() - ok_at) // 60) if ok_at else "ещё ни разу"
            line(bool(ok_at) and time.time() - ok_at < 3600, "Дуэль: группа %s, цифры отправлены %s" % (d["invite_code"], ago),
                 "проверь интернет. Последняя ошибка: %s" % (st.get("duel_error") or "—"))
    print("ℹ️  Версия %s, автообновление %s" % (VERSION, "включено" if cfg["updates"]["auto"] else "выключено"))
    if alive:
        try:
            t = compute(cfg, days=1)["days"][-1]
            print("ℹ️  Сегодня: работа %s, всего за компьютером %s" % (fmt_h(t["work_sec"]), fmt_h(t["active_sec"])))
        except Exception as ex:
            line(False, "Подсчёт за сегодня: %s" % ex)
    print("\nВсё работает." if not problems else "\nНужно поправить: %d" % len(problems))
    sys.exit(1 if problems else 0)


def updates_cmd(arg):
    if arg in ("on", "off"):
        update_config(lambda raw: raw.setdefault("updates", {}).update(auto=(arg == "on")))
        print("Автообновление " + ("включено" if arg == "on" else "выключено"))
        return
    print("✅ Обновлено, перезапуск через несколько секунд" if update_check(load_config()) else "Уже последняя версия")


RIZE_API = "https://api.rize.io/api/v1/graphql"
RIZE_KEY_PATH = os.path.join(APP_DIR, "rize_key")
INTROSPECTION = """query { __schema { queryType { name } types { name kind
  fields { name args { name type { ...T } } type { ...T } } inputFields { name type { ...T } } enumValues { name } } } }
fragment T on __Type { kind name ofType { kind name ofType { kind name ofType { kind name } } } }"""


def rize_query(key, query, variables=None):
    req = urllib.request.Request(RIZE_API, data=json.dumps({"query": query, "variables": variables or {}}).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.loads(r.read().decode("utf-8"))
    if res.get("errors"):
        raise RuntimeError("; ".join(e.get("message", "") for e in res["errors"]))
    return res["data"]


def rize_connect():
    """Ключ Rize человек вставляет сам. Храним только на этом Mac (права 600) и сохраняем схему API —
    по ней видно, какие данные Rize отдаёт. Личных данных в схеме нет."""
    import getpass
    print("Rize → Settings → API → Generate (срок можно поставить короткий) → скопируй ключ.")
    key = getpass.getpass("Вставь API-ключ Rize (символы не будут видны): ").strip()
    rize_query(key, "query { __typename }")
    fd = os.open(RIZE_KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)
    with open(os.path.join(APP_DIR, "rize_schema.json"), "w", encoding="utf-8") as f:
        json.dump(rize_query(key, INTROSPECTION), f)
    print("✅ Rize подключён. Ключ сохранён только на этом Mac: %s" % RIZE_KEY_PATH)


def history_add(args):
    """Перенести дни из другого трекера: history-add 2026-09-29=2.3 2026-09-30=3 --source Rize"""
    source = "История"
    if "--source" in args:
        i = args.index("--source")
        source = args[i + 1]
        args = args[:i] + args[i + 2:]
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            history = json.load(f)
    except Exception:
        history = {}
    for a in args:
        day, hours = a.split("=")
        datetime.strptime(day, "%Y-%m-%d")
        history[day] = {"work_sec": int(round(float(hours.replace(",", ".")) * 3600)), "source": source}
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1, sort_keys=True)
    print("✅ В истории дней: %d (%s)" % (len(history), ", ".join(sorted(history))))


def preview():
    data = compute(load_config(), days=1)
    print(json.dumps(data["days"][-1], ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- page

PAGE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Focus Duel</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Unbounded:wght@500;700&family=Manrope:wght@400;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#0F1115;--card:#181B22;--line:#22262F;--sel:#2A2F3A;--fg:#F2F2F0;--mute:#9AA0AA;--me:#4C9AFF;--avg:#5A6170;--bad:#FF8A65;--good:#5BD69B;--rest:#3FC1B0}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font-family:Manrope,system-ui,sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:24px 16px 48px;display:flex;flex-direction:column;gap:16px}
.top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.logo{font-family:Unbounded,sans-serif;font-weight:700;font-size:20px;letter-spacing:-.02em}
.mute{color:var(--mute)}
.tabs{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px;background:var(--card);border-radius:12px;padding:4px;max-width:420px}
.tabs button{height:40px;border:0;border-radius:9px;background:transparent;color:var(--mute);font:600 14px Manrope,sans-serif;cursor:pointer}
.tabs button[aria-selected=true]{background:var(--sel);color:var(--fg)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.card{background:var(--card);border-radius:16px;padding:18px;display:flex;flex-direction:column;gap:12px;min-width:0}
.h{font-weight:700;font-size:15px}
.big{font-family:Unbounded,sans-serif;font-weight:700;font-size:44px;letter-spacing:-.03em;line-height:1}
.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}
.kpi{background:var(--bg);border-radius:12px;padding:10px 12px}
.kpi b{display:block;font-size:18px;margin-top:2px}
.kpi span{font-size:12px;color:var(--mute)}
.bar{height:10px;border-radius:5px;background:var(--line);overflow:hidden}
.bar i{display:block;height:100%;background:var(--me);border-radius:5px}
.delta{font-size:13px;font-weight:600}
.chart{display:flex;align-items:flex-end;gap:4px;height:150px}
.col{flex:1;display:flex;flex-direction:column;align-items:center;gap:6px;min-width:0}
.pair{display:flex;align-items:flex-end;gap:2px;height:120px}
.pair i{display:block;width:8px;border-radius:2px 2px 0 0}
.lbl{font-size:10px;color:var(--mute)}
.legend{display:flex;gap:12px;font-size:12px;color:var(--mute);flex-wrap:wrap}
.legend span{display:flex;align-items:center;gap:5px}
.legend i{width:8px;height:8px;border-radius:2px;display:inline-block}
.cat{display:grid;grid-template-columns:150px 1fr 70px;gap:10px;align-items:center;font-size:13px}
.cat .bar i.d{background:var(--bad)} .cat .bar i.n{background:var(--avg)} .cat .bar i.r{background:var(--rest)}
.split{display:flex;height:14px;border-radius:7px;overflow:hidden;background:var(--line)}
.split i{display:block;height:100%}
.heat{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:4px;max-width:360px}
.heat div{aspect-ratio:1;border-radius:4px;background:var(--line);font-size:10px;color:var(--mute);display:flex;align-items:flex-end;justify-content:flex-end;padding:3px}
.err{background:#3a1f1f;color:#ffd7cf;border-radius:12px;padding:14px}
.ask{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 0;border-top:1px solid var(--line)}
.ask .it{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:14px}
.yn{display:flex;gap:6px;flex-shrink:0}
.yn button{height:40px;min-width:64px;padding:0 14px;border:0;border-radius:10px;background:var(--sel);color:var(--fg);font:600 14px Manrope,sans-serif;cursor:pointer}
.yn button.yes{background:var(--me);color:#0F1115}
.yn button.x{min-width:40px;padding:0;color:var(--mute)}
.yn button:disabled{opacity:.4}
details summary{cursor:pointer;color:var(--mute);font-size:13px;padding-top:4px}
.lang{display:flex;gap:2px;background:var(--card);border-radius:9px;padding:3px}
.lang button{border:0;background:transparent;color:var(--mute);font:700 12px Manrope,sans-serif;padding:6px 9px;border-radius:7px;cursor:pointer}
.lang button[aria-pressed=true]{background:var(--sel);color:var(--fg)}
.right{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.duel{color:var(--fg);background:var(--card);border-radius:10px;padding:9px 14px;font-weight:600;font-size:14px;text-decoration:none}
@media (max-width:560px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.cat{grid-template-columns:110px 1fr 60px}.big{font-size:38px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div class="logo">Focus Duel</div>
    <div class="right">
      <div class="lang" role="group" aria-label="Язык / Тіл"><button data-l="ru">RU</button><button data-l="kk">KZ</button></div>
      <a class="duel" href="/duel" id="duelLink">Дуэль →</a>
    </div>
  </div>
  <div class="mute" id="when" style="font-size:13px"></div>
  <div class="tabs" role="tablist">
    <button role="tab" data-t="today" aria-selected="true"></button>
    <button role="tab" data-t="week" aria-selected="false"></button>
    <button role="tab" data-t="month" aria-selected="false"></button>
  </div>
  <div id="view"></div>
</div>
<script>
const LANG0=/*LANG*/"ru";
const DUEL_URL=/*DUEL*/"/duel";
let LANG=(()=>{try{return localStorage.getItem('fd_lang')||LANG0}catch(e){return LANG0}})();
const T={
 ru:{loc:'ru-RU',hu:'ч',mu:'мин',tabs:['Сегодня','Неделя','Месяц'],nodata:'нет данных',empty:'Пока пусто',
  work:'Работа',dist:'Отвлечения',rest:'Отдых',other:'Прочее',ask:'Это работа?',ask7:'за 7 дней',
  askHelp:'«Да» — время пойдёт в работу. «Нет» — не считается ни работой, ни отвлечением.',yes:'Да',no:'Нет',
  isWork:'Работа',notWork:'Не работа',sorted:'Всё разобрано',mine:n=>`Мои ответы (${n}) — нажми, чтобы поменять`,
  askHint:'Поработай немного — здесь появятся программы и сайты, а ты отметишь, что из них работа.',saveErr:'Не сохранилось: ',
  todayGoal:g=>`Работа сегодня · цель ${g} ч`,vsY:'к вчера',vsAvg:'к среднему за 7 дней',kpis:'Показатели',focus:'Фокус-блок',
  sw:'Переключения',firstLast:(a,b)=>`Первая активность ${a}, последняя ${b}`,noAct:'Сегодня ещё нет активности',
  byHour:'Работа по часам',todayL:'Сегодня',avg7:'Среднее за 7 дней',where:'Куда ушло время',thisWeek:'Эта неделя',
  vsLW:'к тем же дням прошлой недели',summary:'Итоги',best:'Лучший день',avgFocus:'Средний фокус-блок',lastWeek:'Прошлая неделя',
  perDay:'В среднем в день',byDay:'Часы работы по дням',prev:'Прошлая',weekCats:'Категории за неделю',
  wd:['Пн','Вт','Ср','Чт','Пт','Сб','Вс'],worked:(n,a)=>`${n} рабочих дней · в среднем ${a}`,streak:'Серия дней ≥ 4 ч',
  inRow:'дней подряд',cal:'Календарь',brighter:'ярче = больше работы',updated:'обновлено',
  awErr:m=>`Не получилось получить данные из ActivityWatch: ${m}<br>Проверь, что ActivityWatch запущен.`,duel:'Дуэль →'},
 kk:{loc:'kk-KZ',hu:'сағ',mu:'мин',tabs:['Бүгін','Апта','Ай'],nodata:'дерек жоқ',empty:'Әзірге бос',
  work:'Жұмыс',dist:'Алаңдау',rest:'Демалыс',other:'Басқа',ask:'Бұл жұмыс па?',ask7:'7 күн ішінде',
  askHelp:'«Иә» — уақыт жұмысқа есептеледі. «Жоқ» — жұмыс та, алаңдау да болып саналмайды.',yes:'Иә',no:'Жоқ',
  isWork:'Жұмыс',notWork:'Жұмыс емес',sorted:'Бәрі сұрыпталды',mine:n=>`Менің жауаптарым (${n}) — өзгерту үшін бас`,
  askHint:'Біраз жұмыс істе — мұнда бағдарламалар мен сайттар шығады, сен олардың қайсысы жұмыс екенін белгілейсің.',saveErr:'Сақталмады: ',
  todayGoal:g=>`Бүгінгі жұмыс · мақсат ${g} сағ`,vsY:'кешемен салыстырғанда',vsAvg:'7 күндік орташамен салыстырғанда',kpis:'Көрсеткіштер',focus:'Фокус-блок',
  sw:'Ауысулар',firstLast:(a,b)=>`Алғашқы белсенділік ${a}, соңғысы ${b}`,noAct:'Бүгін әлі белсенділік жоқ',
  byHour:'Сағат бойынша жұмыс',todayL:'Бүгін',avg7:'7 күндік орташа',where:'Уақыт неге кетті',thisWeek:'Осы апта',
  vsLW:'өткен аптаның сол күндерімен салыстырғанда',summary:'Қорытынды',best:'Ең үздік күн',avgFocus:'Орташа фокус-блок',lastWeek:'Өткен апта',
  perDay:'Күніне орта есеппен',byDay:'Күндер бойынша жұмыс сағаттары',prev:'Өткен',weekCats:'Апталық санаттар',
  wd:['Дс','Сс','Ср','Бс','Жм','Сб','Жс'],worked:(n,a)=>`${n} жұмыс күні · орта есеппен ${a}`,streak:'≥ 4 сағаттық күндер сериясы',
  inRow:'күн қатарынан',cal:'Күнтізбе',brighter:'жарығырақ = көбірек жұмыс',updated:'жаңартылды',
  awErr:m=>`ActivityWatch-тан дерек алу мүмкін болмады: ${m}<br>ActivityWatch қосулы екенін тексер.`,duel:'Дуэль →'}};
// Названия категорий приходят из config.json по-русски — на казахском показываем перевод
const CAT_KK={'Кино и сериалы':'Кино мен сериалдар','Музыка':'Музыка','Игры':'Ойындар','Соцсети и YouTube':'Әлеуметтік желілер мен YouTube',
  'Прочее':'Басқа','Поставщики':'Жеткізушілер','Команда':'Команда','Claude и ИИ':'Claude және ЖИ','Таблицы и документы':'Кестелер мен құжаттар'};
const t=k=>T[LANG][k];
const catName=n=>{if(LANG!=='kk')return n;const w=n.endsWith(' (в работу)');const b=w?n.slice(0,-11):n;return (CAT_KK[b]||b)+(w?' (жұмыс уақытында)':'')};
let DATA=null, TAB='today';
const $=s=>document.querySelector(s);
const H=s=>{const m=Math.round(s/60);const h=Math.floor(m/60);return h?`${h} ${t('hu')} ${String(m%60).padStart(2,'0')} ${t('mu')}`:`${m} ${t('mu')}`};
const HM=s=>{const m=Math.round(s/60);return `${Math.floor(m/60)}:${String(m%60).padStart(2,'0')}`};
const pct=(a,b)=>b?Math.round(100*a/b):0;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const dt=s=>new Date(s+'T12:00:00');
// Браузеры часто не знают казахских дат (выходит «M10»), поэтому месяцы и дни недели — свои
const KK_M=['қаңтар','ақпан','наурыз','сәуір','мамыр','маусым','шілде','тамыз','қыркүйек','қазан','қараша','желтоқсан'];
const KK_WD=['жс','дс','сс','ср','бс','жм','сб'];
const fmtDay=d=>LANG==='kk'?`${KK_WD[d.getDay()]}, ${d.getDate()} ${KK_M[d.getMonth()]}`:d.toLocaleDateString('ru-RU',{weekday:'short',day:'numeric',month:'long'});
const monthName=d=>LANG==='kk'?KK_M[d.getMonth()]:d.toLocaleDateString('ru-RU',{month:'long'});
const fmtDate=s=>fmtDay(dt(s));

function deltaHtml(cur,ref,label){
  if(!ref) return `<div class="delta mute">${label}: ${t('nodata')}</div>`;
  const d=cur-ref, up=d>=0;
  return `<div class="delta" style="color:${up?'var(--good)':'var(--bad)'}">${up?'▲':'▼'} ${H(Math.abs(d))} <span class="mute" style="font-weight:400">${label}</span></div>`;
}
function cats(day){
  const max=Math.max(1,...day.categories.map(c=>c.sec));
  if(!day.categories.length) return `<div class="mute">${t('empty')}</div>`;
  return day.categories.filter(c=>c.sec>=60).slice(0,10).map(c=>`<div class="cat"><div>${esc(catName(c.name))}</div><div class="bar"><i class="${({work:'',distraction:'d',rest:'r',neutral:'n'})[c.type]}" style="width:${100*c.sec/max}%"></i></div><div class="mute" style="text-align:right">${H(c.sec)}</div></div>`).join('');
}
function split(d){
  const all=d.work_sec+d.distraction_sec+d.rest_sec+d.neutral_sec||1;
  const p=v=>(100*v/all).toFixed(1)+'%';
  return `<div class="split" role="img" aria-label="${t('work')} ${H(d.work_sec)}, ${t('dist')} ${H(d.distraction_sec)}, ${t('rest')} ${H(d.rest_sec)}"><i style="width:${p(d.work_sec)};background:var(--me)"></i><i style="width:${p(d.distraction_sec)};background:var(--bad)"></i><i style="width:${p(d.rest_sec)};background:var(--rest)"></i><i style="width:${p(d.neutral_sec)};background:var(--avg)"></i></div>
  <div class="legend"><span><i style="background:var(--me)"></i>${t('work')} ${H(d.work_sec)}</span><span><i style="background:var(--bad)"></i>${t('dist')} ${H(d.distraction_sec)}</span><span><i style="background:var(--rest)"></i>${t('rest')} ${H(d.rest_sec)}</span><span><i style="background:var(--avg)"></i>${t('other')} ${H(d.neutral_sec)}</span></div>`;
}
function askCard(){
  const S=DATA.suggestions||[], A=DATA.answers||[];
  const btn=(item,a,cls,txt)=>`<button class="${cls}" data-item="${esc(item)}" data-a="${a}">${txt}</button>`;
  const rows=S.map(s=>`<div class="ask"><div class="it">${esc(s.item)} <span class="mute">· ${H(s.sec)}</span></div><div class="yn">${btn(s.item,'work','yes',t('yes'))}${btn(s.item,'no','',t('no'))}</div></div>`).join('');
  const mine=A.map(a=>`<div class="ask"><div class="it">${esc(a.item)}</div><div class="yn">${btn(a.item,a.answer==='work'?'no':'work',a.answer==='work'?'yes':'',a.answer==='work'?t('isWork'):t('notWork'))}${btn(a.item,'','x','✕')}</div></div>`).join('');
  if(!S.length&&!A.length) return DATA.days.some(d=>d.active_sec>3600)?'':`<div class="card"><div class="h">${t('ask')}</div><div class="mute" style="font-size:13px">${t('askHint')}</div></div>`;
  return `<div class="card">
    ${S.length?`<div class="top"><div class="h">${t('ask')}</div><div class="mute" style="font-size:12px">${t('ask7')}</div></div>
    <div class="mute" style="font-size:13px">${t('askHelp')}</div>${rows}`
    :`<div class="h">${t('sorted')}</div>`}
    ${A.length?`<details><summary>${t('mine')(A.length)}</summary>${mine}</details>`:''}
  </div>`;
}
async function answer(b){
  document.querySelectorAll('.yn button').forEach(x=>x.disabled=true);
  try{
    const r=await fetch('/api/answer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({item:b.dataset.item,answer:b.dataset.a||null})});
    const j=await r.json();if(j.error)throw new Error(j.error);
    const open=!!document.querySelector('details[open]');DATA=j;render();
    if(open){const d=document.querySelector('details');if(d)d.open=true}
  }catch(e){alert(t('saveErr')+e.message);render()}
}
function today(){
  const D=DATA.days, td=D[D.length-1], y=D[D.length-2];
  const prev7=D.slice(-8,-1), avg=prev7.reduce((a,d)=>a+d.work_sec,0)/7;
  const goal=DATA.goal_hours*3600;
  const avgHourly=Array.from({length:24},(_,h)=>prev7.reduce((a,d)=>a+d.hourly_work[h],0)/7);
  let lo=24,hi=0;[td.hourly_work,avgHourly].forEach(a=>a.forEach((v,h)=>{if(v>60){lo=Math.min(lo,h);hi=Math.max(hi,h)}}));
  if(lo>hi){lo=9;hi=20}
  const hours=[];for(let h=lo;h<=hi;h++)hours.push(h);
  const bar=(v,c)=>`<i style="background:${c};height:${Math.max(2,Math.round(v/3600*120))}px"></i>`;
  const ask=askCard(), askTop=(DATA.suggestions||[]).length>0||!(DATA.answers||[]).length;
  return `${askTop?ask:''}<div class="grid">
   <div class="card">
     <div class="mute" style="font-size:13px">${t('todayGoal')(DATA.goal_hours)}</div>
     <div class="big">${HM(td.work_sec)}</div>
     <div class="bar"><i style="width:${Math.min(100,100*td.work_sec/goal)}%"></i></div>
     ${deltaHtml(td.work_sec,y.work_sec,t('vsY'))}
     ${deltaHtml(td.work_sec,avg,t('vsAvg'))}
   </div>
   <div class="card">
     <div class="h">${t('kpis')}</div>
     <div class="kpis">
      <div class="kpi"><span>${t('focus')}</span><b>${H(td.longest_focus_sec)}</b></div>
      <div class="kpi"><span>${t('dist')}</span><b>${pct(td.distraction_sec,td.work_sec+td.distraction_sec)}%</b></div>
      <div class="kpi"><span>${t('sw')}</span><b>${td.switches}</b></div>
      <div class="kpi"><span>${t('rest')}</span><b>${HM(td.rest_sec)}</b></div>
     </div>
     ${split(td)}
     <div class="mute" style="font-size:13px">${td.first_active?t('firstLast')(td.first_active,td.last_active):t('noAct')}</div>
   </div>
  </div>
  <div class="card">
    <div class="top"><div class="h">${t('byHour')}</div><div class="legend"><span><i style="background:var(--me)"></i>${t('todayL')}</span><span><i style="background:var(--avg)"></i>${t('avg7')}</span></div></div>
    <div class="chart">${hours.map(h=>`<div class="col"><div class="pair">${bar(td.hourly_work[h],'var(--me)')}${bar(avgHourly[h],'var(--avg)')}</div><div class="lbl">${h}</div></div>`).join('')}</div>
  </div>
  <div class="card"><div class="h">${t('where')}</div>${cats(td)}</div>${askTop?'':ask}`;
}
function week(){
  const D=DATA.days, td=dt(DATA.today), dow=(td.getDay()+6)%7;
  const cur=D.slice(-(dow+1)), prev=D.slice(-(dow+8),-(dow+1));
  const sum=a=>a.reduce((s,d)=>s+d.work_sec,0);
  const max=Math.max(3600,...prev.concat(cur).map(d=>d.work_sec));
  const names=t('wd');
  const cols=names.map((n,i)=>{const a=cur[i],b=prev[i];
    const h=v=>Math.max(2,Math.round(v/max*120));
    return `<div class="col"><div class="pair"><i style="background:var(--me);width:14px;height:${a?h(a.work_sec):0}px"></i><i style="background:var(--avg);width:14px;height:${b?h(b.work_sec):0}px"></i></div><div class="lbl">${n}</div><div class="lbl">${a?HM(a.work_sec):''}</div></div>`}).join('');
  const agg={};cur.forEach(d=>d.categories.forEach(c=>{agg[c.name]=agg[c.name]||{name:c.name,type:c.type,sec:0};agg[c.name].sec+=c.sec}));
  const fake={categories:Object.values(agg).sort((a,b)=>b.sec-a.sec)};
  const best=cur.reduce((b,d)=>d.work_sec>(b?b.work_sec:-1)?d:b,null);
  const avgFocus=cur.length?cur.reduce((s,d)=>s+d.longest_focus_sec,0)/cur.length:0;
  return `<div class="grid">
   <div class="card"><div class="mute" style="font-size:13px">${t('thisWeek')}</div><div class="big">${HM(sum(cur))}</div>${deltaHtml(sum(cur),sum(prev.slice(0,cur.length)),t('vsLW'))}</div>
   <div class="card"><div class="h">${t('summary')}</div><div class="kpis" style="grid-template-columns:repeat(2,minmax(0,1fr))">
     <div class="kpi"><span>${t('best')}</span><b>${best&&best.work_sec?names[(dt(best.date).getDay()+6)%7]+' · '+HM(best.work_sec):'—'}</b></div>
     <div class="kpi"><span>${t('avgFocus')}</span><b>${H(avgFocus)}</b></div>
     <div class="kpi"><span>${t('lastWeek')}</span><b>${HM(sum(prev))}</b></div>
     <div class="kpi"><span>${t('perDay')}</span><b>${HM(cur.length?sum(cur)/cur.length:0)}</b></div>
   </div>${split(cur.reduce((a,d)=>({work_sec:a.work_sec+d.work_sec,distraction_sec:a.distraction_sec+d.distraction_sec,rest_sec:a.rest_sec+d.rest_sec,neutral_sec:a.neutral_sec+d.neutral_sec}),{work_sec:0,distraction_sec:0,rest_sec:0,neutral_sec:0}))}</div>
  </div>
  <div class="card"><div class="top"><div class="h">${t('byDay')}</div><div class="legend"><span><i style="background:var(--me)"></i>${t('thisWeek')}</span><span><i style="background:var(--avg)"></i>${t('prev')}</span></div></div><div class="chart" style="height:170px">${cols}</div></div>
  <div class="card"><div class="h">${t('weekCats')}</div>${cats(fake)}</div>`;
}
function month(){
  const D=DATA.days, goal=DATA.goal_hours*3600, td=dt(DATA.today);
  const first=new Date(td.getFullYear(),td.getMonth(),1);
  const pad=(first.getDay()+6)%7;
  const inMonth=D.filter(d=>dt(d.date).getMonth()===td.getMonth());
  const map={};inMonth.forEach(d=>map[d.date]=d);
  const dim=new Date(td.getFullYear(),td.getMonth()+1,0).getDate();
  const cells=[];for(let i=0;i<pad;i++)cells.push('<div style="background:transparent"></div>');
  for(let d=1;d<=dim;d++){const k=`${td.getFullYear()}-${String(td.getMonth()+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;const r=map[k];
    const op=r&&r.work_sec?Math.min(1,0.18+0.82*r.work_sec/goal):0;
    cells.push(`<div title="${k}: ${r?H(r.work_sec):'—'}" style="${op?`background:rgba(76,154,255,${op.toFixed(2)});color:#0F1115`:''}">${d}</div>`)}
  let streak=0;for(let i=D.length-1;i>=0;i--){if(D[i].work_sec>=4*3600)streak++;else if(i===D.length-1)continue;else break}
  const total=inMonth.reduce((s,d)=>s+d.work_sec,0), worked=inMonth.filter(d=>d.work_sec>1800).length;
  return `<div class="grid">
   <div class="card"><div class="mute" style="font-size:13px">${monthName(first)}</div><div class="big">${Math.round(total/3600)} ${t('hu')}</div><div class="mute">${t('worked')(worked,HM(worked?total/worked:0))}</div></div>
   <div class="card"><div class="h">${t('streak')}</div><div class="big">${streak}</div><div class="mute">${t('inRow')}</div></div>
  </div>
  <div class="card"><div class="top"><div class="h">${t('cal')}</div><div class="mute" style="font-size:12px">${t('brighter')}</div></div>
   <div class="heat">${t('wd').map(n=>`<div style="background:transparent;aspect-ratio:auto;justify-content:center">${n}</div>`).join('')}${cells.join('')}</div></div>`;
}
function chrome(){
  document.documentElement.lang=LANG==='kk'?'kk':'ru';
  document.querySelectorAll('.tabs button').forEach((b,i)=>b.textContent=t('tabs')[i]);
  document.querySelectorAll('.lang button').forEach(b=>b.setAttribute('aria-pressed',b.dataset.l===LANG));
  $('#duelLink').textContent=t('duel');$('#duelLink').href=DUEL_URL;
}
function render(){
  chrome();
  if(!DATA)return;
  $('#when').textContent=fmtDate(DATA.today)+' · '+t('updated')+' '+DATA.generated_at;
  $('#view').innerHTML={today,week,month}[TAB]();
}
async function load(){
  try{const r=await fetch('/api/stats');const j=await r.json();if(j.error)throw new Error(j.error);DATA=j;render()}
  catch(e){$('#view').innerHTML=`<div class="err">${t('awErr')(esc(e.message))}</div>`}
}
$('#view').addEventListener('click',e=>{const b=e.target.closest('.yn button');if(b)answer(b)});
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{TAB=b.dataset.t;document.querySelectorAll('.tabs button').forEach(x=>x.setAttribute('aria-selected',x===b));render()});
document.querySelectorAll('.lang button').forEach(b=>b.onclick=()=>{LANG=b.dataset.l;try{localStorage.setItem('fd_lang',LANG)}catch(e){}render()});
render();load();setInterval(load,5*60*1000);
</script>
</body>
</html>
"""


# Страница дуэли. Одна и та же: на Mac (localhost:5700/duel) и на хостинге для телефона.
# Видит только агрегаты из Supabase: часы, фокус, отвлечения. Без конфигурации — демо-данные.
DUEL_PAGE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0F1115">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Focus Duel">
<link rel="apple-touch-icon" href="icon-180.png">
<link rel="manifest" href="manifest.webmanifest">
<title>Focus Duel</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Unbounded:wght@500;700&family=Manrope:wght@400;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#0F1115;--card:#181B22;--line:#22262F;--sel:#2A2F3A;--fg:#F2F2F0;--mute:#9AA0AA;--bad:#FF8A65;--good:#5BD69B;--p1:#4C9AFF;--p2:#B794F6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font-family:Manrope,system-ui,sans-serif;-webkit-text-size-adjust:100%}
.wrap{max-width:960px;margin:0 auto;padding:max(20px,env(safe-area-inset-top)) 16px 48px;display:flex;flex-direction:column;gap:12px}
.top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.logo{font-family:Unbounded,sans-serif;font-weight:700;font-size:20px;letter-spacing:-.02em}
.mute{color:var(--mute)} .small{font-size:12px}
.demo{background:#2a2414;color:#ffd99a;border-radius:10px;padding:8px 12px;font-size:13px}
.tabs{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px;background:var(--card);border-radius:12px;padding:4px;max-width:420px}
.tabs button{height:40px;border:0;border-radius:9px;background:transparent;color:var(--mute);font:600 14px Manrope,sans-serif;cursor:pointer}
.tabs button[aria-selected=true]{background:var(--sel);color:var(--fg)}
#view{display:flex;flex-direction:column;gap:12px}
.card{background:var(--card);border-radius:16px;padding:16px;display:flex;flex-direction:column;gap:12px;min-width:0}
.h{font-weight:700;font-size:15px}
.duo{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.pc{background:var(--card);border-radius:16px;padding:14px;display:flex;flex-direction:column;gap:8px;min-width:0;border:1.5px solid transparent}
.pc.lead{border-color:var(--c)}
.pn{display:flex;align-items:center;gap:6px;font-weight:700;font-size:15px;min-width:0}
.pn span.nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dot{width:9px;height:9px;border-radius:50%;background:var(--c);flex-shrink:0}
.chip{margin-left:auto;font-size:11px;font-weight:700;color:#0F1115;background:var(--c);border-radius:6px;padding:2px 6px;flex-shrink:0}
.big{font-family:Unbounded,sans-serif;font-weight:700;font-size:40px;letter-spacing:-.03em;line-height:1}
.bar{height:8px;border-radius:4px;background:var(--line);overflow:hidden}
.bar i{display:block;height:100%;border-radius:4px;background:var(--c)}
.kv{display:flex;justify-content:space-between;gap:8px;font-size:13px;border-top:1px solid var(--line);padding-top:7px}
.kv span{color:var(--mute)}
.live{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--good);margin-right:5px;vertical-align:1px}
.gap{text-align:center;font-weight:600;font-size:14px}
.score{display:flex;align-items:center;justify-content:center;gap:14px}
.score .big{font-size:44px}
.score .who{font-size:13px;font-weight:700;display:flex;align-items:center;gap:6px}
.chart{display:flex;align-items:flex-end;gap:3px;height:150px}
.col{flex:1;display:flex;flex-direction:column;align-items:center;gap:6px;min-width:0}
.pair{display:flex;align-items:flex-end;gap:2px;height:120px}
.pair i{display:block;width:7px;border-radius:2px 2px 0 0}
.lbl{font-size:10px;color:var(--mute);white-space:nowrap}
.legend{display:flex;gap:12px;font-size:12px;color:var(--mute);flex-wrap:wrap}
.legend span{display:flex;align-items:center;gap:5px}
.legend i{width:8px;height:8px;border-radius:2px;display:inline-block}
.tbl{display:grid;grid-template-columns:1fr auto auto;gap:8px 14px;font-size:13px;align-items:center}
.tbl .hd{color:var(--mute);font-size:12px}
.heat{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:3px}
.heat div{aspect-ratio:1;border-radius:4px;background:var(--line);font-size:9px;color:var(--mute);display:flex;align-items:flex-end;justify-content:flex-end;padding:2px}
.months{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.err{background:#3a1f1f;color:#ffd7cf;border-radius:12px;padding:14px}
.lang{display:flex;gap:2px;background:var(--card);border-radius:9px;padding:3px}
.lang button{border:0;background:transparent;color:var(--mute);font:700 12px Manrope,sans-serif;padding:6px 9px;border-radius:7px;cursor:pointer}
.lang button[aria-pressed=true]{background:var(--sel);color:var(--fg)}
.right{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
@media (min-width:700px){.big{font-size:52px}.pc{padding:18px;gap:10px}.pair i{width:10px}}
@media (max-width:380px){.big{font-size:34px}}
@media (max-width:420px){.chip{background:transparent;padding:0;font-size:14px}.chip .tx{display:none}}
</style>
</head>
<body>
<div class="wrap">
  <div class="top"><div class="logo">Focus Duel</div>
    <div class="right"><div class="mute small" id="when"></div><div class="lang" role="group" aria-label="Язык / Тіл"><button data-l="ru">RU</button><button data-l="kk">KZ</button></div></div></div>
  <div id="demo"></div>
  <div class="tabs" role="tablist">
    <button role="tab" data-t="today" aria-selected="true"></button>
    <button role="tab" data-t="week" aria-selected="false"></button>
    <button role="tab" data-t="month" aria-selected="false"></button>
  </div>
  <div id="view"></div>
</div>
<script>
const BOOT=/*BOOT*/null;
const hash=new URLSearchParams(location.hash.slice(1));
const CODE=(BOOT&&BOOT.code)||hash.get('g'), ME=(BOOT&&BOOT.me)||hash.get('me');
const DEMO=!(BOOT&&BOOT.url&&BOOT.key&&CODE);
const LANG0=(BOOT&&BOOT.lang)||((navigator.language||'').startsWith('kk')?'kk':'ru');
let LANG=(()=>{try{return localStorage.getItem('fd_lang')||LANG0}catch(e){return LANG0}})();
const T={
 ru:{loc:'ru-RU',hu:'ч',mu:'мин',tabs:['Сегодня','Неделя','Месяц'],demo:'Демо: выдуманные цифры. После подключения тут будут ваши.',
  lead:'Лидер',goal:(g,p)=>`цель ${g} ч · ${p}%`,focus:'Фокус',dist:'Отвлечения',sw:'Переключения',
  notYet:'сегодня ещё не работал',offline:h=>`не на связи ${h} ч`,never:'ещё не присылал данные',atPc:'за компьютером',lastAt:x=>`последняя активность в ${x}`,
  even:'Пока ровно',ahead:(n,d)=>`${n} впереди на ${d}`,weekSfx:' по часам за неделю',byHour:'По часам',week:'Неделя',won:'выигранные дни',
  waitT:'Ждём второго участника',waitD:'Как только друг установит Focus Duel и введёт код группы, здесь появится дуэль.',code:'Код группы:',
  score:'Счёт недели',scoreSub:'дни, кто больше работал',byDay:'По дням',compare:'Сравнение',total:'Всего за неделю',
  perDay:'В среднем в день',avgFocus:'Средний фокус-блок',best:'Лучший день',monthFor:m=>`за ${m}`,streak:'дней подряд с целью',
  hit:(a,b)=>`Цель выполнена ${a} раз из ${b}`,wd:['Пн','Вт','Ср','Чт','Пт','Сб','Вс'],err:'Не получилось загрузить дуэль: '},
 kk:{loc:'kk-KZ',hu:'сағ',mu:'мин',tabs:['Бүгін','Апта','Ай'],demo:'Демо: ойдан алынған сандар. Қосылғаннан кейін мұнда өз сандарыңыз болады.',
  lead:'Көшбасшы',goal:(g,p)=>`мақсат ${g} сағ · ${p}%`,focus:'Фокус',dist:'Алаңдау',sw:'Ауысулар',
  notYet:'бүгін әлі жұмыс істемеді',offline:h=>`${h} сағаттан бері байланыста емес`,never:'әлі дерек жібермеді',atPc:'компьютер басында',lastAt:x=>`соңғы белсенділік ${x}`,
  even:'Әзірге тең',ahead:(n,d)=>`${n} ${d} алда`,weekSfx:' (апта бойынша)',byHour:'Сағат бойынша',week:'Апта',won:'жеңген күндер',
  waitT:'Екінші қатысушыны күтеміз',waitD:'Досың Focus Duel-ді орнатып, топ кодын енгізген соң мұнда дуэль пайда болады.',code:'Топ коды:',
  score:'Апта есебі',scoreSub:'кім көбірек жұмыс істеген күндер',byDay:'Күндер бойынша',compare:'Салыстыру',total:'Аптада барлығы',
  perDay:'Күніне орта есеппен',avgFocus:'Орташа фокус-блок',best:'Ең үздік күн',monthFor:m=>`${m} айында`,streak:'күн қатарынан мақсат орындалды',
  hit:(a,b)=>`Мақсат орындалды: ${a} / ${b} күн`,wd:['Дс','Сс','Ср','Бс','Жм','Сб','Жс'],err:'Дуэльді жүктеу мүмкін болмады: '}};
const t=k=>T[LANG][k];
const RGB=[[76,154,255],[183,148,246]], COL=['var(--p1)','var(--p2)'];
let DATA=null, TAB='today';
const $=s=>document.querySelector(s);
const H=s=>{const m=Math.round(s/60);const h=Math.floor(m/60);return h?`${h} ${t('hu')} ${String(m%60).padStart(2,'0')} ${t('mu')}`:`${m} ${t('mu')}`};
const HM=s=>{const m=Math.round(s/60);return `${Math.floor(m/60)}:${String(m%60).padStart(2,'0')}`};
const pct=(a,b)=>b?Math.round(100*a/b):0;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const ymd=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
const dt=s=>new Date(s+'T12:00:00');
// Браузеры часто не знают казахских дат (выходит «M10»), поэтому месяцы и дни недели — свои
const KK_M=['қаңтар','ақпан','наурыз','сәуір','мамыр','маусым','шілде','тамыз','қыркүйек','қазан','қараша','желтоқсан'];
const KK_WD=['жс','дс','сс','ср','бс','жм','сб'];
const fmtDay=d=>LANG==='kk'?`${KK_WD[d.getDay()]}, ${d.getDate()} ${KK_M[d.getMonth()]}`:d.toLocaleDateString('ru-RU',{weekday:'short',day:'numeric',month:'long'});
const monthName=d=>LANG==='kk'?KK_M[d.getMonth()]:d.toLocaleDateString('ru-RU',{month:'long'});
const EMPTY={work_sec:0,distraction_sec:0,rest_sec:0,active_sec:0,longest_focus_sec:0,switches:0,hourly_work:Array(24).fill(0),first_active:null,last_active:null,updated_at:null};

function demoData(){
  let seed=11;const rnd=()=>(seed=seed*16807%2147483647)/2147483647;
  const now=new Date(), members=[{id:'a',name:'Даниял',goal_hours:5},{id:'b',name:'Нурхан',goal_hours:6}], stats=[];
  for(let i=34;i>=0;i--){const d=new Date(now);d.setDate(d.getDate()-i);const date=ymd(d),dow=d.getDay();
    members.forEach((m,k)=>{
      const off=dow===0||(dow===6&&k===1);
      let left=off?rnd()*1.5*3600:(3.4+rnd()*3)*3600*(k?.92:1);
      const hourly=Array(24).fill(0);const start=k?10:9;
      for(let h=start;h<start+11&&left>0;h++){if(i===0&&h>=now.getHours())break;const v=Math.min(left,(.35+rnd()*.55)*3600);hourly[h]=v;left-=v}
      const work=hourly.reduce((a,b)=>a+b,0);const last=hourly.reduce((a,v,h)=>v?h:a,-1);
      stats.push({member_id:m.id,date,work_sec:work,distraction_sec:work*(.03+rnd()*.16),rest_sec:rnd()*5400,active_sec:work*1.2,
        longest_focus_sec:Math.min(work,(.3+rnd()*1.1)*3600),switches:Math.round(work/3600*(70+rnd()*120)),hourly_work:hourly,
        first_active:work?`${String(start).padStart(2,'0')}:${String(Math.floor(rnd()*50)).padStart(2,'0')}`:null,
        last_active:work?(i===0&&k===0?`${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}`:`${String(last+1).padStart(2,'0')}:${String(Math.floor(rnd()*50)).padStart(2,'0')}`):null,
        updated_at:new Date(now-(k?40:3)*60000).toISOString()})})}
  return {group:{name:'Даниял × Нурхан'},members,stats};
}
async function fetchData(){
  const from=new Date();from.setDate(from.getDate()-34);
  const r=await fetch(BOOT.url.replace(/\/$/,'')+'/rest/v1/rpc/get_group_stats',{method:'POST',
    headers:Object.assign({'Content-Type':'application/json',apikey:BOOT.key},BOOT.key.startsWith('eyJ')?{Authorization:'Bearer '+BOOT.key}:{}),
    body:JSON.stringify({p_code:CODE,p_from:ymd(from),p_to:ymd(new Date())})});
  const j=await r.json();if(!r.ok)throw new Error(j.message||('HTTP '+r.status));return j;
}
function prepare(raw){
  let members=raw.members.slice();
  if(ME){const i=members.findIndex(m=>m.id===ME);if(i>0)members.unshift(members.splice(i,1)[0])}
  members=members.slice(0,2);
  const by={},seenAt={};raw.stats.forEach(s=>{(by[s.member_id]=by[s.member_id]||{})[s.date]=s;
    const u=Date.parse(s.updated_at);if(u&&!(seenAt[s.member_id]>u))seenAt[s.member_id]=u});
  const days=[];for(let i=34;i>=0;i--){const d=new Date();d.setDate(d.getDate()-i);days.push(ymd(d))}
  return {group:raw.group,members,days,today:days[days.length-1],get:(m,d)=>((by[m.id]||{})[d])||EMPTY,seenAt:m=>seenAt[m.id]||0};
}
function seen(m){
  const at=DATA.seenAt(m),gap=(Date.now()-at)/3600000;
  if(!at)return t('never');
  if(gap>2)return `<span style="color:var(--bad)">${t('offline')(Math.floor(gap))}</span>`;
  const s=DATA.get(m,DATA.today);if(!s.last_active)return t('notYet');
  const [h,mi]=s.last_active.split(':').map(Number),now=new Date(),ago=now.getHours()*60+now.getMinutes()-(h*60+mi);
  return ago<=15?`<span class="live"></span>${t('atPc')}`:t('lastAt')(s.last_active);
}
function personCard(m,k,s,lead){
  const goal=m.goal_hours*3600, dist=pct(s.distraction_sec,s.work_sec+s.distraction_sec);
  return `<div class="pc ${lead?'lead':''}" style="--c:${COL[k]}">
    <div class="pn"><i class="dot"></i><span class="nm">${esc(m.name)}</span>${lead?`<span class="chip">👑<span class="tx"> ${t('lead')}</span></span>`:''}</div>
    <div class="big">${HM(s.work_sec)}</div>
    <div class="bar"><i style="width:${Math.min(100,100*s.work_sec/goal)}%"></i></div>
    <div class="mute small">${t('goal')(m.goal_hours,pct(s.work_sec,goal))}</div>
    <div class="kv"><span>${t('focus')}</span><b>${HM(s.longest_focus_sec)}</b></div>
    <div class="kv"><span>${t('dist')}</span><b style="color:${dist>20?'var(--bad)':'inherit'}">${dist}%</b></div>
    <div class="kv"><span>${t('sw')}</span><b>${s.switches}</b></div>
    <div class="mute small">${seen(m)}</div>
  </div>`;
}
function gapLine(a,b,va,vb,suffix){
  const d=Math.abs(va-vb);if(d<60)return `<div class="gap mute">${t('even')}${suffix||''}</div>`;
  const w=va>vb?a:b, k=va>vb?0:1;
  return `<div class="gap">${t('ahead')(`<span style="color:${COL[k]}">${esc(w.name)}</span>`,H(d))}${suffix||''}</div>`;
}
function pairChart(labels,va,vb,max,sub){
  const h=v=>Math.max(v?3:0,Math.round(v/max*120));
  return `<div class="chart">${labels.map((l,i)=>`<div class="col"><div class="pair"><i style="background:var(--p1);height:${h(va[i])}px"></i><i style="background:var(--p2);height:${h(vb[i])}px"></i></div><div class="lbl">${l}</div>${sub?`<div class="lbl">${sub[i]}</div>`:''}</div>`).join('')}</div>`;
}
function legend(){const [a,b]=DATA.members;return `<div class="legend"><span><i style="background:var(--p1)"></i>${esc(a.name)}</span>${b?`<span><i style="background:var(--p2)"></i>${esc(b.name)}</span>`:''}</div>`}
function weekDays(){const td=dt(DATA.today),dow=(td.getDay()+6)%7;return DATA.days.slice(-(dow+1))}
function wins(days){const [a,b]=DATA.members;let wa=0,wb=0;days.forEach(d=>{const x=DATA.get(a,d).work_sec,y=DATA.get(b,d).work_sec;if(Math.abs(x-y)<60)return;if(x>y)wa++;else wb++});return [wa,wb]}
function waiting(){return `<div class="card"><div class="h">${t('waitT')}</div><div class="mute" style="font-size:14px">${t('waitD')}</div>${CODE?`<div style="font-size:14px">${t('code')} <b style="font-family:Unbounded,sans-serif;letter-spacing:.04em">${esc(CODE)}</b></div>`:''}</div>`}

function today(){
  const [a,b]=DATA.members;if(!b)return waiting();
  const td=DATA.today,sa=DATA.get(a,td),sb=DATA.get(b,td),lead=Math.abs(sa.work_sec-sb.work_sec)>=60?(sa.work_sec>sb.work_sec?0:1):-1;
  let lo=24,hi=0;[sa,sb].forEach(s=>s.hourly_work.forEach((v,h)=>{if(v>60){lo=Math.min(lo,h);hi=Math.max(hi,h)}}));if(lo>hi){lo=9;hi=19}
  const hours=[];for(let h=lo;h<=hi;h++)hours.push(h);
  const [wa,wb]=wins(weekDays());
  return `<div class="duo">${personCard(a,0,sa,lead===0)}${personCard(b,1,sb,lead===1)}</div>
  ${gapLine(a,b,sa.work_sec,sb.work_sec)}
  <div class="card"><div class="top"><div class="h">${t('byHour')}</div>${legend()}</div>${pairChart(hours,hours.map(h=>sa.hourly_work[h]),hours.map(h=>sb.hourly_work[h]),3600)}</div>
  <div class="card"><div class="top"><div class="h">${t('week')}</div><div class="mute small">${t('won')}</div></div>
    <div class="score"><div class="who" style="color:var(--p1)">${esc(a.name)}</div><div class="big">${wa} : ${wb}</div><div class="who" style="color:var(--p2)">${esc(b.name)}</div></div></div>`;
}
function week(){
  const [a,b]=DATA.members;if(!b)return waiting();
  const cur=weekDays(),names=t('wd'),all=names.map((_,i)=>cur[i]||null);
  const va=all.map(d=>d?DATA.get(a,d).work_sec:0),vb=all.map(d=>d?DATA.get(b,d).work_sec:0);
  const sum=v=>v.reduce((x,y)=>x+y,0),[wa,wb]=wins(cur),max=Math.max(3600,...va,...vb);
  const avg=(m,f)=>{const ds=cur.filter(d=>DATA.get(m,d).work_sec>0);return ds.length?ds.reduce((x,d)=>x+f(DATA.get(m,d)),0)/ds.length:0};
  const best=m=>{let bd=null;cur.forEach(d=>{if(!bd||DATA.get(m,d).work_sec>DATA.get(m,bd).work_sec)bd=d});return bd&&DATA.get(m,bd).work_sec?names[(dt(bd).getDay()+6)%7]+' · '+HM(DATA.get(m,bd).work_sec):'—'};
  const distPct=(m,v)=>{const ds=cur.reduce((x,d)=>x+DATA.get(m,d).distraction_sec,0);return pct(ds,sum(v)+ds)+'%'};
  const row=(label,fa,fb)=>`<div>${label}</div><b style="color:var(--p1)">${fa}</b><b style="color:var(--p2)">${fb}</b>`;
  return `<div class="card"><div class="top"><div class="h">${t('score')}</div><div class="mute small">${t('scoreSub')}</div></div>
    <div class="score"><div class="who" style="color:var(--p1)">${esc(a.name)}</div><div class="big">${wa} : ${wb}</div><div class="who" style="color:var(--p2)">${esc(b.name)}</div></div>
    ${gapLine(a,b,sum(va),sum(vb),t('weekSfx'))}</div>
  <div class="card"><div class="top"><div class="h">${t('byDay')}</div>${legend()}</div>${pairChart(names,va,vb,max,all.map((d,i)=>d?HM(Math.max(va[i],vb[i])):''))}</div>
  <div class="card"><div class="h">${t('compare')}</div><div class="tbl">
    <div class="hd"></div><div class="hd">${esc(a.name)}</div><div class="hd">${esc(b.name)}</div>
    ${row(t('total'),HM(sum(va)),HM(sum(vb)))}
    ${row(t('perDay'),HM(avg(a,s=>s.work_sec)),HM(avg(b,s=>s.work_sec)))}
    ${row(t('avgFocus'),HM(avg(a,s=>s.longest_focus_sec)),HM(avg(b,s=>s.longest_focus_sec)))}
    ${row(t('dist'),distPct(a,va),distPct(b,vb))}
    ${row(t('best'),best(a),best(b))}
  </div></div>`;
}
function month(){
  const td=dt(DATA.today),first=new Date(td.getFullYear(),td.getMonth(),1),pad=(first.getDay()+6)%7,dim=new Date(td.getFullYear(),td.getMonth()+1,0).getDate();
  const one=(m,k)=>{
    const goal=m.goal_hours*3600,inMonth=DATA.days.filter(d=>dt(d).getMonth()===td.getMonth());
    const total=inMonth.reduce((x,d)=>x+DATA.get(m,d).work_sec,0),hit=inMonth.filter(d=>DATA.get(m,d).work_sec>=goal).length;
    let streak=0;for(let i=DATA.days.length-1;i>=0;i--){if(DATA.get(m,DATA.days[i]).work_sec>=goal)streak++;else if(i===DATA.days.length-1)continue;else break}
    const cells=[];for(let i=0;i<pad;i++)cells.push('<div style="background:transparent"></div>');
    for(let d=1;d<=dim;d++){const key=ymd(new Date(td.getFullYear(),td.getMonth(),d)),w=DATA.get(m,key).work_sec,op=w?Math.min(1,.18+.82*w/goal):0;
      cells.push(`<div title="${key}: ${H(w)}" style="${op?`background:rgba(${RGB[k]},${op.toFixed(2)});color:#0F1115`:''}">${d}</div>`)}
    return `<div class="card" style="--c:${COL[k]}"><div class="pn"><i class="dot"></i><span class="nm">${esc(m.name)}</span></div>
      <div class="duo"><div><div class="big" style="font-size:30px">${Math.round(total/3600)} ${t('hu')}</div><div class="mute small">${t('monthFor')(monthName(first))}</div></div>
      <div><div class="big" style="font-size:30px">${streak}</div><div class="mute small">${t('streak')}</div></div></div>
      <div class="mute small">${t('hit')(hit,inMonth.length)}</div>
      <div class="heat">${t('wd').map(n=>`<div style="background:transparent;aspect-ratio:auto;justify-content:center">${n}</div>`).join('')}${cells.join('')}</div></div>`;
  };
  return `<div class="months">${DATA.members.map(one).join('')}</div>`;
}
function chrome(){
  document.documentElement.lang=LANG==='kk'?'kk':'ru';
  document.querySelectorAll('.tabs button').forEach((b,i)=>b.textContent=t('tabs')[i]);
  document.querySelectorAll('.lang button').forEach(b=>b.setAttribute('aria-pressed',b.dataset.l===LANG));
  $('#demo').innerHTML=DEMO?`<div class="demo">${t('demo')}</div>`:'';
}
function render(){
  chrome();
  if(!DATA)return;
  const n=new Date();$('#when').textContent=fmtDay(dt(DATA.today))+' · '+String(n.getHours()).padStart(2,'0')+':'+String(n.getMinutes()).padStart(2,'0');
  $('#view').innerHTML={today,week,month}[TAB]();
}
async function load(){
  try{DATA=prepare(DEMO?demoData():await fetchData());render()}
  catch(e){$('#view').innerHTML=`<div class="err">${t('err')}${esc(e.message)}</div>`}
}
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{TAB=b.dataset.t;document.querySelectorAll('.tabs button').forEach(x=>x.setAttribute('aria-selected',x===b));render()});
document.querySelectorAll('.lang button').forEach(b=>b.onclick=()=>{LANG=b.dataset.l;try{localStorage.setItem('fd_lang',LANG)}catch(e){}render()});
render();load();setInterval(load,5*60*1000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)load()});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    # Запуск без аргументов из «Загрузок» и т.п. — это установка у нового человека
    installed = os.path.dirname(os.path.realpath(__file__)) == os.path.realpath(APP_DIR)
    cmd = sys.argv[1] if len(sys.argv) > 1 else ("serve" if installed else "install")
    args = sys.argv[2:]
    OPTS = dict((args[i][2:], args[i + 1]) for i in range(len(args) - 1) if args[i].startswith("--"))
    INTERACTIVE = sys.stdin.isatty() and not OPTS
    if cmd == "install":
        try:
            install()
        except KeyboardInterrupt:
            print(tr("aborted"))
    elif cmd == "uninstall":
        uninstall()
    elif cmd == "preview":
        preview()
    elif cmd == "duel-create":
        duel_create()
    elif cmd == "duel-join" and len(sys.argv) > 2:
        duel_join(sys.argv[2])
    elif cmd == "telegram-setup":
        try:
            telegram_setup()
        except KeyboardInterrupt:
            print("\nПрервано.")
    elif cmd == "duel-page":
        duel_export()
    elif cmd == "check":
        check()
    elif cmd == "status":
        status()
    elif cmd == "rize-connect":
        rize_connect()
    elif cmd == "history-add" and len(sys.argv) > 2:
        history_add(sys.argv[2:])
    elif cmd == "update":
        updates_cmd("now")
    elif cmd == "updates" and len(sys.argv) > 2:
        updates_cmd(sys.argv[2])
    elif cmd in ("work", "notwork") and len(sys.argv) > 2:
        mark(sys.argv[2:], "work" if cmd == "work" else "no")
    elif cmd == "telegram-test":
        c = load_config()
        print(telegram_send(c, summary_text(compute(c, days=8))) and "Отправлено" or "Ошибка")
    else:
        serve()
