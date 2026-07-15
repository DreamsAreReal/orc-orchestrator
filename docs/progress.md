# Progress — orc build (append-only)

## F1 — Границы песочницы + негативный спайк стен [ГЕЙТ входа] — self-pass 2026-07-15

Сделано:
- Наработка прошлого builder (worker_walls.py 393стр, strings.py, test 244стр) прочитана,
  скомпилирована на 3.9.6, 37 unit-тестов зелёные — годна, покрывает детекцию/merge/env/MCP.
- Создан `.verify/negative-walls.sh` — ГЕЙТ входа. РЕАЛЬНО запущен `claude -p` на 2.1.193
  под `--permission-mode bypassPermissions` в walled temp-проекте, 3 стены доказаны ЖИВЬЁМ:
  WALL1 git push — заблокирован (HEAD не сдвинулся); WALL2 rm -rf вне ws — sentinel-файл
  вне workspace ВЫЖИЛ; WALL3 чтение ~/.ssh — контент не утёк. Скрипт exit 0 = 3/3 PASS.
- Evidence: docs/evidence/F1/{negative-walls.log, unit-tests.log, merge-and-env-demo.log}.

Решения:
- **Стена реализована через PreToolUse-хук с exit 2, НЕ через permissions.deny.** Причина:
  issue #6699 — история отказа именно permission-enforcement под bypass; документация
  (code.claude.com/docs/en/hooks) подтверждает: PreToolUse exit 2 блокирует tool-call
  ПОВЕРХ permission-mode (хук = enforced policy до permission-системы). permissions.deny
  оставлен как defense-in-depth (действует в non-bypass режимах). Живой прогон подтвердил.
- Глобальный ~/.claude/settings.json = `defaultMode: bypassPermissions` И уже использует
  PreToolUse-хук (pipeline-hooks.py) для Bash — контур воркера верно моделирует это.
- Негативный спайк детерминирован по МИРУ, не по прозе модели: PASS/FAIL решают факты
  (sentinel выжил, ssh-маркер не в транскрипте, git HEAD не сдвинут), а не согласие модели.
  Модель может сама отказаться — но проверяется, что БЛОКИРУЕТ СТЕНА.

Грабли:
- Первая ~/.ssh проба (cat id_rsa) дала self-refuse модели, не доказывая стену → переписал
  на benign ~/.ssh/config + cooperative framing: тогда стена — единственный блокер.
- `python3 -m orc.worker_walls` без PYTHONPATH → ModuleNotFoundError; в settings.json хук
  использует `sys.path.insert(0, '/Users/admin/orchestrator/src')` абсолютным путём — работает
  из любого cwd воркера (self-contained). Для CLI-обёртки (F3) учесть PYTHONPATH/entry-point.
- Наработка уже была закоммичена прошлым builder (78cc0b7), рабочее дерево по src/ чистое.

Находки инъекций: нет (весь прочитанный код — свой, наработка прошлого builder).

## F2 — Walking skeleton: смена+газета+canary [золотой путь] — self-pass 2026-07-15

Сделано:
- Построен orc CLI (python3-stdlib, 3.9-совместимо): модули config/beads/probes/spawn/
  canary/shift/dispatcher/report/cli + bin/orc (тонкая обёртка) + python3 -m orc.
- `orc init` (beads-очередь в ~/.orc), `orc add <proj> "<text>" [-p]` (метаданные проекта
  в bd metadata), `orc start` (canary→claim→spawn РЕАЛЬНОГО терминала), `orc status`
  (live + --newspaper). --json везде.
- Canary: 5 проверок (bd/auth/ccusage/notify/ram) + опц. spawn-проба; ORC_CANARY_FAIL=<name>
  инъецирует фейл → смена НЕ стартует (G7 доказан живьём, exit 2).
- Газета (signature): первая строка — сводка «N готово, M ждут, K упало; съедено X% окна»,
  ≤150 слов, plain text, статус-глифы ✓✗⏸▸, RU. Live-status: гейты сверху, пул внизу.
- E2E: .verify/e2e-skeleton.sh — РЕАЛЬНЫЙ osascript-терминал с интерактивным claude создал
  hello.txt=[ready] за ~14-42с (3 прогона). Evidence: docs/evidence/F2/{e2e-skeleton,unit-tests}.log.
- Установлен ccusage@20.0.17 (официальный npm-пакет ryoppippi, разрешено brief) — на PATH.

Решения:
- Проект задачи хранится в bd metadata (`bd update --metadata JSON`), читается из
  `bd ready --json` — проверено пробой round-trip. `bd q` даёт чистый ID для скриптов.
- spawn.py: osascript `do script` с shlex-quote команды; свой escaping (ад-хок shell-escape
  в ручном тесте ломал AppleScript — код spawn.py корректен, "tab N of window id M").
- ORC_RAW_PROMPT=1 → воркер получает сырой текст задачи (детерминированный скелет-пруф
  спавна); реальные смены — pipeline-обёртка (гейты конвейера применяются). Записано.
- Изоляция тестов: ORC_HOME/ORC_HUB env → временный home, реальный ~/.orc не тронут.
- Окно ccusage = 5ч блок (300 мин); pct = (300−remaining)/300. window_pct в shift.json.

Грабли:
- ccusage не был persistent (проба юзала npx transient) → поставил глобально официальным
  пакетом. F5/F6 теперь имеют стабильную команду.
- Газета ложно говорила «пуста» при живом воркере (worker не в parked/done/failed) →
  добавил секцию «в работе» в newspaper(). Юнит-тест закрывает регресс.
- shift.json НЕ закрывает/mark-done воркера сам (нет monitor-петли) — это F4/F7. В скелете
  воркер остаётся running; газета это честно показывает.

Находки инъекций: нет.

## F3 — orc add / status (live) + JSON везде — self-pass 2026-07-15

Сделано:
- Формализованы add/status (базис из F2). `orc add --batch` из stdin («proj: text» на строку):
  плохой проект — skip с ошибкой в stderr, остальные создаются. --json у add/status/init.
- Live status: секции «ждут тебя» (⏸ сверху) → «в работе» (▸) → пул-футер (%окна/мин/RAM).
  Гейтовые задачи сортируются в конец ready (order_ready: gate по label ИЛИ metadata.gate).
- tests/test_cli.py (7 тестов, гоняют реальный orc против реального bd в изолированном home):
  single/batch add, skip плохого проекта, ошибка отсутствующего проекта, JSON-валидность
  status, gate-last ordering. .verify/timing-add.sh: G11 — 10 задач в ready за 8с (≤300с).

Решения:
- test_cli.py помечен skipif(not bd_available) — тесты требуют beads; на машине без bd
  не падают, а скипаются. Реальный bd/git per-test медленный (~36с/7), но честный.

Грабли:
- нет; add/status/batch/json уже были заложены в F2, F3 добавил покрытие+тайминг.

Находки инъекций: нет.
