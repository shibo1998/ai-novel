"""P0/P1 修复回归：{book} 参数路径穿越守卫 + settings 结构校验。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ainovel import paths, settings


class TestBookNameGuard:
    """paths.validate_book_name 单元 + API 层穿越拦截（P0 回归）。"""

    @pytest.mark.parametrize(
        "bad",
        [
            "..",
            "...",
            "a/../b",
            r"..\data",
            "书名/子目录",
            "a:b",
            "尾点.",
            "尾空格 ",
            "",
            "  ",
            "a\tb",
            "a\nb",
        ],
    )
    def test_validate_rejects(self, bad: str):
        with pytest.raises(ValueError):
            paths.validate_book_name(bad)

    @pytest.mark.parametrize("ok", ["测试书", "我的书 第一卷", ".隐藏目录"])
    def test_validate_accepts(self, ok: str):
        assert paths.validate_book_name(ok) == ok

    def test_api_overview_traversal_blocked(self, home):
        """book='..'（URL 编码）必须被拒绝（400/404），而不是解析到仓库根返回数据。"""
        from ainovel.api import app

        c = TestClient(app)
        r = c.get("/api/books/%2E%2E/overview")
        assert r.status_code in (400, 404)
        assert "detail" in r.json()

    def test_api_delete_traversal_blocked(self, home):
        """DELETE /api/books/<穿越名> 必须 400，仓库文件不受影响。"""
        from ainovel.api import app

        c = TestClient(app)
        for payload in ["%2E%2E", r"..%5Cdata"]:
            assert c.delete(f"/api/books/{payload}").status_code in (400, 404)
        # 守卫生效：home 下文件原封不动
        assert (home / "prompts").exists()
        assert (home / "lint").exists()

    def test_api_write_traversal_blocked(self, home):
        """写入类端点同样受守卫保护（bible PUT 走 book_dir）。"""
        from ainovel.api import app

        c = TestClient(app)
        r = c.put("/api/books/%2E%2E/bible", json={"基础设定": "x"})
        assert r.status_code == 400


class TestSettingsStructureGuard:
    """settings 结构校验（P1 回归）：写不进坏结构，坏文件不致命。"""

    def test_update_rejects_scalar_provider(self, home):
        with pytest.raises(ValueError):
            settings.update({"providers": {"openai": "not-a-dict"}})
        # 磁盘未被破坏
        assert isinstance(settings.load()["providers"]["openai"], dict)

    def test_update_rejects_scalar_prices(self, home):
        with pytest.raises(ValueError):
            settings.update({"prices": 123})
        assert isinstance(settings.load()["prices"], dict)

    def test_load_tolerates_broken_file(self, home):
        """手改坏 settings.json 不致命：回退默认值，应用仍可启动。"""
        (home / "data").mkdir(exist_ok=True)
        (home / "data" / "settings.json").write_text(
            '{"providers": {"openai": "broken"}}', encoding="utf-8"
        )
        s = settings.load()
        assert isinstance(s["providers"]["openai"], dict)
        assert s["providers"]["openai"]["api_key"] == ""

    def test_masked_never_reveals_key(self, home):
        settings.update({"providers": {"openai": {"api_key": "sk-secret-abcdef"}}})
        masked = settings.masked()["providers"]["openai"]["api_key"]
        assert "sk-secret" not in masked
