#!/usr/bin/env bash
# PreToolUse(Bash) 훅 — 검증을 통과하지 않은 브리프가 커밋되는 것을 막는다.
#
# AGENTS.md는 "검증 미통과 상태로 커밋하지 않는다"고 적어두지만 그것은 권고다.
# 훅은 결정적이다. stdin으로 들어온 훅 JSON에서 실행될 명령을 꺼내, git commit 이고
# 스테이징된 브리프가 있으면 validate_brief.py 를 돌린다. 실패하면 exit 2 로 커밋을 막고
# 그 출력을 에이전트에게 돌려준다(에이전트는 그 지적을 고친 뒤 다시 커밋한다).
#
# 검사 대상은 **스테이징된 briefs/YYYY-MM-DD-*.md 뿐**이다. 문서·스크립트 커밋은 그냥 통과한다.

set -uo pipefail

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$repo_root" || exit 0

payload=$(cat)
command=$(printf '%s' "$payload" | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("tool_input", {}).get("command", ""))
except Exception:
    print("")')

# git commit 이 아니면 관여하지 않는다 (git log --format=...commit... 같은 오탐을 피한다).
printf '%s' "$command" | grep -Eq '(^|[;&|[:space:]])git ([^;&|]*[[:space:]])?commit([[:space:]]|$)' || exit 0

# mapfile 은 bash 4+ 전용이라 macOS 기본 bash 3.2 에서 죽는다. 파일명 규칙상 공백이 없으므로
# 개행 기준 분리로 충분하다.
staged=$(git diff --cached --name-only --diff-filter=ACM -- 'briefs/*.md' \
  | grep -E '^briefs/[0-9]{4}-[0-9]{2}-[0-9]{2}-.+\.md$' || true)
[ -z "$staged" ] && exit 0
IFS=$'\n'
# shellcheck disable=SC2086
if ! out=$(python3 scripts/validate_brief.py $staged 2>&1); then
  {
    echo "브리프 검증 실패 — 커밋을 막았다. 아래를 고치고 다시 커밋한다."
    echo "$out"
    echo
    echo "재실행: python3 scripts/validate_brief.py $staged"
  } >&2
  exit 2
fi
exit 0
