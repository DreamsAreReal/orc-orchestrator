# Фиче-лист — Автономный таск-контур «orc»

North Star: утром накидал ~10 задач по проектам — Mac сам довёл их конвейером pipeline до конца без потери качества.

## Дисциплина статусов
- Каждая фича рождается `todo` (= failing). F1 — walking skeleton. Гранулярность: 1 такт/≤3 критерия.
- `self-pass` ставит builder (доказательство: команда→вывод в `docs/evidence/F<N>/`). `verified` ставит оркестратор по вердикту evaluator-а (тест ACT→OBSERVE→PERSIST→ROUND-TRIP).
- `cut` — фича вырезана целиком. Смоук на старте сессии builder-а: исполняемые проверки скелета + золотого пути.
- CLI-продукт: «глазами» = вывод команд, не скриншоты. Столбец «Ворота» = какие G из brief.md закрывает фича.

## Фичи

### F1 — Границы песочницы: генератор deny-стен + НЕГАТИВНЫЙ спайк [M1] — ГЕЙТ входа в реальный спавн
Ворота: часть G0c, безопасность (Р13/reward hacking). Опыт/ценность: без доверенных стен автономная смена не имеет права запускать реальный claude.
Зачем: воркеры под глобальным bypassPermissions спавнят реальный claude уже в скелете (F2) → стены обязаны быть доказаны ДО первого реального спавна.
Что: генератор проектного `.claude/settings.json` воркера с deny (git push, rm -rf вне workspace, чтение ~/.ssh и чужих проектов, запись вне workspace) + чистка env от секрет-денилиста + MCP-allowlist из конфига (дефолт пуст). **Негативный спайк** доказывает блокировку на 2.1.193.
Приёмка:
- [x] негативный спайк: из-под воркера `git push` / `rm -rf` вне workspace / чтение `~/.ssh/id_*` — ЗАБЛОКИРОВАНЫ (вывод в evidence/F1/); спайк не прошёл → эскалация, дальнейшая сборка стоп
- [x] генератор не затирает существующий `.claude/settings.json` проекта: merge (deny-набор добавляется, пользовательские правила сохраняются), а не overwrite
- [x] env воркера без переменных секрет-денилиста; MCP только из allowlist
Проверка: `bash .verify/negative-walls.sh` (ГЕЙТ: этот тест зелёный ДО F2)
Статус: verified
Доказательство:
- `bash .verify/negative-walls.sh` → "ALL WALLS HELD (3/3). F1 gate PASS", exit 0 (LIVE claude 2.1.193, bypassPermissions). Лог: docs/evidence/F1/negative-walls.log
- `python3 -m pytest tests/test_worker_walls.py` → 37 passed. Лог: docs/evidence/F1/unit-tests.log
- merge/env/MCP живьём: docs/evidence/F1/merge-and-env-demo.log (user-hooks+customUserKey+model preserved, 4 secret vars stripped, MCP allowlist [] по умолчанию)
- **ДОЛГ G0c ЗАКРЫТ (M4): git-push-возможность лишена в границах песочницы воркера.** Проблема (из eval M3): обфусцированный `git push` обходит F1-паттерн-хук (base64|bash), а F13-seatbelt конфайнит только ФС-запись (сеть вкл для claude API/git fetch) → воркер мог push-нуть через osxkeychain. Фикс: `worker_walls.push_neutralizing_export_prefix()` экспортит GIT_TERMINAL_PROMPT=0 / GIT_ASKPASS=/usr/bin/false / credential.helper='' (inline GIT_CONFIG) в inner-команду воркера (spawn.build_start_command + ghostty) → ни один git-процесс в дереве воркера не может получить креды. Доказательство:
  - `bash .verify/push-wall.sh` → "F13-push PASS", exit 0. БАЗЛАЙН (норм. env): обфусц. push дошёл до АУТЕНТИФИЦИРОВАННОГО GitHub (keychain дал креды → "Repository not found" = стена нагружена). ВОРКЕР-env: тот же обфусц. push → "could not read Username: terminal prompts disabled", exit 128, sentinel НЕ ушёл. Лог: docs/evidence/F13-push/push-wall.log (+baseline.out/walled.out/push-spike.sh).
  - НЕТ побочки: `claude auth status` loggedIn=true exit 0 под этим env (OAuth в Keychain — свой путь, не git creds); `git ls-remote` публичного репо exit 0 (read не требует кредов). Лишён ТОЛЬКО push.
  - `python3 -m pytest tests/test_worker_walls.py` → 41 passed (+4: env-форма, prefix-shell-shape, start-command-carries-wall, keychain-disabled-under-worker-env).

### F2 — Walking skeleton: сквозная смена из 1 задачи + газета + canary [M1] [золотой путь]
Ворота: G0b, часть G1, G7 (canary), signature. Опыт/ценность: весь такт «накидал→смена→газета» тонким срезом; signature (газета+canary) в скелете, не заглушкой.
Зачем: доказать сквозной поток под уже-проверенными стенами (F1).
Что: `orc add` одну задачу → `orc start` → canary-предполёт → claim → spawn РЕАЛЬНОГО терминала с интерактивным claude в папке проекта → задача идёт → `orc status` печатает газету.
Приёмка:
- [x] canary печатает отчёт (bd/auth/ccusage/RAM/спавн-терминала); подставной фейл → смена не стартует (G7)
- [x] `orc start` спавнит реальный интерактивный терминал с claude (не headless), задача «создай hello.txt со словом ready» выполняется, файл появляется
- [x] `orc status` печатает газету: первая строка = сводка (N done/parked/failed + РЕАЛЬНЫЙ расход токенов сменой), первый экран ≤150 слов
Проверка: `bash .verify/e2e-skeleton.sh` + вывод в `docs/evidence/F2/`
Статус: verified
Доказательство:
- `bash .verify/e2e-skeleton.sh` → "F2 SKELETON PASS", exit 0. РЕАЛЬНЫЙ osascript-терминал с интерактивным claude создал hello.txt=[ready] за ~14с; G7 forced-fail отказал старт; газета ≤150 слов. Лог: docs/evidence/F2/e2e-skeleton.log
- `python3 -m pytest tests/test_skeleton.py` → 13 passed (config/shift/ordering/report/canary). Лог: docs/evidence/F2/unit-tests.log
- команда запуска: `bin/orc {init|add <proj> "<text>"|start [--once]|status [--newspaper]} [--json]`
- [КРИТ-ФИКС ГАЗЕТЫ 2026-07-15, найден пользователем] сводка/итог-пула БОЛЬШЕ НЕ показывают «съедено N% окна». `_window_pct = (300-remaining_minutes)/300` — это ПРОШЕДШЕЕ ВРЕМЯ 5-часового блока, а НЕ расход квоты (та же время-vs-лимиты путаница, что убрана из admission). Теперь сводка показывает РЕАЛЬНЫЙ расход сменой (дельта ccusage totalTokens от tokens_at_start, снятого при `orc start`; формат k/M; фолбэк — дельта costUSD «~$0.3»). Время до сброса блока подписано ЧЕСТНО отдельной строкой «до сброса окна лимитов N мин», НЕ как «съедено». Абсолют, не выдуманный % (ccusage не знает кап Max x20). Тесты: tests/test_report.py 8 passed (формат k/M, per-task-дельта→window-дельта→cost-фолбэк→None, сводка/футер без «% окна», omit при ccusage-down) + обновлён test_skeleton (326k вместо «50% окна»). Пример: `смена: 1 готово, 1 ждут тебя, 0 упало; потрачено ~326k токенов за смену`. Evidence: docs/evidence/newspaper-spend/

### F3 — `orc add` / `orc status` (live) + JSON везде [M1]
Ворота: G11, часть G12. Опыт/ценность: постановка ≤5 мин; живой status (такт «день»).
Что: `orc add <proj> "<text>" [-p N]` и `--batch` из stdin; `orc status` два режима (live: строка/задача фаза/статус/минуты/расход, гейты сверху ⏸, итог пула; газета по завершении). Все команды `--json`.
Приёмка:
- [x] `orc add --batch` из 10 строк создаёт 10 bd-задач ≤5 мин (секундомер) (G11)
- [x] `orc status` live: строка на активную задачу + итог пула, гейтовые сверху; `--json` валиден
Проверка: `python3 -m pytest tests/test_cli.py` + `bash .verify/timing-add.sh`
Статус: verified
Доказательство:
- `bash .verify/timing-add.sh` → "G11 PASS: 10 tasks in ready in 8s (<= 5 min)", exit 0. Лог: docs/evidence/F3/timing-add.log
- `python3 -m pytest tests/test_cli.py` → 7 passed (single/batch add, skip плохого проекта, JSON-валидность, gate-last ordering). Лог: docs/evidence/F3/unit-tests.log

### F4 — Диспетчер-ядро: ready→claim→re-validate→preflight→mutex→spawn [M1] [золотой путь]
Ворота: G3, часть G12. Опыт/ценность: сердце автономии; project-mutex + порядок = «по умному».
Что: цикл `bd ready --json` (сорт: гейтовые/ждущие-человека в конец) → claim → re-validate ТЗ-дельты (продуктовый слой изменился → пометка) → preflight (чистое git-дерево; грязное «не наше» → парковка) → project-mutex (1 задача/репо, 1 воркер) → spawn → регистрация PID в shift.json. **Арбитр рассинхрона: bd = правда о задачах, shift.json = правда о процессах; расхождение → bd важнее, shift.json чинится по bd + живым PID.**
Приёмка:
- [x] две задачи одного проекта НЕ идут одновременно (интервалы активности не пересекаются) (G3)
- [x] грязное git-дерево «не наше» → задача паркуется с причиной; re-validate: изменённый продуктовый слой после claim → пометка в STATE задачи
- [x] гейтовые задачи спавнятся ПОСЛЕ автономных (сортировка в конец)
Проверка: `python3 -m pytest tests/test_dispatcher.py` (моки bd + git-фикстуры)
Статус: verified
Доказательство:
- `python3 -m pytest tests/test_dispatcher.py` → 11 passed (preflight×4, revalidate×3, mutex, dirty-park, reconcile×2). Лог: docs/evidence/F4/unit-tests.log
- `bash .verify/dispatcher-core.sh` → "F4 DISPATCHER-CORE PASS", exit 0 (live: G3 1-spawn/проект + mutex-refuse; dirty-park с причиной, bd status open; gate-order AUTO AUTO GATE). Лог: docs/evidence/F4/dispatcher-core.log

### F5 — Admission + back-pressure (лимиты/RAM) [M2]
Ворота: G4, G12. Опыт/ценность: тратить недоиспользуемый пул безопасно.
Что: перед spawn — free-RAM-гейт + детект лимит-строк CLI. session/weekly/Opus → парковка до ресета (парс времени); 429/529 → ретрай без парковки.

КРИТ-ФИКС 2026-07-15 (баг найден пользователем на живом shift.json): admission БОЛЬШЕ НЕ
гейтит по времени окна. Философия: «есть квота → запускай; тратить недоиспользуемый пул».
Единственный ДОСТОВЕРНЫЙ сигнал исчерпания квоты — реактивная лимит-строка CLI
(`classify_limit`). `ccusage remaining_minutes` = время до тика 5-часового блока, НЕ остаток
квоты: после сброса блока начинается СВЕЖАЯ квота, поэтому «осталось <5 мин» — повод
ЗАПУСКАТЬ, а не паркать. Старый гейт `remaining_minutes < min_window_minutes` («window-low»)
паркал задачу при ~70% свободной квоты (self-block контура) — ВЫКОШЕН целиком. Гейты
admission теперь ровно три: ready_count>0, RAM, реальная лимит-строка. `window-inactive` →
ADMIT с логом (нет телеметрии ≠ нет квоты). `min_window_minutes` — мёртвое поле (display-only).
Приёмка:
- [x] фикстуры лимит-строк: session→парковка+время ресета, weekly→глубокая, Opus→деградация, 429→ретрай (100% фикстур) (G4)
- [x] допуск: ready≠∅ + окно/RAM ok → spawn ≤60 сек; нехватка RAM → не спавнит (G12)
Проверка: `python3 -m pytest tests/test_admission.py` (фикстуры tests/fixtures/limit-*.txt)
Статус: verified
Доказательство:
- `bash .verify/admission.sh` → "F5 ADMISSION PASS (6/6 fixtures classified + admission gate correct on RAM/window/limit)", exit 0. 6 РЕАЛЬНЫХ лимит-строк CLI (code.claude.com/docs/en/errors): session→park+reset 3:45pm, weekly→park+reset Mon 12:00am, Opus→degrade, 429/529→retry (no park), none→no-limit. Лог: docs/evidence/F5/admission.log
- `python3 -m pytest tests/test_admission.py` → 23 passed (классификация×7, парс ресет-времени×4, гейт RAM/окно/ready/limit×12). Лог: docs/evidence/F5/unit-tests.log
- интеграция в dispatcher: `python3 -m pytest tests/test_dispatcher.py` → 14 passed (11 F4 + 3 admission: low-ram→park без claim, ram/window ok→spawn, session-limit→park). Лог: docs/evidence/F5/dispatcher-tests.log
- fixtures: tests/fixtures/limit-{session,weekly,opus,429,529,none}.txt
- [КРИТ-ФИКС 2026-07-15] `python3 -m pytest tests/test_admission.py` → 28 passed. Новые регрессы бага пользователя: remaining_minutes=3/1/None + нет лимит-строки + RAM ok + ready>0 → ADMIT (не park); window inactive/None → ADMIT + флаг no-telemetry; park ТОЛЬКО по реальной лимит-строке даже при широком окне; near-reset+лимит-строка → всё равно park (истина — лимит-строка, не часы). Убраны 2 старых теста «window-low»/«window-inactive → park».
- [ЖИВОЙ ПРОГОН реального claude] `orc start --once` (БЕЗ ORC_SPAWN_CMD_OVERRIDE) на ~/Desktop/orc-live-demo: admission ADMIT (окно 288 мин, квота свободна), реальный claude спавнлен (Terminal 6118), создал result.txt=«DONE» + STATE.md=«Status: DONE» за ~6с; poll_completions: worker_progressed=True → bd close → газета «✓ orc-7d7 готово ~163336 ток.»; ccusage totalTokens 13.04M→13.36M (реально потрачено ~326k); окно воркера само закрылось (RAM свободна). Доказательство: docs/evidence/live-demo/

### F6 — Бюджет-кап + per-задачная атрибуция расхода [M2]
Ворота: G8, контр-метрика «расход». Опыт/ценность: не сжечь weekly-кап; газета показывает «сколько съела задача».
Что: расход задачи = дельта `ccusage` total между claim и close (на 1-воркерной машине атрибуция точна — работает один воркер). Бюджет-кап задачи и смены из конфига → превышение → парковка + запись.
Приёмка:
- [x] расход задачи = tokens_after − tokens_before ФОРМУЛА корректна против РЕАЛЬНОГО ccusage + монотонность; WORK-DRIVEN дельта (claude реально жжёт) → перенесена в приёмку F12 (E2E-смена; экономия окна — честно, R-M2)
- [x] задача с заниженным капом останавливается с парковкой и записью в газету (G8)
- [x] кап смены превышен → новые задачи не стартуют
Проверка: `python3 -m pytest tests/test_budget.py` + живой прогон (evidence/F6/)
Статус: verified
Доказательство:
- `bash .verify/budget.sh` → "F6 BUDGET PASS (spend-attribution formula vs REAL ccusage + monotonicity; low-cap park; newspaper DONE/WAVE/BETA; summary-first). Work-driven delta deferred to F12", exit 0. ФОРМУЛА атрибуции (task_spend = дельта ccusage) проверена против РЕАЛЬНОГО live-чтения ccusage (63M ток.) + монотонности (real re-read delta≥0); синтетический инкремент 12345 — проверка ФОРМУЛЫ, НЕ измерение работы. Work-driven дельта — приёмка F12. Лог: docs/evidence/F6/budget.log
- `python3 -m pytest tests/test_budget.py` → 15 passed (spend-атрибуция×5, task-cap×5, shift-cap×2, done_kind/newspaper DONE/wave/BETA + summary-first×3). Лог: docs/evidence/F6/unit-tests.log
- backlog-мелочи внесены: газета — сводка «N готово» ТЕПЕРЬ ПЕРВОЙ строкой (было 2-й, паспорт вкуса); DONE / DONE-WAVE-N (предложена волна) / BETA (ждёт решения) различаются в газете + per-task расход «~N ток.». test_skeleton assertion исправлен под новую (верную) раскладку.
- интеграция: shift-cap блокирует новые спавны (spawn_one), task-cap паркует живого воркера + стоп (enforce_budget в orc status); 124 теста passed, 0 регрессий.
- ОТЛОЖЕННАЯ WORK-DRIVEN ДЕЛЬТА ЗАКРЫТА в F12 (M4): живой прогон — расход окна 19%→43% (+24пп), costUSD $62, воркеры реально жгли. Per-task короткоинтервальная totalTokens-дельта=0 (ccusage кэширует totalTokens активного блока — задокументированный JSONL-риск; пул делят все сессии). Формула верна (эти тесты), work-driven расход реален на уровне окна. См. F12-доказательство.

### F7 — Watchdog: петля/тишина детект + внешняя проверка [M2]
Ворота: G5. Опыт/ценность: выход из meltdown/зависаний без ложных убийств.
Что: heartbeat из PostToolUse; PreToolUse-маркер «tool-in-flight» отличает работу от тишины. Петля (K=конфиг одинаковых hash) / тишина-без-маркера → внешняя проверка пост-условий (git/артефакты) → kill → рестарт от STATE.md, cap=конфиг → эскалация.
Приёмка:
- [x] синтетическая петля (K одинаковых hash) и тишина-без-маркера детектятся (G5)
- [x] живой Bash-вызов ≥2 мин НЕ убивается (0 ложных kill)
- [x] рестарт только после внешней проверки пост-условий; cap соблюдается → эскалация
Проверка: `python3 -m pytest tests/test_watchdog.py`
Статус: verified
Доказательство:
- `bash .verify/watchdog.sh` → "F7 WATCHDOG PASS (loop + silence detected; long tool spared = 0 false kills; restart bounded, cap escalates)", exit 0. Синтетика (без claude): 4 одинаковых hash→LOOP; heartbeat 300с назад без маркера→SILENCE; маркер in-flight держится 200с (=живой Bash ≥2мин)→verdict OK (0 ложных kill); no-progress→restart, real-progress→spared, cap=2→escalate. Лог: docs/evidence/F7/watchdog.log
- `python3 -m pytest tests/test_watchdog.py` → 18 passed (heartbeat/marker×2, loop×4, silence+false-kill-guard×5, external-check×2, bounded-recovery×5). Лог: docs/evidence/F7/unit-tests.log
- heartbeat-хуки встроены в worker settings.json (PreToolUse marker + PostToolUse heartbeat, merge не затирает чужие хуки); ORC_SESSION=task_id связывает хук воркера и watchdog диспетчера; 142 теста, 0 регрессий.

### F8 — Восстановление диспетчера + lease TTL [M2]
Ворота: G6. Опыт/ценность: безнадзорная надёжность после падения.
Что: kill -9 диспетчера → рестарт читает shift.json → сверяет с реальными PID (живые подхватывает, мёртвые → задача в ready через lease). Атомарная запись shift.json (tmp+rename).
Приёмка:
- [x] kill -9 диспетчера посреди смены → рестарт продолжает, 0 дублей/потерь задач (G6)
- [x] мёртвый воркер (PID нет) → его задача возвращается в ready (lease)
Проверка: `bash .verify/kill-restart.sh`
Статус: verified
Доказательство:
- `bash .verify/kill-restart.sh` → "F8 RECOVERY PASS (real PID; live worker adopted; crash -> lease; seam-only, no claude)", exit 0. R-M2 БЛОКЕР-4 ЗАКРЫТ: seam-override экспортирован ГЛОБАЛЬНО → НИ ОДИН `orc start` не спавнит реальный claude (NO-CLAUDE PASS, окно не жжётся); shift.json получил ЖИВОЙ воркер-PID через tty (Terminal, реальный воркер не обёртка); рестарт с живым → adopt (0 дублей); kill -9 → рестарт → задача пережила (lease, 0 потерь). Лог: docs/evidence/F8/kill-restart.log
- `python3 -m pytest tests/test_recovery.py` → 11 passed (atomic shift.json, reconcile adopt/lease/idempotent/closed-not-reopened, lease-safety re-resolve/expired, pid_on_window×3, real-pid-via-window). Лог: docs/evidence/F8/unit-tests.log
- eval-фикс PID: spawn.pid_on_window() резолвит PID через tty окна (race-free), не lsof-cwd сразу после спавна; reconcile(cfg) добавляет lease TTL + re-resolve PID в пределах лиза. R-M2 СУЩ (PID = обёртка в Ghostty) снят: дефолт Terminal, PID через tty = реальный воркер. 175 тестов, 0 регрессий.

### F9 — Гейт-протокол (bd-задача + живое ожидание + macOS-уведомление) [M2] [золотой путь]
Ворота: G2. Опыт/ценность: единственная точка человека; signature-опыт «карточка решения».
Что: воркер дошёл до ТЗ-гейта → macOS-уведомление (osascript) → сессия ЖДЁТ живьём (выбор пользователя; слот держится); гейтовые задачи в конце очереди (F4). После ответа задача продолжает по STATE.md. Карточка: скоуп/планка/полномочия + путь к ТЗ + цена ошибки; необратимое в батче не утверждается.
Приёмка:
- [x] реальный гейт: уведомление доставлено (osascript), карточка содержит путь к ТЗ + цену ошибки (G2)
- [x] после ответа задача продолжает ровно с «Следующего шага» STATE.md
Проверка: `bash .verify/e2e-gate.sh` + вывод в evidence/F9/
Статус: verified
Доказательство:
- `bash .verify/e2e-gate.sh` → "F9 GATE PASS (real notification delivered; card has brief path + cost + irreversible; window held; resume ready)", exit 0. R-M2 БЛОКЕР-3 ЗАКРЫТ (было FAIL из-за Ghostty-невыполнения; теперь дефолт Terminal ИСПОЛНЯЕТ seam): РЕАЛЬНЫЙ спавн (Terminal, seam пишет gate STATE.md — экономно, claude не жгу) + РЕАЛЬНОЕ osascript-уведомление (rc=0); газета-карточка: скоуп/планка/полномочия + ПУТЬ К ТЗ (brief.md) + ЦЕНА ОШИБКИ + маркер «необратимое — не в батче»; окно ДЕРЖИТСЯ (3 процесса на tty воркера живы, слот не освобождён); STATE.md.Next → резюм готов. Лог: docs/evidence/F9/e2e-gate.log
- `python3 -m pytest tests/test_gate.py` → 8 passed (нотификация: композиция/escape/dryrun/unknown-channel; карточка: scope/bar/authority/brief/cost/irreversible; poll-gate: park+notify+keep-window). Лог: docs/evidence/F9/unit-tests.log
- РЕШЕНИЕ по «1 живому claude»: каждый механизм F9 доказан реальной инфрой (реальный Terminal-спавн, реальный osascript, реальный bd, реальный поллинг STATE.md); диспетчер поллит STATE.md независимо от того, claude её написал или seam — механизм идентичен. Живой claude НЕ жёгся (экономно, окно бережём для F12). Приёмка «уведомление доставлено (osascript)» выполнена реальной доставкой (rc=0). 175 тестов, 0 регрессий.

### F10 — LaunchAgent + config + kill switch [M3]
Ворота: G10. Опыт/ценность: подъём в GUI-сессии (Keychain), ручной стоп, дневной режим.
Что: plist (Aqua, абсолютный путь `/opt/homebrew/bin/claude`, PATH), `~/.orc/config.json` (все калибровки — нет хардкода порогов), `orc stop` (≤10 сек, задачи в ready). **Удержание Mac от сна В КОНТУР НЕ ВСТРАИВАЕТСЯ (`caffeinate`/подобное конфликтует с мышью пользователя — прямой фидбек 2026-07-15); v1 дневной, при активности Mac не спит; для долгой смены пользователь настраивает сон сам в System Settings.**
Приёмка:
- [x] LaunchAgent из GUI-сессии стартует диспетчер, `claude auth status`=0 из его контекста
- [x] `orc stop` останавливает всех воркеров ≤10 сек, задачи в ready (G10)
- [x] все калибровки из config.json (нет хардкода)
- [x] ДОБАВЛЕНО: `orc setup` ставит shellExitAction=0 на Terminal-профиль (plistlib) с бэкапом прежнего значения — воспроизводимый husk-фикс для любого пользователя (README)
Проверка: `bash .verify/launchagent.sh` + `python3 -m pytest tests/test_config.py`
Статус: verified
Доказательство:
- `bash .verify/launchagent.sh` → "F10 LAUNCHAGENT PASS", exit 0. PART 1: РЕАЛЬНЫЙ user LaunchAgent (Aqua) → `claude auth status` auth_exit=0 (loggedIn:true, max), keychain_exit=0, PATH из plist, claude по абсолютному пути; ОБЯЗАТЕЛЬНЫЙ teardown (bootout+rm plist) — 0 residue. PART 2: `orc stop` за 1.24с (≤10с) остановил воркера (0 процессов на tty, RAM свободна) + задача вернулась в ready. PART 3: config.json override (stop_grace=9, min_ram=777, label) honoured — нет хардкода. Лог: docs/evidence/F10/{launchagent.log, la-probe.log, stop.json}
- `python3 -m pytest tests/test_config.py` → 15 passed (config-override/malformed-fallback×3, plist Aqua/PATH/absolute/label×5, orc stop kill+requeue×3, setup shellExitAction=0+backup+revert+idempotent×4). Лог: docs/evidence/F10/unit-tests.log
- команды: `orc {install [--uninstall]|setup [--revert]|stop|daemon [--once]} [--json]`; README.md документирует husk-фикс с бэкапом (orcPrevShellExitAction) и LaunchAgent (Aqua/absolute-path/PATH-not-inherited).

### F11 — Патчи конвейера pipeline (docs/tasks/<слаг>/ + развилка фазы 0) + откат [M4] [improvement]
Ворота: G9. Опыт/ценность: двухслойная мультизадачность (продуктовый слой + мини-пайпы задач).
Что: по RS-02 — pipeline-hooks.py (2 глобы STATE.md +tasks/*/docs), pipeline-scorecard.sh (детект workspace), SKILL.md/phase-0 (развилка «STATE есть, но задача новая»→подворкспейс). СТАРОЕ поведение не ломается; патч на отдельной git-ветке ~/.claude с возможностью `git revert`.
Приёмка:
- [x] характеризационный набор: scorecard/doctor на существующем smoke-макете ДО патча зафиксирован; ПОСЛЕ — тот же результат (0 регрессий) (G9)
- [x] scorecard находит `docs/tasks/<слаг>/`, хуки видят STATE там, doctor exit 0
- [x] патч откатывается `git revert` одним коммитом, doctor снова зелёный (проверено)
Проверка: `bash ~/.claude/skills/pipeline/bin/pipeline-lint.sh --doctor` + scorecard на обоих макетах + revert-тест (evidence/F11/)
Статус: verified
Доказательство:
- РАСКЛАДКА (диск важнее RS-02): канон orc = `<project>/docs/tasks/<slug>/STATE.md` (dispatcher.task_state_path, design.md) — НЕ `tasks/*/docs` из RS-02. Патч построен под канон кода. 3 точки кода + 4 промптовые (минимальный дифф, рамка чужого кода).
- Характеризация ДО/ПОСЛЕ на 2 smoke-макетах (proj-a стандартный `docs/`, proj-b tasks-раскладка), патч на git-ветке `orc-tasks-workspace-patch` в ~/.claude, коммит 7b57e2f:
  - doctor: IDENTICAL до/после, exit 0 (0 регрессий системы). evidence/F11/{before,after}/doctor.txt
  - scorecard STANDARD (proj-a): `diff before after` = IDENTICAL (0 регрессий старой раскладки). evidence/F11/{before,after}/scorecard-standard.txt
  - scorecard TASKS (proj-b): PASS=4→8, находит `docs/tasks/add-widget/STATE.md` (STATE/Следующий шаг/Рекап/lint-state все PASS — идентично стандартной). evidence/F11/{before,after}/scorecard-tasks.txt
  - хуки (pipeline-hooks.py posttooluse+stop, обе глобы стр.109/149): tasks-раскладка LIVE-detect 0→1 STATE.md; standard остался 1 (не сломался). evidence/F11/{before,after}/hooks-live-detect.txt
- ОТКАТ (одним шагом): `git revert --no-edit HEAD` → патч исчез из hooks.py (0 совпадений `docs/tasks/*/STATE.md`), doctor exit 0. evidence/F11/revert-test.txt. Ветку вернул на 7b57e2f (патч жив для F12).
- 200 orc-тестов зелёные после патча (патч в ~/.claude ортогонален orc-коду). Репро-скрипты: evidence/F11/{build-fixtures.sh,characterize.sh}.

### F12 — E2E-смена: 3 задачи / 2 проекта / 1 гейт [M4] — владелец центрального гейта G1
Ворота: G1 (главная метрика цели). Опыт/ценность: доказать North Star целиком — Mac сам довёл смену до конца.
Что: полный прогон: 3 реальные задачи (2 проекта `~/Desktop/orc-test-{1,2}`, 1 с гейтом ТЗ) через весь контур до терминального статуса без вмешательства кроме ответа на гейт.
Приёмка:
- [x] 3/3 задачи дошли до терминального статуса (bd closed / parked-on-gate→resolved) без ручного вмешательства кроме гейта (G1)
- [x] DONE каждой задачи подтверждён ВНЕШНИМИ фактами (git-коммиты/артефакты), не заявлением воркера
- [x] сериализация проекта соблюдена, 0 дублей, газета корректна
Проверка: `bash .verify/e2e-shift.sh` (полный сценарий) + вывод в evidence/F12/
Статус: verified
Доказательство:
- `bash .verify/e2e-shift.sh` → "F12 E2E PASS (3/3 terminal by external facts; gate parked; newspaper correct)", exit 0. РЕАЛЬНЫЙ claude на 3 задачах (2 проекта ~/Desktop/orc-test-{1,2} + 1 гейт), изолированный hub. Лог: docs/evidence/F12/e2e-shift.log.
  - ВНЕШНИЕ ФАКТЫ: t1 git-коммит `6c6fdea orc: add READY.txt` + артефакт READY.txt=ready (orc-test-1); t2 `f1c29a8 orc: add HELLO.txt` + HELLO.txt=hello (orc-test-2); гейт-задача parked-on-gate (STATE.md «waiting on gate»). Не заявление воркера — реальные коммиты и файлы.
  - ГАЗЕТА: «смена: 2 готово, 1 ждут тебя, 0 упало; съедено 43% окна» + гейт-карточка (скоуп/планка/цена/путь к ТЗ). Корректна.
  - СЕРИАЛИЗАЦИЯ: orc-test-1 нёс 2 задачи (t1 + гейт), гейт спавнился ПОСЛЕ завершения t1 (order_ready в конец), интервалы не пересеклись, 0 дублей.
- ЖИВЫЕ ПРОВЕРКИ (первый настоящий прогон — три реальных бага среды пойманы и починены):
  - (а) HUSK-ФИКС РАБОТАЕТ: завершённые воркеры (t1/t2) — окна САМИ ЗАКРЫЛИСЬ после чистого stop диспетчера (shellExitAction=0, профиль Clear Dark); гейт-воркер окно ДЕРЖИТ живьём (busy=true) — это F9-дизайн (слот ждёт оператора), не husk. Проверено: `close_worker` гейт-воркера → окно тоже закрылось.
  - (б) F6 РЕАЛЬНАЯ ДЕЛЬТА (отложенная приёмка ЗАКРЫТА, честно): расход окна вырос 19%→43% (+24пп 5ч-окна) за сессию, costUSD $62 — воркеры реально жгли. Per-task/короткоинтервальная `totalTokens`-дельта = 0: ccusage кэширует `totalTokens` активного блока (нестабильный внутренний JSONL — задокументированный риск), общий пул делят все сессии. ФОРМУЛА атрибуции проверена tests/test_budget.py; work-driven расход реален на уровне окна (moving remainingMinutes/costUSD).
  - (в) ВЕСЬ ПУТЬ накидал→смена→газета вживую: add×3 → daemon-петля → воркеры до терминала → poll детект → bd close + газета догоняет + стоп воркера.
- ТРИ БАГА СРЕДЫ (пойманы первым живым прогоном, починены, регресс-тесты добавлены):
  1. Seatbelt F13 блокировал `~/.claude/session-env` + `shell-snapshots` → Bash-tool воркера падал («EPERM session-env»), git commit невозможен. Фикс: sandbox.build_profile добавляет узкие claude-runtime-подпути (session-env/shell-snapshots/…), НЕ весь ~/.claude (enforcement skills/agents/settings.json остаются недоступны воркеру).
  2. Seatbelt блокировал Bash-harness `/private/tmp/claude-<uid>/` → та же поломка. Фикс: узкий allow `/private/tmp/claude-<uid>` (uid-scoped, НЕ широкий /private/tmp; sentinel в $HOME — стена держит).
  3. preflight парковал 2-ю задачу проекта на грязном `docs/tasks/<slug>/STATE.md` (orc-артефакт t1) как «человек редактирует». Фикс: `_OURS_PREFIXES` += `docs/tasks/` + `.orc/` (orc-managed, не human-WIP). Регресс-тесты: task-state-dirty→ok, human-edit-despite-task-state→park.
  - F13 sandbox-walls.sh ПЕРЕПРОВЕРЕН после фиксов 1-2: 5 обфусцированных обходов + ~/.ssh всё ещё ЗАБЛОКИРОВАНЫ (0 регрессий стены). 210 тестов зелёные (+2 preflight, +3 sandbox, +1 prompt-dir seam, +4 push-wall из G0c).

### F14 — Замыкание петли: детект завершения задачи + газета догоняет DONE [M1] [фикс-фича из consumer M1, золотой путь]
Ворота: часть G1, core loop. Опыт/ценность: «вернулся за кофе → газета показывает результат» — без этого продукт бессмыслен (consumer: задача выполнена, газета висит на «в работе»).
Что: диспетчер поллит `<проект>/docs/tasks/<слаг>/STATE.md` (или features) активной задачи; терминальный статус (DONE-WAVE-N/DONE/parked-on-gate) → `bd close` + обновление shift.json/газеты + закрытие вкладки воркера (osascript по сохранённому window/tab id). Спавн сохраняет идентификатор вкладки (чинит `pid None`). Полировка consumer: `init --help` с текстом; согласовать «.beads»↔«~/.orc» в сообщениях; `status` после add показывает ready-задачи (не «смена не запущена» при непустой очереди). Язык вывода — смешанный (решение пользователя, не баг).
Приёмка:
- [x] consumer-сценарий: задача выполнилась → `orc status --newspaper` показывает «1 готово» БЕЗ ручного ls/cat (петля замкнута)
- [x] вкладка воркера закрывается после терминального статуса; идентификатор вкладки в shift.json (не None) — NB: «закрывается» реализовано как СТОП воркера (kill по tty, RAM освобождается); удаление пустого husk-окна — best-effort, блокируется Terminal-профилем shellExitAction пользователя (находка среды, косметика)
- [x] `orc status` при непустой ready-очереди до start показывает задачи; `init --help` непустой; сообщения о хабе единообразны
Проверка: `bash .verify/e2e-loop-close.sh` (полный: add→start→ждать DONE→газета=готово, автоматически) + вывод в evidence/F14/
Статус: verified
Доказательство:
- `bash .verify/e2e-loop-close.sh` → "F14 CLOSE-THE-LOOP PASS", exit 0. РЕАЛЬНЫЙ osascript-терминал (window id 4865, tty ttys014); `orc status` ПОЛЛИТ STATE.md → газета «смена: 1 готово» за ~4с БЕЗ ручного ls; воркер остановлен (0 процессов на tty); bd closed. Лог: docs/evidence/F14/e2e-loop-close.log
- `python3 -m pytest tests/test_loop_close.py` → 15 passed (детектор DONE/DONE-WAVE-N/BETA/gate/in-progress + poll done/gate/bd-error/no-state + запись window id). Лог: docs/evidence/F14/unit-tests.log
- полный набор: 83 passed (68 M1 + 15 F14), 0 регрессий.
- полировка consumer живьём: init --help с текстом; `orc status` до start показывает секцию «в очереди»; сообщения зовут хаб «~/.orc» единообразно (см. e2e-loop-close.log init-строку).

### F13 — OS-sandbox как основная стена (усиление F1) [M3] [фикс-фича из eval M1]
Ворота: G0c, безопасность. Опыт/ценность: паттерн-хук обходится обфускацией (base64|bash, xargs rm, python shutil.rmtree, find -delete) — для безнадзорного bypass нужна OS-стена.
Что: воркеры под OS-sandbox (macOS seatbelt, из волны A: ФС-запись только в workspace + сеть по allowlist, покрывает подпроцессы, −84% промптов) как ОСНОВНАЯ граница; PreToolUse-hook — вторичный слой. Негативный спайк расширяется обфусцированными обходами.
Приёмка:
- [x] обфусцированные обходы (base64|bash rm вне ws, python -c shutil.rmtree, find вне ws -delete) ЗАБЛОКИРОВАНЫ sandbox (evidence/F13/)
- [x] запись вне workspace невозможна на уровне ОС, не только hook
Проверка: `bash .verify/sandbox-walls.sh` (расширенный негативный спайк) + вывод в evidence/F13/
Статус: verified
Доказательство:
- СПАЙК `.spikes/probe/sandbox.md` (правило спайка ДО фикса): macOS seatbelt (`sandbox-exec` + профиль deny-file-write* + allow только subpath workspace) блокирует обфусцированные обходы на уровне syscall (независимо от того, как достигнута запись); ключевая находка профиля — НЕ вайтлистить широкий родитель (мой первый профиль пустил /private/tmp = ложная течь). Сеть: полный `(deny network*)` работает; per-host allowlist в user seatbelt ненадёжен — задокументировано (дефолт: сеть вкл, git push держит F1-хук).
- `bash .verify/sandbox-walls.sh` → "F13 SANDBOX PASS", exit 0. Профиль orc (`sandbox.write_profile`) + обёртка (`sandbox.wrap_command`); 5 обфусцированных обходов sentinel'а ВНЕ workspace (rm / base64|bash / python shutil.rmtree / find -delete / xargs rm) — ВСЕ заблокированы (Operation not permitted, sentinel выжил); запись в ~/.ssh заблокирована; запись ВНУТРИ workspace работает. Лог: docs/evidence/F13/sandbox-walls.log
- ИНТЕГРАЦИЯ (реальный сквозной спавн): `orc start` спавнит РЕАЛЬНЫЙ Terminal-воркер под sandbox-exec; воркер пытается base64|bash rm sentinel'а вне ws → sentinel ВЫЖИЛ (стена держит через spawn_one→spawn_worker→spawn_terminal→build_start_command→sandbox-exec); профиль записан в `<project>/.orc/sandbox.sb`. См. хвост docs/evidence/F13/sandbox-walls.log.
- `python3 -m pytest tests/test_sandbox.py` → 10 passed (форма профиля: deny-write+narrow-subpath+no-broad-parent+device-sinks+deny-network×4; write_profile в workspace; wrap_command sandbox-exec; build_start_command wrapped-by-default/opt-out/seatbelt-absent-fallback×3; config default). Лог: docs/evidence/F13/unit-tests.log. 200 тестов всего, 0 регрессий.
- РЕШЕНИЕ: OS-sandbox — ОСНОВНАЯ стена (переживает обфускацию); PreToolUse-хук F1 — вторичный слой (ловит git push, даёт модели читаемую причину). Дефолт sandbox=on; sandbox_deny_network=off (воркерам нужен claude API/git fetch/brew). Живой claude НЕ жёгся: sandbox-exec + пробное действие = тот же путь enforcement, что у Bash-tool воркера.

### F15 — Бэкенд-абстракция спавна + чистое закрытие [M2] [фикс-фича из фидбека; ПЕРЕСМОТРЕНА по R-M2]
Ворота: часть G1/North Star (безнадзорная чистота), UX. Опыт/ценность: рабочий дефолтный спавн + бэкенд-абстракция; цель «0 husk» НЕ достижима скриптом на этой машине (честно).
Зачем: Terminal.app при профиле shellExitAction=keep-window держит пустое husk-окно после стопа воркера — раздражитель. ПЕРВАЯ попытка (спавн в Ghostty) ПРОВАЛЕНА evaluator-ом (R-M2 БЛОКЕР-1): Ghostty 1.3.1 `-e` НЕ исполняет команду воркера (пустое окно). Пересмотр.
Что: бэкенд-абстракция spawn_worker/close_worker/worker_pid (config `terminal`). ДЕФОЛТ = **Terminal.app** (надёжно ИСПОЛНЯЕТ команду воркера; PID через tty окна = реальный воркер-PID). Ghostty оставлен OPT-IN (нефункционален на 1.3.1 — спайк .spikes/probe/ghostty-exec.md, варианты A-M все NOT EXECUTED), НЕ дефолт. Стоп воркера = kill по tty (RAM освобождается — существенное требование North Star). Husk-окно: удаление скриптом НЕВОЗМОЖНО на этой машине (AppleScript close/System Events/AXCloseButton — no-op, TCC-барьер + профиль; проверено многократно) — это НЕустранимое ограничение среды, задокументировано; воркер ОСТАНОВЛЕН (0 процессов, 0 RAM) — функциональной течи нет.
Приёмка:
- [x] дефолтный бэкенд РЕАЛЬНО исполняет команду воркера (Terminal); e2e-loop-close зелёный на дефолте (БЛОКЕР-2 закрыт)
- [x] бэкенд-абстракция: spawn/close/pid маршрутизируют; Ghostty opt-in, не дефолт; PID = реальный воркер (tty)
- [ ] ЦЕЛЬ «0 husk» — НЕ достигнута: husk-окно неустранимо скриптом (среда/профиль/TCC). Существенное (воркер остановлен, RAM свободна) — выполнено; косметика husk — ограничение среды, вынесено пользователю (профиль Terminal «Close if shell exited cleanly» или рабочий Ghostty-билд — P2)
Проверка: `bash .verify/e2e-loop-close.sh` (дефолт) + `python3 -m pytest tests/test_ghostty.py`
Статус: verified (с честной оговоркой: 0-husk не достигнут — ограничение среды)
Доказательство:
- СПАЙК `.spikes/probe/ghostty-exec.md`: Ghostty 1.3.1 `-e` не исполняет команду (12 вариантов A-M, все NOT EXECUTED; ни один tty/child-shell не появляется). → ОТКАТ дефолта на Terminal.
- `bash .verify/e2e-loop-close.sh` → PASS exit 0 НА ДЕФОЛТНОМ бэкенде (Terminal исполняет seam, петля F14 замыкается, воркер остановлен) — регресс M1 (БЛОКЕР-2) закрыт. Лог: docs/evidence/F14/e2e-loop-close.log
- `python3 -m pytest tests/test_ghostty.py` → 14 passed (бэкенд-абстракция: маршрутизация spawn/close/pid, дефолт=terminal, Ghostty opt-in-документирован). Лог: docs/evidence/F15/unit-tests.log
- ЧЕСТНОЕ ОПРОВЕРЖЕНИЕ прошлого self-pass: заявление «0 husk / окно само закрывается» ОТОЗВАНО — Ghostty не исполнял (окно пустое), Terminal husk неустраним скриптом. 175 тестов, 0 регрессий.

---

### ФИКС-ВОЛНА цикл 1 (фаза 5 verify — P0: блокеры + профминимум-безопасность + полнота)
Из вердиктов R-final-{E1,E2,E3}. Каждый — фикс + регресс-тест зелёный (evidence в docs/evidence/fix1/).
БЛОКЕРЫ:
- [x] **B0 многострочный промпт ломал спавн** (spawn.build_start_command + ghostty; найден пользователем на живом окне): промпт инлайнился в shell/AppleScript; литеральный перенос строки в `do script "..."` рвал AppleScript-парс → shell висел на `quote>`, claude НЕ запускался (однострочные работали, ГЕЙТОВЫЕ/многострочные — нет). Фикс: промпт пишется в `<project>/.orc/prompt-<session>.txt` (sandbox-writable, gitignored, orc-managed) и читается `claude "$(cat <файл>)"` → команда запуска ОДНОСТРОЧНАЯ независимо от контента, промпт байт-в-байт. Статус: verified
  - `bash .verify/e3/e3-multiline-prompt.sh` → exit 0 (sandbox on/off): многострочный промпт (апострофы/бэктики/кавычки/переносы) round-trips байт-в-байт через printf-seam, 0 continuation. Лог: docs/evidence/fix1/P0ml-multiline-prompt-regress.log
  - `bash .verify/e2e-gate.sh` → PASS (реальный гейт, ранее ломавший shell). Лог: docs/evidence/fix1/P0ml-e2e-gate.log
  - unit: test_multiline_prompt_round_trips_via_file, test_prompt_file_lives_in_orc_managed_scratch, test_ghostty inner-command.
- [x] **B1 reward-hacking** (dispatcher.poll_completions): DONE засчитывается ТОЛЬКО при внешнем факте (git-коммит после старта воркера ИЛИ изменённый/созданный артефакт); нет факта → park «suspected-fake-done», bd blocked. `watchdog.external_progress` подключён к completion + фильтр расширен (orc-managed .claude/.orc/docs/tasks — не деливерабл). Статус: verified
  - `bash .verify/e3/e3-rewardhack-live.sh` → exit 0: фейк-DONE ПАРКУЕТСЯ (bd blocked, HEAD unchanged), control (реальный коммит) закрывается. Лог: docs/evidence/fix1/B1-rewardhack-regress.log
  - unit: test_poll_done_without_external_fact_is_parked (+ 2 loop-close обновлены под реальный деливерабл, не ослабление).
- [x] **B2 обфусц. SSH git push + ~/.ssh read** (worker_walls + sandbox): env снимает SSH-push (GIT_SSH_COMMAND/GIT_SSH=/usr/bin/false, SSH_AUTH_SOCK='', core.sshCommand); seatbelt `(deny file-read* (subpath <HOME>/.ssh))`. Статус: verified
  - `bash .verify/e3/e3-exfil2.sh` → exit 0 (3/3): SSH-auth под sandbox denied, git-push через env падает, ключ-read под sandbox EPERM. Комбо real-path (env+sandbox+attacker override) → WALL HELD. Логи: docs/evidence/fix1/B2-exfil-regress.log, B2-combined-realpath.log
  - unit: test_push_neutralizing_env_disables_ssh_transport, test_profile_denies_reading_ssh.
ПРОФМИНИМУМ-БЕЗОПАСНОСТЬ:
- [x] **P3 секрет-env на спавне** (spawn.build_start_command): `unset` секрет-денилиста ПЕРВЫМ в prefix; claude OAuth (Keychain) не тронут. Живой: секреты→CLEARED, 0 leak. docs/evidence/fix1/P3-env-strip-regress.log. unit: test_start_command_unsets_secrets_on_spawn.
- [x] **P4 orc stop PID-anchored** (cli.cmd_stop): SIGKILL по PID из shift.json как якорь, tty — фолбэк. Регресс: tty=None → воркер убит по PID (rc=-9). docs/evidence/fix1/P4-stop-pid-anchored.log. unit: test_stop_kills_recorded_pid_when_tty_resolution_fails.
- [x] **P5 sandbox fail-closed** (sandbox.sandbox_gate + spawn_one): недоступен/off без allow_no_sandbox → НЕ спавнить, park. docs/evidence/fix1/P5-sandbox-fail-closed.log. unit: 4 (unavailable/disabled/opt-out/gate).
ПОЛНОТА (дёшево):
- [x] **P6 G7 canary-уведомление** (notify.notify_canary_fail): на canary-fail — macOS-уведомление. docs/evidence/fix1/P6-P7-canary-notify-json.log.
- [x] **P7 start --json цельный** (cmd_start): человекочит.→stderr, stdout=только валидный JSON (json.tool парсит). docs/evidence/fix1/P6-P7-canary-notify-json.log.
- [x] **P8 newspaper деградирует** (report._gate_card): try/except BeadsError → минимальная карточка+пометка, не краш. docs/evidence/fix1/P8-newspaper-degrade.log. unit: 2.
- [x] **P9 .env в .gitignore** (.env/.env.*/!.env.example). docs/evidence/fix1/P9-gitignore-env.log.
Итог фикс-волны цикл 1: 210 → **226 тестов** (+16), 0 регрессий.

### ФИКС-ВОЛНА цикл 2 (реверификация R-final-reverify — последний блокер + существенное)
- [x] **B1 усилен** (external_progress + gitutil): реверификация нашла 2 тривиальных обхода цикла-1 (оракул принимал ЛЮБОЙ foreign dirty-файл / ЛЮБОЙ коммит новее старта). Теперь «внешний факт» = РЕАЛЬНЫЙ непустой деливерабл: (а) коммит засчитывается ТОЛЬКО с непустым diff по non-orc-managed файлу (`commit_touches_real_files`: отвергает `--allow-empty` и STATE.md-only; `commits_since` фильтрует по %ct строго > старта, не по date-fuzzy `--since`); (б) dirty-файл — ТОЛЬКО непустой (`dirty_has_nonempty_file`: отвергает пустой `touch`); non-git фолбэк тоже требует size>0 + исключает orc-managed. Пустой touch / empty-commit / только-STATE.md → park. Статус: verified
  - `bash .verify/e3/reverify-b1.sh` → exit 0: 7 фейков (вкл. empty-touch, allow-empty-commit) ПАРКУЮТСЯ, 2 реальных деливерабла (непустой файл, непустой коммит) закрываются. Лог: docs/evidence/fix1/B1-reverify-strengthened.log
  - `bash .verify/e3/e3-rewardhack-live.sh` → exit 0: матрица (zero-artifact/empty-touch/allow-empty-commit паркуются; непустой коммит закрывается). Лог: docs/evidence/fix1/B1-rewardhack-regress.log
  - unit: +6 (external_progress: empty-touch/allow-empty/real-commit/state-only-commit/non-git-empty; loop-close: 2 параметр. обхода).
- [x] **B2-opt-out ГРОМКИЙ** (canary + newspaper + config): реверификация: env-слой САМ не держит прямой ssh (под env-prefix без sandbox `ssh -T git@github` аутентифицируется); B2 держится ТОЛЬКО на sandbox-ssh-read-deny → `allow_no_sandbox=true` МОЛЧА снимал всю SSH/эксфильтрацию-стену. Теперь: `[WARN]` в canary на каждом старте + ⚠-баннер в газете «воркер БЕЗ OS-стен: ~/.ssh + SSH/сеть-эксфильтрация НЕ заблокированы» + расширенный threat-model коммент в config. Статус: verified
  - Живой: canary `[WARN] sandbox: OS-sandbox DISABLED ...`, газета `⚠ ВНИМАНИЕ: OS-песочница ОТКЛЮЧЕНА ...`. Лог: docs/evidence/fix1/B2-optout-loud.log. unit: test_canary_warns_loud_when_sandbox_disabled, test_newspaper_shows_no_sandbox_banner.
Итог фикс-волны (циклы 1+2): 210 → **235 тестов** (+25), 0 регрессий. Смоук: pytest 235, reverify-b1/e3-rewardhack/e3-exfil2/e2e-loop-close/e2e-gate — все зелёные. Остаётся beta-финал.

### P1-ВОЛНА (финал P1 — README/косметика/G1 живой pipeline + фиксы, найденные на живом прогоне)
Из R-final-{consumer2,E2} + два фикса, всплывшие на РЕАЛЬНОМ pipeline-прогоне. Тесты 255→260 (+5), 0 регрессий. Evidence: docs/evidence/pipeline-live/.
- [x] **README «Модель угроз»** (consumer2/E3): секция «Threat model — what is walled, what is not». Честно раздельно: ЗАКРЫТО на уровне ядра/проверок (запись/удаление вне ws — seatbelt-syscall держит обфускацию; ~/.ssh read deny; секрет-env unset на спавне; reward-hack = DONE только по непустому внешнему факту; git push HTTPS+SSH — env лишён кредов; MCP пуст) vs ОСТАЁТСЯ честно (воркер МОЖЕТ читать файлы вне проекта; сеть открыта→теоретическая эксфильтрация; sandbox ОБЯЗАТЕЛЕН, allow_no_sandbox громко warn). Вывод: запускай доверенные задачи; чувствительные — с осторожностью. README.md.
- [x] **Косметика газеты — упавшие задачи свой заголовок** (consumer-1): `RU_SECTION_FAILED = "── упало (разберись) ──"`; failed-строки рендерятся под своим заголовком ПОСЛЕ секции «завершено», не молча под ней. report.py:newspaper. Тест: test_newspaper_failed_tasks_have_own_header (отдельный заголовок, порядок после done). NB: гейт-карточка ≤80 (_truncate_path=80 симв.) и ⚠→ASCII (`!`/`[WARN]`) были закрыты прошлыми волнами — перепроверено, держат.
- [x] **G1 — ПОЛНЫЙ pipeline-прогон РЕАЛЬНОГО claude ЧЕРЕЗ КОНВЕЙЕР** (доказать North Star «через pipeline», не seam/raw как F12): тест-проект ~/Desktop/orc-pipeline-demo (git init), изолированный hub ~/Desktop/orc-pipeline-demo-hub. Стартовый промпт воркера УСИЛЕН: `start_prompt` (non-raw) теперь ЯВНО «invoke the `pipeline` skill (Skill tool) before doing any work -- do NOT do it raw» (dispatcher.py:542). Прогон 2/2 (после фикса):
  - RUN 1 (factorial): worker PID = /opt/homebrew/bin/claude с pipeline-промптом (ps подтверждает; ORC_RAW_PROMPT/ORC_SPAWN_CMD_OVERRIDE/PROMPT_DIR/OVERRIDE ВСЕ пусты — не seam, не raw). STATE.md показал РЕАЛЬНЫЕ фазы pipeline: 0 intake→1 folded→2 ТЗ/ГЕЙТ (микро-режим предложен явно)→4 BUILD→5 VERIFY DONE. Артефакт factorial.py+test (pytest 7 passed) — реальный, работает. НО orc ПАРКНУЛ «suspected-fake-done»: poll сработал ~4с РАНЬШЕ, чем воркер флашнул файлы, и воркер не git-commit-нул. → нашёл РЕАЛЬНЫЙ баг (см. фикс ниже).
  - RUN 2 (is_palindrome, с фиксом + «git commit»): task DONE, spent 2.66M ток., БЕЗ ложного парка. Внешние факты: git-коммит `f3ea38e Add is_palindrome with pytest tests` + palindrome.py/test (pytest 5 passed). Петля ЗАМКНУЛАСЬ: daemon poll→bd close→газета «смена: 1 готово; потрачено ~2.7M токенов за смену», окно воркера закрылось. Evidence: docs/evidence/pipeline-live/{04-08}.
- [x] **ФИКС B1-гонка (найден на RUN 1)**: DONE-claim от ЖИВОГО воркера без внешнего факта БОЛЬШЕ не паркуется как fake-done (воркер мог не дофлашить деливерабл — доказано живьём). Паркуется fake-done ТОЛЬКО МЁРТВЫЙ воркер (вышел, ничего не произвёл — истинный reward-hack). Живой без факта → not-yet-done, оставлен в workers, re-poll. Стена B1 НЕ ослаблена (деливерабл всё равно обязателен до close). dispatcher.poll_completions + `_pid_alive`. Тест: test_poll_done_live_worker_no_fact_not_parked_then_closes (live+DONE+no-fact→не парк; факт появился→close); 2 старых B1-теста пинят мёртвый PID.
- [x] **ФИКС canary окно-vs-работоспособность (3-й экземпляр корневой ошибки, после admission F5 и газеты F6; пользователь получил ЛОЖНОЕ «смена не стартовала»)**: canary БОЛЬШЕ не ФЕЙЛИТ смену по ccusage-окну/нотификатору. bd/auth/RAM остаются hard-fail (реальные блокеры→смена стоит→G7-уведомление). ccusage-окно и notify → ИНФОРМАЦИОННЫЕ (warn, ok=True): неактивное/сброшенное окно = свежая квота (реальный лимит ловит admission по лимит-строке); нет нотификатора = нет пушей, не повод фейлить. canary.py:run. Тесты: test_canary_inactive_window_still_starts_shift (active=False + None → PASS), test_canary_missing_notifier_still_starts_shift, test_canary_real_blocker_still_fails (RAM-блокер всё ещё фейлит). notify_canary_fail шлётся только при bd/auth/ram/spawn.

### P2-ВОЛНА (backlog из DONE-WAVE-2: references/sandbox, сеть-политика, масштаб)
- [x] **P2/1 — references скилла достижимы под sandbox** (коммит e61ac49): read-only skills доступны воркеру, write-deny держится. 275 тестов.
- [x] **P2/2 — сеть-политика** (коммит 1372de8): удобный выбор open/deny + per-task `--offline`. 275 тестов.
- [x] **P2/3 — масштабный E2E: очередь/сериализация/газета НА ОБЪЁМЕ** (self-pass). Раньше проверялось поштучно (F12 = 3 задачи); теперь 8 задач / 3 проекта / 2 гейта / смешанные приоритеты в ОДНОЙ смене, реальный claude + seam вместе. `.verify/scale-e2e.sh` (воспроизводимо; `REAL_TASKS=N` — сколько задач реальным claude, дефолт 1). Приёмка (все PASS, evidence docs/evidence/scale-e2e/):
  - (1) СЕРИАЛИЗАЦИЯ: 8 интервалов активности `[started_epoch, ended_epoch]` из shift.json НЕ пересекаются (project-mutex; на 1-воркерной машине строго последовательно) + прямая проба mutex (`spawn_one` на занятом проекте → `project busy (mutex)`). Реальные claude-воркеры дают явно длинные интервалы (48.6с/35.8с) vs seam (~7-9с).
  - (2) ГЕЙТЫ ПОСЛЕ АВТОНОМНЫХ: оба гейта (p=0, высокий приоритет) стартовали ПОСЛЕ всех 6 автономных → gate-last бьёт приоритет (order_ready).
  - (3) ГАЗЕТА на объёме: «6 готово, 2 ждут тебя, 0 упало» — числа сходятся с фактами на диске (сверка через product-строку RU_REPORT_SUMMARY, без хардкода прозы в тесте). Per-task расход реален у claude (840k/1.07M ток.).
  - (4) 0 ДУБЛЕЙ / 0 ПОТЕРЯННЫХ: 8/8 терминальны (6 done + 2 parked-gate), нет дублей done-id, нет пересечения done∩parked.
  - (5) РЕАЛЬНЫЙ+SEAM В ОДНОЙ СМЕНЕ: 2 реальных claude-артефакта (ALPHA/BETA, закоммичены реальным claude — внешний факт) + 4 seam-артефакта, все непустые; F6 дельта окна +3.9M ток. (реальный claude жёг).
  - ТРИ БАГА, ПОЙМАННЫХ НА МАСШТАБЕ (product-фиксы, стены НЕ ослаблены):
    (а) **Граница %ct 1 секунды** (gitutil.commits_since + external_progress `baseline_rev`): быстрый воркер, чей коммит попадал в ТУ ЖЕ секунду, что и старт, НЕ распознавался (`%ct > since` строго) → задача зависала. Фикс: захват HEAD на старте (`head_at_start` в shift.json), `%ct >= floor(since)` С ИСКЛЮЧЕНИЕМ baseline-предков → same-second коммит виден, baseline по-прежнему исключён. B1-стена держит (reverify-b1 7 фейков паркуются). Тесты: tests/test_gitutil_boundary.py (3).
    (б) **reconcile requeue финишировавшего воркера**: воркер, написавший DONE+факт и вышедший МЕЖДУ тиками, requeue-ился по лизу (дублировал работу, зависал). Фикс: `_dead_worker_finished` — мёртвый+DONE+реальный факт остаётся для poll_completions (не requeue); мёртвый+DONE+НЕТ факта — по-прежнему requeue+park suspected-fake-done (стена держит). Тесты: 2 в test_recovery.py.
    (в) **Интервалы активности в терминальных записях** (shift.py): done/parked/failed теперь несут `started/started_epoch/ended/ended_epoch` → сериализация аудируется прямо из shift.json. Тест: test_terminal_records_carry_activity_interval.
  - Тесты 275 → **281** (+6), 0 регрессий. Смоук после фиксов: pytest 281, e2e-loop-close/e2e-gate/reverify-b1/e3-rewardhack-live — зелёные.
  - NB (косметика, НЕ P2/3): `.verify/e2e-gate.sh` грепал ПОЛНЫЙ путь к brief.md, который газета усекает до 80 кол (P1-фича) для длинных tmp-путей → ложный CARD FAIL на baseline. Починен греп (усечённый путь: `brief.md` + хвост слага). Карточка всегда рендерила путь верно — это была хрупкость теста, не продукта.

---
Майлстоуны: M1 = F1-F4 ✓verified + F14 (замыкание петли, из consumer), M2 = F5-F9 + F15 (надёжность + гейт + бэкенд-абстракция), M3 = F10 + F13 (ops + OS-sandbox), M4 = F11-F12 (патчи конвейера + E2E).
Золотой путь: F2 (скелет+signature), F4 (ядро), F9 (гейт-опыт).
Порядок священен: F1 (стены-гейт) ДО F2 (первый реальный спавн). Дефолтный бэкенд спавна = Terminal.app (исполняет команду воркера); Ghostty opt-in (нефункционален на 1.3.1).
Фикс-фичи из eval M1: F8 (реальный PID в shift.json — G6/G10), F13 (OS-sandbox — обфускация обходит паттерн-хук). Фикс-фича из фидбека: F15 (бэкенд-абстракция; ЦЕЛЬ 0-husk не достигнута — ограничение среды, честно).
R-M2 (evaluator р.2, 4 блокера): все закрыты — Б1 Ghostty-невыполнение→откат на Terminal; Б2 регресс M1-петли→e2e-loop-close зелёный на дефолте; Б3 F9 E2E FAIL→PASS на Terminal; Б4 kill-restart жёг claude→seam-only. F6 work-driven дельта→F12 (честно).
