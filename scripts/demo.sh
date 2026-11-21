#!/usr/bin/env sh
# 합성 시연을 명령행으로 끝까지 돈다: 자료 생성 → prepare(미리보기·지문) → commit(저장).
# 로컬 AI 없이 동작한다. 결과는 임시 폴더에 만들고 원본 취합표가 그대로인지 확인한다.
set -eu
cd "$(dirname "$0")/.."
out="${1:-$(mktemp -d)}"
python examples/make_demo.py "$out" >/dev/null
before=$(sha256sum "$out/교육이수현황.xlsx" | cut -d' ' -f1)

# tee를 쓰면 dash(pipefail 없음)에서 prepare의 종료 코드를 잃는다. 파일로 받은 뒤 보여 준다.
set +e
ureonggaksi prepare --request "발급번호 기준으로 신청인이랑 생일, 이수시간 채워줘" \
    --target "$out/교육이수현황.xlsx" --out "$out/작업.plan.json" \
    "$out/이수확인서_홍길동.hwpx" "$out/이수확인서_김영희.hwpx" "$out/이수확인서_박민수.hwpx" \
    >"$out/prepare.txt" 2>&1
code=$?
set -e
cat "$out/prepare.txt"
[ "$code" -eq 0 ] || { echo "prepare 종료 코드 $code" >&2; exit "$code"; }

# 지문은 출력을 긁지 않고 계획 파일에서 읽는다.
hash=$(python -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["plan_hash"])' "$out/작업.plan.json")
set +e
ureonggaksi commit --plan "$out/작업.plan.json" --plan-hash "$hash"
code=$?
set -e
after=$(sha256sum "$out/교육이수현황.xlsx" | cut -d' ' -f1)
[ "$before" = "$after" ] || { echo "원본 취합표가 바뀌었습니다" >&2; exit 2; }
echo "원본 해시 그대로: $before"
ls "$out"
# 시연 자료에는 일부러 직접 처리할 칸이 하나 있어 commit은 1(일부 채움)로 끝난다. 2 이상만 실패다.
[ "$code" -le 1 ]
