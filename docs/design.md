# Design — Автономный таск-контур «orc»

инструментарий верификации: pytest (unit — фикстуры лимитов/watchdog/re-validate); bash-скрипты E2E (`.verify/e2e-shift.sh`); `bin/pipeline-lint.sh --doctor` + scorecard для патчей конвейера; фикстуры в `tests/fixtures/`. CLI-продукт — скриншоты N/A, «глазами» = вывод команд в evidence/.

## Опыт / ценность (владелец смысла; каждая фича ссылается сюда)
Такт оператора: **накидал → смена идёт → гейт → газета.**
1. Утро: `orc add proj "задача"` ×N (≤5 мин) → `orc start` (или LaunchAgent сам).
2. День: `orc status` — живая картина (строка/задача: фаза, статус, минуты, расход; ждущие гейта — сверху; итог пула внизу).
3. Гейт: воркер дошёл до ТЗ-гейта → macOS-уведомление → **сессия ждёт живьём** (выбор пользователя); гейтовые задачи диспетчер держит В КОНЦЕ очереди, чтобы автономные прошли первыми.
4. Итог: `orc status` (или авто по завершении смены) → **газета**: первый экран ≤150 слов, первой строкой сводка «7 DONE, 2 ждут, 1 упала; съедено 34% окна», ниже — гейт-карточки и пути.
Signature = газета+canary: «одна команда — полная картина работы смены» (v1 дневной; газета честно помечает срезанное/не-взятое).

## Архитектура
Один процесс-диспетчер (python3-stdlib, демон в LaunchAgent) + тонкие bash-обёртки спавна. Состояние на диске (crash-safe): очередь = beads (`.beads/` в хабе), рантайм-состояние смены = `~/.orc/shift.json` (PID-реестр, lease, статусы), heartbeat = `~/.orc/hb/<session>.log`.

```
orc (python CLI)        add / status / start / stop
  └ dispatcher loop     bd ready→claim→re-validate→preflight→mutex→spawn→monitor
      ├ spawner (bash)  osascript: new Terminal → cd proj && claude "<prompt>"
      ├ watchdog        heartbeat + PreToolUse-маркер → петля/тишина → verify→kill→restart(cap)
      ├ admission       ccusage blocks --active + free-RAM + limit-strings
      └ notifier        osascript display notification (P1: happy/telegram)
конфиг ~/.orc/config.json   калибровки (пороги, капы, denylist, allowlist MCP)
воркер: проектный .claude/settings.json (deny-стены) + стартовый промпт → pipeline
```

## Контракты
- **bd** (очередь): `bd ready --json`, `bd update <id> --claim`, `bd close <id>`; гейт = блокирующая bd-задача «approve-ТЗ-<слаг>» на человеке (пока open — build-задача не ready). Приоритет `-p` → порядок; гейтовые/зависящие-от-человека помечены меткой, диспетчер сортирует их в конец.
- **workspace задачи**: `<проект>/docs/tasks/<слаг>/` — свой STATE.md/brief.md/features (двухслойность: продуктовый слой `<проект>/docs/` общий, patch-инвентарь RS-02).
- **стартовый промпт** (шаблон, en): «Resume/başla pipeline task. Workspace: docs/tasks/<слаг>/. Product layer: docs/. Task: <текст>. Read docs/tasks/<слаг>/STATE.md if exists (resume), else phase 0.» → сессия вызывает скилл pipeline.
- **heartbeat**: PostToolUse-хук воркера пишет `<ts> <tool> <arg-hash>`; PreToolUse-хук пишет маркер «tool-in-flight» → watchdog отличает работу от зависания.
- **shift.json**: `{workers:[{pid,tab_id,session,proj,task,phase,started,tokens_before}], parked:[...], done:[...]}` — правда о ПРОЦЕССАХ; kill только по своим PID. `tab_id` = Terminal window id (F14, чинит `pid None`). **Арбитр рассинхрона: bd = правда о ЗАДАЧАХ, shift.json = правда о ПРОЦЕССАХ; при расхождении bd важнее — shift.json чинится по bd + живым PID (F4/F8).**
- **замыкание петли (F14)**: диспетчер ПОЛЛИТ `<проект>/docs/tasks/<слаг>/STATE.md` активной задачи (вызов из `orc status`); терминал-статус (DONE/DONE-WAVE-N/BETA → done; parked-on-gate/«ждёт ответа» → gate). done → `bd close` + shift.mark_done + СТОП ВОРКЕРА + газета догоняет; gate → park (окно держим оператору, F9). Стоп воркера = `spawn.close_worker(cfg, handle, session)` через бэкенд-селектор (см. ниже F15).
- **спавн-бэкенд (F15, решение 2026-07-15)**: воркеры спавнятся в **Ghostty** (реальный терминал пользователя), НЕ Terminal.app. Причина: Terminal.app при профиле shellExitAction=keep-window оставлял husk-окна с диалогом «подтвердите закрытие» (накопилось 16, боль пользователя); `close (window id)` — no-op (проверено: close/System Events/AXCloseButton, вероятен TCC-барьер). Ghostty закрывает surface при выходе `-e`-команды (спайк: 0 окон после exit). Механика: `open -na Ghostty.app --args -e bash -lc '<cmd>'`; воркер экспортит `ORC_SESSION=<task_id>` → маркер в argv для find/kill (`pgrep/pkill -f "ORC_SESSION=<id>"`); стоп воркера = kill процесса → окно само закрывается (0 husk, 0 диалог). У Ghostty НЕТ AppleScript window id → shift.json.tab_id хранит session-маркер (Ghostty) ИЛИ window id (Terminal). Config `terminal: ghostty|terminal` (дефолт ghostty); если Ghostty не установлен → fallback на Terminal. spawn_worker/close_worker/worker_pid маршрутизируют по бэкенду; F8-PID (Ghostty: pgrep маркера) и F14-close идут через него. Терминал-fallback сохраняет старое поведение (kill по tty + best-effort close, husk возможен на keep-профиле — косметика).
- **settings.json воркера**: генератор МЕРЖИТ deny-набор в существующий `<проект>/.claude/settings.json` (пользовательские правила сохраняются), не overwrite (side-effect на продукт минимизирован; F1).
- **per-задачный расход**: дельта `ccusage` total между claim и close (1 воркер → атрибуция точна; F6).
- **admission**: `spawn if ready≠∅ and free_ram≥threshold and window_remaining≥min and no limit-string active`.

## Данные / состояние
Всё на диске, переживает kill -9: bd (dolt), shift.json (atomic write через tmp+rename), heartbeat-логи, git-коммиты воркеров = чекпойнты задач. Восстановление после рестарта диспетчера: прочитать shift.json → сверить с реальными PID (живые — подхватить, мёртвые → задача в ready через lease).

## Опыт потребления по фазам (деконструкция такта)
- Постановка: батч-режим `orc add --batch <<EOF` (строка = задача, `proj: text`) — 10 задач одной вставкой.
- Живой status: обновляемая таблица, gate-задачи ⏸ сверху, работающие ▸, done ✓, fail ✗; низ — «пул: 34% окна, 5.2ч осталось, RAM ok».
- Газета: сводка → карточки гейтов (скоуп/планка/полномочия + путь к ТЗ + цена ошибки) → упавшие с крэш-досье → пути к диффам.

## Инструментарий верификации (детерминированный)
- unit: `python3 -m pytest tests/` (фикстуры лимит-строк, heartbeat-петля, re-validate, admission).
- E2E: `.verify/e2e-shift.sh` (2 orc-test проекта, 1 задача с гейтом; проверка bd-статусов, git-фактов, отсутствия дублей).
- патчи конвейера: `~/.claude/skills/pipeline/bin/pipeline-lint.sh --doctor` + scorecard на старом и новом макете (характеризационный набор).

## Принятые ADR
ADR-0001 стек (python-stdlib, спайк stack.md) · ADR-0002 конфиг JSON (tomllib нет в 3.9.6) + гейт как bd-задача с сортировкой в конец. Deny-стены — фича F1 (негативный спайк = гейт входа), отдельный ADR не нужен.
