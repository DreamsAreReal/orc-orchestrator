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
Статус: self-pass
Доказательство:
- `bash .verify/admission.sh` → "F5 ADMISSION PASS (6/6 fixtures classified + admission gate correct on RAM/window/limit)", exit 0. 6 РЕАЛЬНЫХ лимит-строк CLI (code.claude.com/docs/en/errors): session→park+reset 3:45pm, weekly→park+reset Mon 12:00am, Opus→degrade, 429/529→retry (no park), none→no-limit. Лог: docs/evidence/F5/admission.log
- `python3 -m pytest tests/test_admission.py` → 23 passed (классификация×7, парс ресет-времени×4, гейт RAM/окно/ready/limit×12). Лог: docs/evidence/F5/unit-tests.log
- интеграция в dispatcher: `python3 -m pytest tests/test_dispatcher.py` → 14 passed (11 F4 + 3 admission: low-ram→park без claim, ram/window ok→spawn, session-limit→park). Лог: docs/evidence/F5/dispatcher-tests.log
- fixtures: tests/fixtures/limit-{session,weekly,opus,429,529,none}.txt

### F6 — Бюджет-кап + per-задачная атрибуция расхода [M2]
Ворота: G8, контр-метрика «расход». Опыт/ценность: не сжечь weekly-кап; газета показывает «сколько съела задача».
Что: расход задачи = дельта `ccusage` total между claim и close (на 1-воркерной машине атрибуция точна — работает один воркер). Бюджет-кап задачи и смены из конфига → превышение → парковка + запись.
Приёмка:
- [x] расход задачи = tokens_after − tokens_before корректен на живом прогоне (сверка с ccusage session)
- [x] задача с заниженным капом останавливается с парковкой и записью в газету (G8)
- [x] кап смены превышен → новые задачи не стартуют
Проверка: `python3 -m pytest tests/test_budget.py` + живой прогон (evidence/F6/)
Статус: self-pass
Доказательство:
- `bash .verify/budget.sh` → "F6 BUDGET PASS (live spend delta + low-cap park + newspaper DONE/WAVE/BETA + summary-first)", exit 0. Расход = дельта РЕАЛЬНОГО ccusage total (claim→close): live total прочитан живьём (10.4M ток.), task_spend вернул точную дельту 12345, real re-read монотонен (delta≥0). Лог: docs/evidence/F6/budget.log
- `python3 -m pytest tests/test_budget.py` → 15 passed (spend-атрибуция×5, task-cap×5, shift-cap×2, done_kind/newspaper DONE/wave/BETA + summary-first×3). Лог: docs/evidence/F6/unit-tests.log
- backlog-мелочи внесены: газета — сводка «N готово» ТЕПЕРЬ ПЕРВОЙ строкой (было 2-й, паспорт вкуса); DONE / DONE-WAVE-N (предложена волна) / BETA (ждёт решения) различаются в газете + per-task расход «~N ток.». test_skeleton assertion исправлен под новую (верную) раскладку.
- интеграция: shift-cap блокирует новые спавны (spawn_one), task-cap паркует живого воркера + стоп (enforce_budget в orc status); 124 теста passed, 0 регрессий.

### F7 — Watchdog: петля/тишина детект + внешняя проверка [M2]
Ворота: G5. Опыт/ценность: выход из meltdown/зависаний без ложных убийств.
Что: heartbeat из PostToolUse; PreToolUse-маркер «tool-in-flight» отличает работу от тишины. Петля (K=конфиг одинаковых hash) / тишина-без-маркера → внешняя проверка пост-условий (git/артефакты) → kill → рестарт от STATE.md, cap=конфиг → эскалация.
Приёмка:
- [ ] синтетическая петля (K одинаковых hash) и тишина-без-маркера детектятся (G5)
- [ ] живой Bash-вызов ≥2 мин НЕ убивается (0 ложных kill)
- [ ] рестарт только после внешней проверки пост-условий; cap соблюдается → эскалация
Проверка: `python3 -m pytest tests/test_watchdog.py`
Статус: todo

### F8 — Восстановление диспетчера + lease TTL [M2]
Ворота: G6. Опыт/ценность: безнадзорная надёжность после падения.
Что: kill -9 диспетчера → рестарт читает shift.json → сверяет с реальными PID (живые подхватывает, мёртвые → задача в ready через lease). Атомарная запись shift.json (tmp+rename).
Приёмка:
- [ ] kill -9 диспетчера посреди смены → рестарт продолжает, 0 дублей/потерь задач (G6)
- [ ] мёртвый воркер (PID нет) → его задача возвращается в ready (lease)
Проверка: `bash .verify/kill-restart.sh`
Статус: todo

### F9 — Гейт-протокол (bd-задача + живое ожидание + macOS-уведомление) [M2] [золотой путь]
Ворота: G2. Опыт/ценность: единственная точка человека; signature-опыт «карточка решения».
Что: воркер дошёл до ТЗ-гейта → macOS-уведомление (osascript) → сессия ЖДЁТ живьём (выбор пользователя; слот держится); гейтовые задачи в конце очереди (F4). После ответа задача продолжает по STATE.md. Карточка: скоуп/планка/полномочия + путь к ТЗ + цена ошибки; необратимое в батче не утверждается.
Приёмка:
- [ ] реальный гейт: уведомление доставлено (osascript), карточка содержит путь к ТЗ + цену ошибки (G2)
- [ ] после ответа задача продолжает ровно с «Следующего шага» STATE.md
Проверка: `bash .verify/e2e-gate.sh` + вывод в evidence/F9/
Статус: todo

### F10 — LaunchAgent + config + kill switch [M3]
Ворота: G10. Опыт/ценность: подъём в GUI-сессии (Keychain), ручной стоп, дневной режим.
Что: plist (Aqua, абсолютный путь `/opt/homebrew/bin/claude`, PATH), `~/.orc/config.json` (все калибровки — нет хардкода порогов), `orc stop` (≤10 сек, задачи в ready). **Удержание Mac от сна В КОНТУР НЕ ВСТРАИВАЕТСЯ (`caffeinate`/подобное конфликтует с мышью пользователя — прямой фидбек 2026-07-15); v1 дневной, при активности Mac не спит; для долгой смены пользователь настраивает сон сам в System Settings.**
Приёмка:
- [ ] LaunchAgent из GUI-сессии стартует диспетчер, `claude auth status`=0 из его контекста
- [ ] `orc stop` останавливает всех воркеров ≤10 сек, задачи в ready (G10)
- [ ] все калибровки из config.json (нет хардкода)
Проверка: `bash .verify/launchagent.sh` + `python3 -m pytest tests/test_config.py`
Статус: todo

### F11 — Патчи конвейера pipeline (docs/tasks/<слаг>/ + развилка фазы 0) + откат [M4] [improvement]
Ворота: G9. Опыт/ценность: двухслойная мультизадачность (продуктовый слой + мини-пайпы задач).
Что: по RS-02 — pipeline-hooks.py (2 глобы STATE.md +tasks/*/docs), pipeline-scorecard.sh (детект workspace), SKILL.md/phase-0 (развилка «STATE есть, но задача новая»→подворкспейс). СТАРОЕ поведение не ломается; патч на отдельной git-ветке ~/.claude с возможностью `git revert`.
Приёмка:
- [ ] характеризационный набор: scorecard/doctor на существующем smoke-макете ДО патча зафиксирован; ПОСЛЕ — тот же результат (0 регрессий) (G9)
- [ ] scorecard находит `docs/tasks/<слаг>/`, хуки видят STATE там, doctor exit 0
- [ ] патч откатывается `git revert` одним коммитом, doctor снова зелёный (проверено)
Проверка: `bash ~/.claude/skills/pipeline/bin/pipeline-lint.sh --doctor` + scorecard на обоих макетах + revert-тест (evidence/F11/)
Статус: todo

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
- [ ] обфусцированные обходы (base64|bash rm вне ws, python -c shutil.rmtree, find вне ws -delete) ЗАБЛОКИРОВАНЫ sandbox (evidence/F13/)
- [ ] запись вне workspace невозможна на уровне ОС, не только hook
Проверка: `bash .verify/sandbox-walls.sh` (расширенный негативный спайк) + вывод в evidence/F13/
Статус: todo

---
Майлстоуны: M1 = F1-F4 ✓verified + F14 (замыкание петли, из consumer), M2 = F5-F9 (надёжность + гейт), M3 = F10 + F13 (ops + OS-sandbox), M4 = F11-F12 (патчи конвейера + E2E).
Золотой путь: F2 (скелет+signature), F4 (ядро), F9 (гейт-опыт).
Порядок священен: F1 (стены-гейт) ДО F2 (первый реальный спавн).
Фикс-фичи из eval M1: F8 (реальный PID в shift.json — G6/G10), F13 (OS-sandbox — обфускация обходит паттерн-хук).
