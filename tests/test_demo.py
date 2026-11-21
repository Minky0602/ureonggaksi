"""README '시연' 절이 말하는 결과를 고정한다(합성 자료, 로컬 AI 없음)."""

import hashlib
import importlib.util
from pathlib import Path

from openpyxl import load_workbook

from ureonggaksi.rules import RuleStore
from ureonggaksi.service import commit, prepare

ROOT = Path(__file__).resolve().parents[1]
REQUEST = "발급번호 기준으로 신청인이랑 생일, 이수시간 채워줘"


def _make_demo():
    spec = importlib.util.spec_from_file_location("make_demo", ROOT / "examples" / "make_demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_demo_fills_what_the_readme_says(tmp_path):
    paths = _make_demo().generate(tmp_path / "demo")
    sources, target = paths[:-1], paths[-1]
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    prepared = prepare(REQUEST, sources, target, output_path=tmp_path / "결과.xlsx")
    assert prepared.question is None
    assert {("pair", "성명", "신청인"), ("pair", "생년월일", "생일")} <= set(prepared.unsure)
    assert prepared.plan.counts.cells_write == 8
    assert [m.where for m in prepared.manual] == ["교육이수현황!E4"]
    package = commit(prepared, RuleStore(tmp_path / "rules"))
    assert package.status == "partial"
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before
    sheet = load_workbook(package.output_path)["교육이수현황"]
    assert [c.value for c in sheet[4]] == ["A-003", "박민수", "1993-07-21", None, "10시간"]
    # 같은 서식의 이수확인서가 셋이라 근거에 파일 이름이 있어야 어느 문서를 볼지 안다
    text = package.manual_path.read_text(encoding="utf-8")
    assert "[근거 이수확인서_박민수.hwpx " in text
