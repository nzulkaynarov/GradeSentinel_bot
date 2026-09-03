#!/usr/bin/env bash
# PostToolUse-hook (Edit|Write): мгновенная проверка отредактированного файла.
#   *.py            → python -m py_compile (синтаксис)
#   locales/*.json  → валидный JSON
# Exit 2 + stderr → Claude видит ошибку и чинит сразу, не дожидаясь прогона тестов.
set -u
input="$(cat)"
file="$(printf '%s' "$input" | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin); print(d.get("tool_input",{}).get("file_path",""))
except Exception:
    print("")' 2>/dev/null)"
[ -z "$file" ] && exit 0
[ -f "$file" ] || exit 0

case "$file" in
  *.py)
    if ! out="$(python3 -m py_compile "$file" 2>&1)"; then
      echo "py_compile FAILED for $file:" >&2
      echo "$out" >&2
      exit 2
    fi
    ;;
  */locales/*.json|*.json)
    if ! out="$(python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$file" 2>&1)"; then
      echo "Invalid JSON in $file:" >&2
      echo "$out" >&2
      exit 2
    fi
    ;;
esac
exit 0
