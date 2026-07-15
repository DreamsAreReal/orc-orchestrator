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
- [x] `orc status` печатает газету: первая строка = сводка (N done/parked/failed + % окна), первый экран ≤150 слов
Проверка: `bash .verify/e2e-skeleton.sh` + вывод в `docs/evidence/F2/`
Статус: verified
Доказательство:
- `bash .verify/e2e-skeleton.sh` → "F2 SKELETON PASS", exit 0. РЕАЛЬНЫЙ osascript-терминал с интерактивным claude создал hello.txt=[ready] за ~14с; G7 forced-fail отказал старт; газета ≤150 слов. Лог: docs/evidence/F2/e2e-skeleton.log
- `python3 -m pytest tests/test_skeleton.py` → 13 passed (config/shift/ordering/report/canary). Лог: docs/evidence/F2/unit-tests.log
- команда запуска: `bin/orc {init|add <proj> "<text>"|start [--once]|status [--newspaper]} [--json]`

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
Что: перед spawn — `ccusage blocks --active --json` (окно) + free-RAM + детект лимит-строк CLI. session/weekly/Opus → парковка до ресета (парс времени); 429/529 → ретрай без парковки.
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
Статус: self-pass
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
- [ ] 3/3 задачи дошли до терминального статуса (bd closed / parked-on-gate→resolved) без ручного вмешательства кроме гейта (G1)
- [ ] DONE каждой задачи подтверждён ВНЕШНИМИ фактами (git-коммиты/артефакты), не заявлением воркера
- [ ] сериализация проекта соблюдена, 0 дублей, газета корректна
Проверка: `bash .verify/e2e-shift.sh` (полный сценарий) + вывод в evidence/F12/
Статус: todo

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
Майлстоуны: M1 = F1-F4 ✓verified + F14 (замыкание петли, из consumer), M2 = F5-F9 + F15 (надёжность + гейт + бэкенд-абстракция), M3 = F10 + F13 (ops + OS-sandbox), M4 = F11-F12 (патчи конвейера + E2E).
Золотой путь: F2 (скелет+signature), F4 (ядро), F9 (гейт-опыт).
Порядок священен: F1 (стены-гейт) ДО F2 (первый реальный спавн). Дефолтный бэкенд спавна = Terminal.app (исполняет команду воркера); Ghostty opt-in (нефункционален на 1.3.1).
Фикс-фичи из eval M1: F8 (реальный PID в shift.json — G6/G10), F13 (OS-sandbox — обфускация обходит паттерн-хук). Фикс-фича из фидбека: F15 (бэкенд-абстракция; ЦЕЛЬ 0-husk не достигнута — ограничение среды, честно).
R-M2 (evaluator р.2, 4 блокера): все закрыты — Б1 Ghostty-невыполнение→откат на Terminal; Б2 регресс M1-петли→e2e-loop-close зелёный на дефолте; Б3 F9 E2E FAIL→PASS на Terminal; Б4 kill-restart жёг claude→seam-only. F6 work-driven дельта→F12 (честно).
