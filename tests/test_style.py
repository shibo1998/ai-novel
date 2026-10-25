"""M5 文风系统：语料 → F1 特征卡 → F2 注入。"""
from ainovel import assembler, pipeline, style
from ainovel.mockllm import MockLLM


def test_corpus_card_toggle_flow(book_env):
    book, _ = book_env
    assert style.read_card(book) == "" and not style.enabled(book)

    style.write_corpus(book, "测试语料。" * 200)
    mock = MockLLM()
    # F1 生成特征卡（走管线计费与日志）
    from ainovel import prompts as P

    p = P.get("F1", book)
    res, _p, _t = pipeline._call(book, None, "style", "F1", {"语料样本": style.read_corpus(book)},
                                 client=mock)
    style.write_card(book, res.text)
    assert "句法特征" in style.read_card(book)

    # 未启用：system 段不含特征卡
    sys_off = assembler.render_system(book)
    assert "文风特征卡" not in sys_off

    style.set_enabled(book, True)
    sys_on = assembler.render_system(book)
    assert "文风特征卡" in sys_on and "句法特征" in sys_on

    style.set_enabled(book, False)
    assert "文风特征卡" not in assembler.render_system(book)


def test_enabled_requires_card(book_env):
    book, _ = book_env
    style.set_enabled(book, True)
    # 卡不存在时 enabled() 仍返回 False（读卡为准）
    assert style.enabled(book) is False
