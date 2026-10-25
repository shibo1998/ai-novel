"""提示词库：23 条全量加载 / digest 稳定 / 渲染严格 / 书级覆盖。"""
import pytest

from ainovel import prompts

ALL_IDS = {"S0", "S1", "S2", "F2", "G1", "G2", "G3", "G4", "G5", "G6", "T1",
           "W1", "W2", "W3", "W4", "W5", "V1", "V2", "V3", "V4", "V5", "V6", "F1"}


def test_all_23_prompts_load(home):
    lib = prompts.library()
    assert ALL_IDS <= set(lib)
    assert len(lib) >= 23


def test_required_frontmatter(home):
    for p in prompts.library().values():
        assert p.fm.get("model_stage"), p.id
        assert isinstance(p.fm.get("temperature"), (int, float)), p.id
        assert p.digest and len(p.digest) == 64


def test_digest_stable(home):
    p1 = prompts.get("W1")
    p2 = prompts.get("W1")
    assert p1.digest == p2.digest


def test_render_missing_var_raises(home):
    with pytest.raises(prompts.PromptError, match="未提供变量"):
        prompts.render("W1", {"系统段": "x"})  # 缺其余变量


def test_render_fills(home):
    p, text = prompts.render("V5", {"正文": "ABCD", "问题清单": "问题", "字数区间": "2200-2800"})
    assert "〖" not in text
    assert "ABCD" in text


def test_book_override(home, book_env):
    book, h = book_env
    override_dir = h / "books" / book / "prompts" / "review"
    override_dir.mkdir(parents=True)
    src = (h / "prompts" / "review" / "V5.md").read_text(encoding="utf-8")
    (override_dir / "V5.md").write_text(src.replace("定向修复", "定向修复·书级改版"), encoding="utf-8")
    p = prompts.get("V5", book)
    assert p.source == "book"
    assert "书级改版" in p.full_text()
    assert p.digest != prompts.get("V5").digest
    assert prompts.get("V5").source == "global"  # 全局不受影响
