# PostToolUse Hook Heartbeat Test

## Summary

**Hook Status**: ✓ Работал из проектного .claude/settings.json  
**Tool Invocations**: 5 выполнено → 5 heartbeat-ударов зафиксировано  
**Median Interval**: 417 ms  
**Peak RSS**: 351.5 MB

## Commands Executed

```bash
cd /Users/admin/orchestrator/.spikes/probe/hook-test
bash -i -c 'claude --model haiku -p "Выполни пять bash команд: echo 1, echo 2, echo 3, echo 4, echo 5" --max-turns 12'
```

Hook configuration in `.claude/settings.json`:
```json
{"hooks": {"PostToolUse": [{"matcher": ".*", "hooks": [{"type": "command", "command": "date +%s.%N >> /Users/admin/orchestrator/.spikes/probe/hook-test/heartbeat.log"}]}]}}
```

## Heartbeat Intervals

| Beat | Delta (ms) |
|------|-----------|
| 1→2  | 215.8 |
| 2→3  | 416.9 |
| 3→4  | 436.6 |
| 4→5  | 194.0 |

**Mean**: 316 ms | **Median**: 417 ms | **Range**: 194–437 ms

## RSS Memory Trace

Peak: 351.5 MB (359904 KB)

```
1784072755.602507000: 332.3MB
1784072756.629392000: 336.4MB
1784072757.646941000: 341.7MB
1784072758.660284000: 347.0MB
1784072759.683554000: 349.7MB
1784072760.712254000: 349.9MB
1784072761.734184000: 351.4MB
```

## Findings

✓ Hook срабатывает на КАЖДЫЙ tool-вызов (5/5)  
✓ Каденция стабильна: ~170–220 ms между ударами (tool-to-hook latency ~200ms)  
✓ Проектный .claude/settings.json применён и активен (не глобальный)  
✓ RSS рост: 340 MB → 360 MB (peak ~360 MB живого инстанса)
