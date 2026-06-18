# Деплой JARVIS на Oracle Cloud Always Free (24/7, не спит, бесплатно)

Бот крутится на бесплатной виртуалке Oracle, HTTPS даёт Caddy через бесплатный
домен DuckDNS. Всё в одном процессе — бот, Mini App и мост к ПК (общий `PCBridge`).

```
Telegram ─┐
Телефон  ─┼──► https://<твой>.duckdns.org ──► Caddy (TLS) ──► jarvis:8000
ПК (wss) ─┘                                                   (render_app:app)
```

---

## Часть 1. Аккаунт Oracle + виртуалка (в браузере)

1. Зайди на **https://cloud.oracle.com** → **Start for free**.
   - Нужна карта для верификации личности — **деньги не списывают** (Always Free).
   - **Home Region** выбери поближе: *Germany Central (Frankfurt)* или *Switzerland North (Zurich)*.
   - ⚠️ Регион менять потом нельзя — выбирай сразу правильный.

2. Создай инстанс: меню ☰ → **Compute → Instances → Create Instance**.
   - **Image:** Canonical **Ubuntu 22.04** (или 24.04).
   - **Shape → Change Shape:**
     - Сначала пробуй **Ampere (ARM)** → `VM.Standard.A1.Flex`, выставь **1 OCPU / 6 GB** (в пределах free 4 OCPU / 24 GB).
     - Если выдаёт **"Out of capacity"** (частая беда ARM) → переключись на **`VM.Standard.E2.1.Micro`** (AMD, 1 GB, всегда доступен). Для бота хватит.
   - **SSH keys:** выбери **Generate a key pair for me** → **скачай приватный ключ** (`.key`) — он понадобится для входа. Положи рядом, запомни путь.
   - Убедись, что назначается **публичный IPv4**.
   - **Create**. Через ~1 мин запиши **Public IP address** инстанса.

3. Открой порты 80 и 443 в сети:
   - На странице инстанса → раздел **Primary VNIC** → клик по **Subnet** → **Security List** (Default).
   - **Add Ingress Rules** дважды:
     - Source `0.0.0.0/0`, IP Protocol **TCP**, Destination Port **80**
     - Source `0.0.0.0/0`, IP Protocol **TCP**, Destination Port **443**

---

## Часть 2. Бесплатный домен DuckDNS

1. **https://www.duckdns.org** → войди (Google/GitHub).
2. Придумай поддомен, напр. `jarvismarkx` → получишь **`jarvismarkx.duckdns.org`**.
3. В поле **current ip** впиши **Public IP** твоей VM → **update ip**.

---

## Часть 3. Настройка на сервере (по SSH)

Подключись (пример для скачанного ключа `jarvis.key`):

```bash
# Windows PowerShell / Git Bash:
ssh -i C:/путь/к/jarvis.key ubuntu@<PUBLIC_IP>
```

> Если ssh ругается на права ключа — `icacls` (Win) или `chmod 600 jarvis.key` (bash).

Дальше на самой VM, по порядку:

```bash
# 1. Docker + git
sudo apt update
sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker ubuntu

# 2. ⚠️ ГЛАВНАЯ ловушка Oracle: на Ubuntu по умолчанию iptables РЕЖЕТ всё кроме 22.
#    Без этих строк HTTPS работать НЕ будет, хотя порты в Security List открыты.
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80  -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save

# 3. Перелогинься, чтобы docker заработал без sudo
exit
```

Снова зайди по `ssh ...`, затем:

```bash
# 4. Клонируем проект
git clone https://github.com/Sardorbek505/jarvis-mark-x.git
cd jarvis-mark-x

# 5. Секреты: копируем шаблон и заполняем
cp .env.oracle.example .env
nano .env        # впиши DOMAIN, MINIAPP_URL, GEMINI_API_KEY, TELEGRAM_BOT_TOKEN,
                 # TELEGRAM_ALLOWED_USERS, PC_LINK_TOKEN. Ctrl+O, Enter, Ctrl+X.

# 6. Запуск!
docker compose -f docker-compose.oracle.yml up -d --build

# 7. Смотрим логи (ждём "JARVIS started ✅" и выдачу TLS-сертификата Caddy)
docker compose -f docker-compose.oracle.yml logs -f
```

---

## Часть 4. Проверка

1. В браузере: `https://<твой>.duckdns.org/health` → должно вернуть `{"status":"ok",...}`
   (первый заход может занять ~30 сек, пока Caddy получает сертификат).
2. В Telegram напиши боту **/start** → должен ответить.
3. Команда **/app** → откроется Mini App.

---

## Часть 5. Подключение домашнего ПК (управление компьютером)

На **домашнем ПК** в `config/api_keys.json`:

```json
"pc_link_url": "wss://<твой>.duckdns.org",
"pc_link_token": "<тот_же_PC_LINK_TOKEN_что_в_.env_на_сервере>"
```

Запусти PC-клиент на ПК (`python -m telegram_bot.pc_server` или твой `start_all.bat`).
В Telegram `/status` покажет **ПК: онлайн ✅**.

---

## Обслуживание

```bash
# Обновить после новых коммитов
cd jarvis-mark-x && git pull && docker compose -f docker-compose.oracle.yml up -d --build

# Рестарт / стоп / логи
docker compose -f docker-compose.oracle.yml restart
docker compose -f docker-compose.oracle.yml down
docker compose -f docker-compose.oracle.yml logs -f --tail=100
```

## Если что-то не так

| Симптом | Причина / фикс |
|---|---|
| `https://.../health` не открывается | Не добавил iptables-правила (Часть 3, шаг 2) или порты в Security List (Часть 1, шаг 3) |
| Caddy в логах: ошибка получения сертификата | DuckDNS IP ≠ реальный IP VM, либо порт 80 закрыт (Let's Encrypt стучит на 80) |
| Бот не отвечает, в логах нет webhook | `MINIAPP_URL` в `.env` не совпадает с доменом, или неверный `TELEGRAM_BOT_TOKEN` |
| `Out of capacity` при создании VM | Переключись на shape `VM.Standard.E2.1.Micro` (AMD) |
| ПК офлайн в `/status` | `pc_link_url`/`pc_link_token` на ПК не совпадают с сервером |
