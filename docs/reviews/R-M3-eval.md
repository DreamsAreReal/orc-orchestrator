# Ревью M3 (evaluator, внешний, «докажи мне») — orc

Майлстоун M3: **F10** (LaunchAgent + config + kill switch + orc setup husk-фикс) и
**F13** (OS-sandbox seatbelt поверх F1-хука, фикс дыры обфускации из eval M1).
Раунд 1 для M3. Все прогоны — мои, руками. Живой claude НЕ жёгся (F10 auth через
probe-LaunchAgent без claude-спавна; F13 через sandbox-exec-обёртку + пробное действие).
Baseline окон Terminal: 0. Финал: 0. Residue убран.

## Метод и сырые прогоны

### Смоук M1+M2 (регресс?)
- `python3 -m pytest tests/ -q` → **200 passed** (заявлено 200 — СОВПАЛО; пересчитал сам).
- `bash .verify/e2e-loop-close.sh` → **PASS, exit 0, ~21с**. Петля M1 цела на дефолтном
  бэкенде (Terminal): газета догнала «1 готово», воркер остановлен (0 процессов на tty),
  bd closed. Регресса M1/M2 нет.
- Продукт не мутирован: `git status src/ tests/ bin/ .verify/` → чисто (изменены только
  evidence-логи, которые verify-скрипты перегенерируют — не код продукта).

### F13 OS-sandbox (закрытие дыры eval M1) — ГЛАВНОЕ
- `bash .verify/sandbox-walls.sh` → **PASS, exit 0**. 5 обфусцированных обходов sentinel'а
  ВНЕ ws (direct rm / base64|bash rm / python shutil.rmtree / find -delete / xargs rm) —
  ВСЕ заблокированы; ~/.ssh-запись заблокирована; запись ВНУТРИ ws работает.
- **Мой независимый контроль** (не доверяю ассертам скрипта — проверил, что это не мок):
  - CONTROL-1: та же `rm` БЕЗ sandbox → sentinel УДАЛЁН (rm реально работает, тест не
    подстроен под выживание).
  - CONTROL-2: та же `rm` ПОД sandbox → `rm: Operation not permitted`, exit 1, sentinel
    ВЫЖИЛ. Это реальный OS-блок на уровне syscall, не отказ модели.
  - CONTROL-3 (ловушка /private/tmp из eval M1): профиль пускает ТОЛЬКО узкий subpath
    `(subpath "/private/tmp/orc-eval-ws.XXX")`; запись в КОРЕНЬ /private/tmp вне ws →
    ЗАБЛОКИРОВАНА. Широкий родитель НЕ вайтлистится — ловушка закрыта.
  - CONTROL-4: запись ВНУТРИ ws → работает (sandbox не кирпичная стена).
- **Профиль-инвариант проверен в реальном спавн-пути** (не только в тест-скрипте):
  `spawn.build_start_command(...)` с дефолтным cfg → оборачивает воркера
  `/usr/bin/sandbox-exec -f <ws>/.orc/sandbox.sb bash -lc '...'`. Дефолт sandbox=on;
  `sandbox=False` → без обёртки. Профиль пишется под `<project>/.orc/sandbox.sb`.
- **Анти-персистенс проверен мной**: если воркер перезапишет свой `.orc/sandbox.sb` на
  открытую политику — СЛЕДУЮЩИЙ спавн `write_profile()` пере-затвердевает файл
  (`(deny file-write*)` снова присутствует). Ядро читает профиль на spawn до exec; правка
  файла в рантайме не меняет уже загруженную политику. Воркер не может ослабить стену.
- **Спайк** `.spikes/probe/sandbox.md` обосновывает: механизм (seatbelt file-write* deny +
  narrow allow), ловушка широкого родителя честно задокументирована, сеть — бинарная
  (per-host allowlist ненадёжен в user seatbelt — честно).
- `python3 -m pytest tests/test_sandbox.py` → 10 passed (форма профиля/narrow-subpath/
  no-broad-parent/wrap/default).
**F13: PASS.** Обфускация FS-записи заблокирована на уровне ОС, профиль узкий, не мок.

### F10 LaunchAgent + kill switch + config
- `bash .verify/launchagent.sh` → **PASS, exit 0, ~23с**.
  - PART 1 (auth из Aqua-LaunchAgent): probe-LaunchAgent `com.user.orc-f10-probe` в Aqua-
    контексте (тот же PATH+сессия, что строит `launchagent.build_plist_dict`) → `claude
    auth status` **auth_exit=0**, loggedIn:true, keychain_exit=0. Keychain достижим из
    launchd GUI-сессии — доказано без спавна claude-воркера (probe = auth-проба). Teardown:
    bootout+rm plist → 0 residue (проверено `launchctl print` = не загружен).
  - PART 2 (kill switch G10): РЕАЛЬНЫЙ Terminal-воркер (seam `sleep 600`, не claude);
    `orc stop` за **1.25с** (≤10с) → 0 живых процессов на tty (RAM освобождена); задача
    вернулась в **ready** (`orc-f10-home_hxG90y-uru`).
  - PART 3 (config-driven): override `stop_grace=9/min_ram=777/label=com.user.orc-custom`
    → загруженный код чтит override; `build_plist_dict` даёт Label из config, PATH,
    LimitLoadToSessionType=Aqua, claude_bin абсолютный. Нет хардкода.
- `python3 -m pytest tests/test_config.py` → 15 passed.
- **Residue-аудит после моего прогона**: `~/Library/LaunchAgents/` без orc-plist; probe и
  daemon не загружены в launchd; **com.user.no-caffeinate НЕ тронут** (present+loaded);
  tmp-residue моих прогонов убран; окна Terminal 0→0 (husk от PART2 самозакрылся, т.к.
  профиль пользователя уже shellExitAction=0).
**F10: PASS.** auth=0, residue убран, stop≤10с, config-driven, no-caffeinate цел.

### F10 orc setup (husk-фикс) — обратимость
- **Мой round-trip на КОПИИ реального plist** (не мутировал пользовательский): реальный
  профиль «Clear Dark» уже =0 → `set_close_on_exit` changed=False (идемпотентно, ничего
  не трогает); `orc setup --json` → `{"changed": false, "old": 0}`; реальный plist
  остался =0.
- **Синтетический round-trip (ненулевой оригинал)**: профиль shellExitAction=2 → set →
  0, **backup сохранён `orcPrevShellExitAction=2`**; revert → восстановил 2, backup-ключ
  удалён. Unset-случай: set → 0 (backup=""), revert → ключ shellExitAction полностью
  удалён (вернул отсутствие). Обратимость с бэкапом — доказана в обе стороны.
- `orc setup --revert` есть; идемпотентность есть.
**setup-husk: PASS (обратим, с бэкапом).**

## Пересчёт количественных под-заявок
- «200 тестов» → мой прогон 200 passed. СХОДИТСЯ.
- «5 обфусцированных обходов заблокированы» → пересчитал: A/B/C/D/E = 5 обходов +
  ~/.ssh-запись + control-inside — все верны. СХОДИТСЯ.
- «orc stop ≤10с» → 1.25с. СХОДИТСЯ с запасом.
- «auth_exit=0» → 0. СХОДИТСЯ.

## Паспорт вкуса (дисциплина CLI-вывода)
- `orc setup` (plain): «orc setup: Terminal profile 'Clear Dark' already closes windows
  on exit (shellExitAction=0); nothing to change.» — plain-text, без эмодзи, без
  «Successfully!», информативно.
- `orc stop` (plain): «orc stop: no active workers; nothing to stop.» — чисто.
- `config.json`: валидный JSON, grep-able, важные калибровки читаемы.
- `--json` у setup/stop — валиден.
- Grep банов (Successfully/эмодзи) в src → пусто.
**Паспорт вкуса: PASS.**
ОГОВОРКА (не блокер): паспорт/brief называют «конфиг TOML/orc.toml», по факту —
`config.json`. Отклонение ратифицировано ADR-0002 (tomllib отсутствует в Python 3.9.6,
zero-dependency стек). Документировано и принято на гейте design — не тихий дрейф.

## ПРОБЛЕМЫ

- **[СУЩЕСТВЕННОЕ]** `src/orc/config.py` (sandbox_deny_network=False по умолчанию) +
  `worker_walls`/F1 — обфусцированный `git push` НЕ заблокирован ни одним слоем на
  дефолте. Sandbox покрывает ТОЛЬКО file-write (сеть открыта воркеру для claude API/git
  fetch); F1-хук ловит ЛИТЕРАЛЬНЫЙ `git push`, но обфускация (`echo <b64 git push> |
  base64 -d | bash`) обходит паттерн — тот же класс дыры, что закрыл F13 для rm.
  Нарушает дух brief §Границы «git push (все формы)» для целевой планки «доверить
  безнадзорной смене». ВНЕ заявленного скоупа F13 (F13-обфускация = rm-класс FS-записи,
  и он закрыт), честно задокументировано в спайке как ограничение user-seatbelt. Фикс/
  эскалация: на финале G0c доказать блок обфусцированного git push (напр. deny сети при
  push-фазе, git-credential-стена, или воркер без push-remote). Не блокер приёмки M3
  (F13/F10 заявленные критерии выполнены), но обязателен до DONE.

## ЧТО ПЛОХО

- **Опыт/безопасность (главное)**: OS-стена закрыла FS-разрушение, но сетевой класс
  (обфусцированный git push, curl-эксфильтрация секретов из env, которые sandbox не
  чистит) остаётся на паттерн-хуке — для «безнадзорного bypass» это неполная гарантия.
  Sandbox — file-write only; deny-network отключён по необходимости (claude API). Это
  честная, но реальная асимметрия: удаление невозможно, а исходящая сеть — свободна.
- **auth=0 доказан прокси, не реальным daemon**: PART 1 ставит probe-LaunchAgent с тем же
  Aqua+PATH контекстом, но payload = `claude auth status`, а НЕ `orc daemon`. Контекст
  идентичен (`build_plist_dict` проверен в PART 3), но полный «LaunchAgent поднимает
  диспетчер и гонит смену» вживую не прогонялся — это ляжет на F12 E2E. Для M3 приёмлемо
  (береги окно), но «LaunchAgent стартует диспетчер» строго доказан только на уровне
  плиста+auth-пробы, не сквозного запуска смены.
- **Husk-косметика profile-dependent**: `orc stop`/close освобождают RAM (существенное),
  но пустое husk-окно закрывается лишь если у пользователя shellExitAction=0. `orc setup`
  это чинит, НО требует ручного quit Terminal.app перед правкой (иначе Terminal перезапишет
  plist на выходе) — onboarding-шероховатость, не автомат.
- **Относительно референса bd** (паспорт: «реально работает, grep-able»): F10/F13 —
  детерминированные, plain-text, config-driven, соответствуют планке дисциплины вывода.
  Слабое место — не вывод, а полнота стены (сетевой класс), см. выше.

## ПРОВЕРКА ДОКАЗАТЕЛЬСТВ
- «200 тестов, 0 регрессий» → перепрогнал: 200 passed. ВЕРНО.
- «5 обфусцированных обходов заблокированы sandbox на ОС-уровне, не мок» → независимый
  контроль with/without sandbox: без — удаляет, с — Operation not permitted. ВЕРНО, не мок.
- «профиль не вайтлистит широкий родитель» → /private/tmp корень вне ws заблокирован,
  allow только narrow subpath. ВЕРНО.
- «auth_exit=0 из LaunchAgent Aqua» → probe в Aqua-контексте, auth_exit=0, keychain=0.
  ВЕРНО (прокси-daemon, контекст идентичен — см. ЧТО ПЛОХО).
- «orc stop ≤10с, задачи в ready» → 1.25с, задача в ready, 0 живых процессов. ВЕРНО.
- «config-driven, нет хардкода» → override honoured (stop_grace/min_ram/label). ВЕРНО.
- «orc setup обратим с бэкапом» → round-trip 2→0→2, backup-ключ, unset-случай. ВЕРНО.
- «residue убран (bootout+rm), no-caffeinate цел» → LaunchAgents без orc, no-caffeinate
  present+loaded. ВЕРНО.
- «воркер под sandbox по дефолту» → build_start_command оборачивает sandbox-exec на
  дефолтном cfg; профиль пере-затвердевает каждый спавн. ВЕРНО.

## СОМНЕНИЯ
- Env воркера чистится denylist-ом (F1), но sandbox не запрещает ЧТЕНИЕ секретов и
  исходящую сеть — комбинация «читаемый секрет + открытая сеть» = теоретический канал
  эксфильтрации в безнадзорном режиме. Вне скоупа M3, но релевантно финалу.
- Полный «LaunchAgent → orc daemon → смена» не прогонялся вживую (окно берегу) — F12
  обязан это закрыть, иначе «диспетчер стартует из launchd» остаётся полу-доказанным.

## РЕЗЮМЕ
M3 солиден: F13 реально закрывает дыру обфускации из eval M1 на уровне ОС (проверено
независимым with/without-контролем — не мок; профиль узкий, ловушка /private/tmp закрыта,
воркер не может ослабить профиль). F10 auth=0 из Aqua-LaunchAgent, kill switch 1.25с с
requeue, всё config-driven, residue убран, no-caffeinate цел; husk-фикс обратим с бэкапом.
200 тестов зелёные, регрессов M1/M2 нет. Один СУЩЕСТВЕННЫЙ долг для ФИНАЛА (сетевой класс:
обфусцированный git push/эксфильтрация вне стены sandbox) — вне заявленного скоупа F13,
честно задокументирован, не блокирует приёмку M3, но обязателен до DONE (G0c/безопасность).

---

ВЕРДИКТ: ПРИНЯТО

Паспорт вкуса: PASS (config/setup/stop — plain-text, grep-able, без эмодзи/«Successfully!»,
`--json` валиден; доказательство — вывод `orc setup`/`orc stop`/config.json выше).
Оговорка: «конфиг TOML» из паспорта → JSON по ADR-0002 (tomllib нет в 3.9.6) —
ратифицированное отклонение, не дрейф.

Блокеров нет. 1 СУЩЕСТВЕННОЕ (сетевой класс обфускации — долг финала, вне скоупа F13).
F13 и F10 заявленные критерии приёмки выполнены и перепроверены независимо.
