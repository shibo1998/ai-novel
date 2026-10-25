"""机械检查 M-01 ~ M-11（规格03 §2.4）。"""
from ainovel import mechcheck

WR = [2200, 2800]


def _hits(body, wr=WR):
    return mechcheck.check(body, word_range=wr)


def test_m01_word_count_block():
    assert any(h.rule == "M-01" and h.level == "block" for h in _hits("太短。"))
    body = "字" * 2500
    hits = _hits(body)
    assert not any(h.rule == "M-01" for h in hits)


def test_m02_banned_word_block():
    body = ("正常句子一个。" * 100) + "\n时光荏苒，岁月如梭。\n" + "正常句子两个。" * 100
    hits = _hits(body)
    banned = [h for h in hits if h.rule == "M-02"]
    assert banned and banned[0].level == "block"
    assert banned[0].word == "时光荏苒"


def test_m03_dialog_suffix_block():
    body = "他淡淡说道：来。" + "正常。" * 500
    hits = _hits(body)
    m3 = [h for h in hits if h.rule == "M-03"]
    assert m3 and m3[0].level == "block"


def test_m04_sensitive_block_and_flag():
    body = "情节推进。" * 300 + "\n他提到了国家领导人。"
    hits = _hits(body)
    assert mechcheck.has_sensitive(hits)
    assert not mechcheck.has_sensitive([h for h in hits if h.rule != "M-04"])


def test_m05_comma_warn():
    body = "一句，两个，逗号，太多。" + "正常。" * 600
    assert any(h.rule == "M-05" for h in _hits(body))


def test_m06_punct_density():
    body = ("哇！" * 3 + "正常句子。") * 200  # 每千字感叹号 > 2
    assert any(h.rule == "M-06" for h in _hits(body))


def test_m07_flat_sentences():
    body = "一二三四五。" * 8
    assert any(h.rule == "M-07" for h in _hits(body))


def test_m08_long_paragraph():
    body = "字" * 130 + "\n" + "短。" * 400
    assert any(h.rule == "M-08" for h in _hits(body))


def test_m09_repeated_para_prefix():
    body = "他走了出去。\n他走向门前。\n他走得极快。\n" + "别的。" * 200
    assert any(h.rule == "M-09" for h in _hits(body))


def test_m10_ending_summary():
    body = "正文推进。" * 400 + "\n他知道。"
    assert any(h.rule == "M-10" for h in _hits(body))


def test_m11_first_person_ratio():
    body = ("我想我知道我要去哪里找我需要的东西我想我明白。" * 40)
    # 构造无对话、我字高密度
    assert any(h.rule == "M-11" for h in _hits(body))


def test_clean_mock_prose_passes_blocks():
    from ainovel.mockllm import make_prose

    hits = mechcheck.check(make_prose((2200, 2800)), word_range=WR)
    assert not mechcheck.has_block(hits)
