# RS-02: Инвентарь точек раскладки workspace в pipeline

## Итоговая таблица

| Точка | Файл:строки | Классификация | Что менять для `docs/tasks/<слаг>/` | Риск |
|---|---|---|---|---|
| **GLOB STATE.md** | pipeline-hooks.py:107–108, 146–147 | Детерминированный код (Python) | Добавить `_g.glob("tasks/*/docs/STATE.md")` + `_g.glob("tasks/*/docs/pipeline/STATE.md")` | КРИТИЧ |
| **GLOB workspace в scorecard** | pipeline-scorecard.sh:6–8 | Детерминированный код (Bash) | Обновить ALT-ветку поиска: `"$W"/tasks/*/docs` вместо `"$W"/*/docs` | ВЫСОК |
| **Промпт резюма** | SKILL.md:207 | Промпт-правило (оркестратор) | `docs/STATE.md` (или `docs/pipeline/STATE.md`, или `docs/tasks/<слаг>/STATE.md`) | НИЗ |
| **Prompt workspace выбор** | phase-0-intake.md:21–25 | Промпт-правило (INTAKE) | Добавить третий вариант: `<cwd>/docs/tasks/<слаг>/docs/` (для group-задач) | СРЕДН |
| **Шаблон STATE** | state-template.md:1 | Шаблон | Комментарий актуален; содержимое не зависит от раскладки | НЕ менять |
| **SKILL.md структура** | SKILL.md:166–184 | Промпт-правило (архитектура) | Примечание в секции: `docs/tasks/<слаг>/docs/` — группировка задач в одной workspace | НИЗ |
| **Дефолт CLAUDE.md** | CLAUDE.md:18–31 | Промпт-правило (глобальное) | Примечание в резюме-секции о вложенных workspace (не требует изменений) | НЕ менять |
| **pipeline-lint.sh state** | pipeline-lint.sh:52–95 | Детерминированный код (Bash) | Параметр `$D` (docs_dir) получает полный путь; поиск уже гибкий (не требует изменений) | НЕ менять |
| **Скелет фазы 0** | phase-0-intake.md:26 | Промпт-правило | Добавить: *«Если workspace в группе, путь: `<cwd>/docs/tasks/<слаг>/`»* | НИЗ |

## Детали по критическим точкам

### 1. pipeline-hooks.py (поиск STATE.md)
**Строки 107–108, 146–147** — две идентичные глобы в функциях `posttooluse()` и `stop()`.

**Текущий код:**
```python
states = (_g.glob("docs/STATE.md") + _g.glob("docs/pipeline/STATE.md")
          + _g.glob("*/docs/STATE.md") + _g.glob("*/docs/pipeline/STATE.md"))
```

**Требуемое изменение:**
```python
states = (_g.glob("docs/STATE.md") + _g.glob("docs/pipeline/STATE.md")
          + _g.glob("*/docs/STATE.md") + _g.glob("*/docs/pipeline/STATE.md")
          + _g.glob("tasks/*/docs/STATE.md") + _g.glob("tasks/*/docs/pipeline/STATE.md"))
```

**Риск:** КРИТИЧЕСКИЙ — эти точки напрямую контролируют детекцию активного pipeline-прогона; без них переинъекция и lint-стена не срабатывают на nested workspace.

### 2. pipeline-scorecard.sh (детект workspace)
**Строки 6–8** — логика поиска `docs` подпапкой глубже.

**Текущий код:**
```bash
D="$W/docs"; [ -f "$W/docs/pipeline/STATE.md" ] && D="$W/docs/pipeline"
[ -f "$D/STATE.md" ] || { ALT=$(ls -d "$W"/*/docs 2>/dev/null | head -1); [ -n "$ALT" ] && { W="${ALT%/docs}"; ...
```

**Требуемое изменение:**
```bash
# Добавить поиск в tasks/ перед паттерном */docs:
ALT=$(ls -d "$W"/tasks/*/docs 2>/dev/null | head -1) || ALT=$(ls -d "$W"/*/docs 2>/dev/null | head -1)
```

**Риск:** ВЫСОКИЙ — scorecard используется для вычисления гейтов; неправильный путь до `$D` приведёт к FAIL всех механизмов.

### 3. Промптовые точки (изменяемость: НИЗКАЯ–СРЕДНЯЯ)

**phase-0-intake.md:21–25** — выбор workspace.  
**Текущая версия** даёт 3 варианта (default/занят/не связана с cwd).  
**Добавить 4-й вариант** в фазе 0: *«Если это подзадача группы → `<cwd>/docs/tasks/<слаг>/docs/`»*.

**SKILL.md:181–184** (описание структуры workspace).  
**Текущий текст** описывает стандартную раскладку.  
**Добавить примечание** про группирование.

## Анализ текущей системы

### Что РАБОТАЕТ БЕЗ ИЗМЕНЕНИЙ
1. **pipeline-lint.sh** (state-режим) — принимает полный путь `$D`; гибкая на раскладку.
2. **Шаблон STATE.md** — не зависит от пути; один раз создаётся, везде одинаков.
3. **Конвенция артефактов (CLAUDE.md)** — правило «в `docs/` workspace-а» — работает для любой раскладки.

### Что НЕ упоминает docs/tasks (проверено)
**Ноль совпадений** по запросу `grep -rn "docs/tasks"` в pipeline/ и CLAUDE.md.  
Это НЕ случайность: задача grouping не была в спектре дизайна на момент последних правок.

## Соотношение точек к ролям

| Роль агента | Точка | Действие |
|---|---|---|
| **Оркестратор** | SKILL.md:207, phase-0-intake.md:21–25 | Выбрать workspace на INTAKE; читать STATE из правильного места на резюме |
| **Builder** | Косвенно через STATE | Коммитит в workspace, полученный на INTAKE |
| **Evaluator** | pipeline-scorecard.sh | Запускает scorecard с параметром W; требует правильного поиска |
| **System (hooks)** | pipeline-hooks.py, pipeline-lint.sh | Находят STATE.md детерминированно |

## Стратегия патча

**Порядок внедрения:**

1. **Незамедлительно** — pipeline-hooks.py (обе функции, 2 глобы).
2. **Параллельно** — pipeline-scorecard.sh (поиск ALT).
3. **В документацию** — phase-0-intake.md + SKILL.md (промпты, не код).

**После** каждого патча — запустить `pipeline-lint.sh --doctor` (проверит целостность).

## Выводы

- **Всего точек**: 9 (8 с потенциалом патча, 1 не требует).
- **В детерминированном коде**: 2 критических (Python + Bash).
- **В промптах**: 4 low-risk.
- **Нет необходимости менять**: 3 (lint.sh, шаблон, CLAUDE.md).
