# Спайк: выбор стека (ADR-0001) — 2026-07-15

Команды и реальный вывод на этом Mac:

```
$ which python3 && python3 --version
/usr/bin/python3
Python 3.9.6

$ which jq
/usr/bin/jq

$ echo '[{"id":"t1","status":"ready"},{"id":"t2","status":"blocked"}]' | python3 -c 'import json,sys; d=json.load(sys.stdin); print([x["id"] for x in d if x["status"]=="ready"])'
['t1']                      # json stdlib парсит bd-подобный вывод

$ python3 -c 'import tomllib'
ModuleNotFoundError: No module named 'tomllib'   # tomllib только с 3.11 → конфиг JSON

$ which terminal-notifier || echo osascript
osascript                   # уведомления через osascript display notification

$ which caffeinate
/usr/bin/caffeinate
```

## Выводы для ADR-0001/0002
- python3 = 3.9.6 системный, zero-dependency: json/subprocess/signal/argparse/pathlib есть.
- tomllib ОТСУТСТВУЕТ → конфиг JSON (не TOML), парсится и python.json, и jq.
- jq есть → bash-обёртки могут парсить JSON без внешних зависимостей.
- Ограничение: код на python 3.9-совместимом синтаксисе (без match, без 3.10+ hints).
