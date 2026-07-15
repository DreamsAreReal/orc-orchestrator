# ccusage гейдж лимитов — проверка на Mac

## 1. JSON вывод (`ccusage blocks --active --json`)

```json
{
  "blocks": [
    {
      "actualEndTime": "2026-07-14T23:32:39.695Z",
      "burnRate": {
        "costPerHour": 102.2860620217668,
        "tokensPerMinute": 418503.22657157615,
        "tokensPerMinuteForIndicator": 24660.660068841375
      },
      "costUSD": 130.33139434999993,
      "endTime": "2026-07-15T03:00:00.000Z",
      "entries": 896,
      "id": "2026-07-14T22:00:00.000Z",
      "isActive": true,
      "isGap": false,
      "models": ["claude-fable-5", "claude-haiku-4-5-20251001", "claude-opus-4-8"],
      "projection": {
        "remainingMinutes": 207,
        "totalCost": 483.22,
        "totalTokens": 118625207
      },
      "tokenCounts": {
        "cacheCreationInputTokens": 3802923,
        "cacheReadInputTokens": 26306781,
        "inputTokens": 944776,
        "outputTokens": 940559
      },
      "totalTokens": 31995039
    }
  ]
}
```

**Ключевые поля для функции допуска:**
- `isActive` — окно активно
- `projection.remainingMinutes` — **207 мин** осталось в 5-часовом окне
- `projection.totalTokens` — 118.6M токенов на полное окно (лимит ~271M)
- `burnRate.tokensPerMinute` — 418k token/min текущий расход
- `tokenCounts` — разбивка по типам (cache, input, output)

## 2. Человекочитаемый вывод

```
7/15/2026, 2:00:00 AM (1h 32m elapsed, 3h 27m remaining) | ACTIVE
  Models: fable-5, haiku-4-5, opus-4-8
  Used: 31,995,039 tokens (11.8%)
  Cost: $130.33
  Remaining: 239,267,048 tokens (88.2%)
  Projected: 118,625,207 tokens (43.7%) | $483.22
```

## 3. Источники данных

```
~/.claude/projects/
  -private-tmp-claude-501--Users-admin-b4fcb186-577c-4013-b3da-d42591ecd408-scratchpad
  -private-tmp-claude-501--Users-admin-b4fcb186-577c-4013-b3da-d42591ecd408-scratchpad-live-*
  
~/.config/claude/projects/
  ✗ не существует
```

## 4. Свободная RAM

```
Mach Virtual Memory: Pages free: 17,230 (~282 MB)
Total memory: 8,589,934,592 bytes = 8 GB
```

## Вывод

✓ **ccusage работает** — JSON парсится, все ключевые поля присутствуют.  
✓ **Функция допуска** может использовать:
  - `remainingMinutes` (207 = 3h 27m осталось в окне)
  - `totalTokens` projection (118.6M из 271M)
  - `burnRate.tokensPerMinute` (418k/min)

✓ **Машина**: 8 GB RAM → формула `8 / 5 = 1.6` → **можно 1 инстанс Claude Code** (свободно 282 MB).
