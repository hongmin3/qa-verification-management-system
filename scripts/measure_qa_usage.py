"""Read-only usage aggregation. Never launches Claude."""
import argparse
import json
from pathlib import Path

def summarize_usage(events: list[dict]) -> dict:
    keys = ('input_tokens','cache_creation_input_tokens','cache_read_input_tokens','output_tokens')
    result = {key: 0 for key in keys}
    result.update(calls=0, thinking_tokens=0, cost_usd=0.0, turns=0, tool_calls=0, failed_calls=0, quota=[])
    seen = set()
    for event in events:
        if event.get('type') == 'assistant':
            result['tool_calls'] += sum(c.get('type') == 'tool_use' for c in event.get('message',{}).get('content',[]))
        if event.get('type') == 'rate_limit_event':
            result['quota'].append(event.get('rate_limit_info',{}))
        if event.get('type') != 'result':
            continue
        identity = event.get('uuid') or event.get('session_id')
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        result['calls'] += 1
        result['failed_calls'] += bool(event.get('is_error'))
        usage = event.get('usage') or {}
        for key in keys:
            result[key] += int(usage.get(key,0) or 0)
        result['thinking_tokens'] += int(usage.get('output_tokens_details',{}).get('thinking_tokens',0) or 0)
        result['cost_usd'] += float(event.get('total_cost_usd',0) or 0)
        result['turns'] += int(event.get('num_turns',0) or 0)
    result['total_tokens'] = sum(result[k] for k in keys)
    return result

def main():
    parser=argparse.ArgumentParser(description='저장된 Claude stream-json 사용량 집계 (AI 호출 없음)')
    parser.add_argument('paths',nargs='+',type=Path)
    args=parser.parse_args()
    events=[]
    for path in args.paths:
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.strip(): events.append(json.loads(line))
    print(json.dumps(summarize_usage(events),ensure_ascii=False,indent=2))

if __name__ == '__main__': main()
