"""frontmatter 解析 / 序列化 / 校验。"""
import pytest

from ainovel import frontmatter as FM


def test_parse_roundtrip():
    text = "---\ntitle: 书名\nstatus: drafting\n---\n正文内容\n"
    fm, body = FM.parse(text)
    assert fm == {"title": "书名", "status": "drafting"}
    assert body == "正文内容\n"
    assert FM.dump(fm, body) == text


def test_parse_missing_frontmatter_raises():
    with pytest.raises(FM.FrontmatterError):
        FM.parse("没有 frontmatter")


def test_parse_bad_yaml_raises():
    with pytest.raises(FM.FrontmatterError):
        FM.parse("---\nfoo: [unclosed\n---\nbody")


def test_validate_required_missing():
    with pytest.raises(FM.FrontmatterError, match="必填"):
        FM.validate({"a": 1}, {"required": ["b"]})


def test_validate_type_and_enum():
    schema = {"types": {"n": int}, "enums": {"st": ["draft", "final"]}}
    FM.validate({"n": 3, "st": "draft"}, schema)
    with pytest.raises(FM.FrontmatterError):
        FM.validate({"n": "3"}, schema)
    with pytest.raises(FM.FrontmatterError):
        FM.validate({"st": "mid"}, schema)


def test_validate_bool_not_int():
    with pytest.raises(FM.FrontmatterError):
        FM.validate({"n": True}, {"types": {"n": int}})


def test_atomic_write(tmp_path):
    from ainovel import atomic

    p = tmp_path / "a" / "b" / "f.md"
    atomic.write_text(p, "内容\n")
    assert p.read_text(encoding="utf-8") == "内容\n"
    atomic.write_text(p, "覆盖\n")
    assert p.read_text(encoding="utf-8") == "覆盖\n"
    # 不留临时文件
    assert list(p.parent.glob("*.tmp")) == []
