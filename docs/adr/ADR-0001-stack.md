# ADR-0001 — Выбрать python3-stdlib для диспетчера, bash для тонких обёрток

## Статус
Принято 2026-07-15

## Контекст
Диспетчер = долгоживущий процесс: парсинг JSON (bd/ccusage `--json`), процесс-менеджмент (spawn терминалов, PID-реестр, kill по своим PID, lease TTL), watchdog (hash-сравнение heartbeat, re-validate), atomic-write состояния, unit-тесты фикстур (G4/G5). Zero внешних зависимостей — требование ТЗ (Ограничения). Спайк-артефакт с командами и выводом: `.spikes/probe/stack.md`.

## Рассмотренные варианты
| Вариант | Плюсы | Минусы | Замеры из спайка |
|---|---|---|---|
| A: python3-stdlib | json/subprocess/signal/argparse/pathlib из коробки; тестируемо pytest; надёжный процесс-контроль | python 3.9.6 системный (старый); tomllib нет → конфиг JSON | `/usr/bin/python3` = 3.9.6; `import tomllib` → ModuleNotFoundError; `import json` OK; JSON-парсинг bd-подобного вывода отработал (spike Bash) |
| B: bash + jq | нативно для spawn/osascript; jq для JSON | процесс-состояние/lease/watchdog-логика на bash хрупки и почти нетестируемы; unit-тесты фикстур болезненны | `jq` = /usr/bin/jq есть; но состояние смены (shift.json atomic, PID-реестр) на bash = ручной риск |
| C: python + внешние (rich/typer/tomli) | удобный CLI/TOML | нарушает zero-dependency; supply-chain-риск (урок happy-форка) | — |

## Решение
**A** — диспетчер, watchdog, orc CLI, admission, re-validate, парсеры — на python3-stdlib (3.9.6-совместимо: без tomllib, match-statement, и т.п.). **bash** — только тонкие обёртки, где он нативен: spawn-скрипт (osascript открыть Terminal), LaunchAgent runner (абсолютный путь claude, PATH в plist), E2E-скрипт. Причина: логика состояния и watchdog требуют тестируемости (G4/G5 — фикстуры) и надёжного процесс-контроля, что на bash хрупко; A даёт это без единой внешней зависимости (jq остаётся резервом для bash-обёрток). tomllib отсутствует → конфиг JSON (ADR-0002).

## Последствия
- Тесты фикстур (лимит-строки, петля heartbeat, re-validate) — прямые pytest.
- Совместимость 3.9.6: не использовать 3.10+ синтаксис; CI-линт `python3.9 -m py_compile`.
- Принятые риски: системный python может обновиться мажорно — self-test после апдейта (тот же механизм, что pin claude); приемлемо, т.к. stdlib-API стабильно.
