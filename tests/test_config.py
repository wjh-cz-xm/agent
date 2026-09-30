"""config.py 测试：目录解析、旧格式迁移、账号操作、wait_before_answer 归一化"""

import json
from pathlib import Path

import pytest

import xmu_rollcall.config as c


# ---------- get_config_dir 解析顺序 ----------

def test_get_config_dir_env_wins(monkeypatch, tmp_path):
    target = tmp_path / "envdir"
    monkeypatch.setenv("XMU_ROLLCALL_CONFIG_DIR", str(target))
    assert c.get_config_dir() == target


def test_get_config_dir_home_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("XMU_ROLLCALL_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    assert c.get_config_dir() == tmp_path / "home" / ".xmu_rollcall"


def test_get_config_dir_cwd_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("XMU_ROLLCALL_CONFIG_DIR", raising=False)

    def bad_home():
        raise RuntimeError("no home")

    monkeypatch.setattr(Path, "home", bad_home)
    monkeypatch.chdir(tmp_path)
    assert c.get_config_dir() == tmp_path / ".xmu_rollcall"


# ---------- load_config ----------

def test_load_config_legacy_migration(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"username": "u1", "password": "p1"}), encoding="utf-8")
    monkeypatch.setattr(c, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(c, "CONFIG_FILE", cfg_file)
    cfg = c.load_config()
    assert cfg["accounts"][0]["username"] == "u1"
    assert cfg["current_account_id"] == 1


def test_load_config_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(c, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(c, "CONFIG_FILE", tmp_path / "nope.json")
    assert c.load_config() == c.DEFAULT_CONFIG.copy()


# ---------- wait_before_answer 归一化 ----------

@pytest.mark.parametrize("raw,expected", [
    (None, 0),
    (False, 0),
    (True, 0),
    (0, 0),
    (-3, 0),
    (5, 5),
    ("3", 3),
    ("abc", 0),
    (2.9, 2),
])
def test_get_wait_before_answer(raw, expected):
    assert c.get_wait_before_answer({"wait_before_answer": raw}) == expected


def test_get_wait_before_answer_default():
    assert c.get_wait_before_answer({}) == 0
    assert c.get_wait_before_answer(None) == 0


# ---------- 账号操作 ----------

def test_add_account_sets_current():
    cfg = c.DEFAULT_CONFIG.copy()
    aid = c.add_account(cfg, "u", "p", "张三")
    assert aid == 1
    assert cfg["current_account_id"] == 1
    assert c.get_current_account(cfg)["username"] == "u"


def test_delete_account_renumbers(monkeypatch, tmp_path):
    monkeypatch.setattr(c, "CONFIG_DIR", tmp_path)
    cfg = {
        "accounts": [
            {"id": 1, "username": "a", "password": "1"},
            {"id": 2, "username": "b", "password": "2"},
            {"id": 3, "username": "c", "password": "3"},
        ],
        "current_account_id": 3,
    }
    ok, to_del, to_rename = c.delete_account(cfg, 2)
    assert ok is True
    assert [a["id"] for a in cfg["accounts"]] == [1, 2]
    assert cfg["current_account_id"] == 2  # 原 3 → 新 2
    assert to_del == str(tmp_path / "2.json")


def test_is_config_complete():
    good = {"accounts": [{"id": 1, "username": "u", "password": "p"}], "current_account_id": 1}
    assert c.is_config_complete(good) is True
    bad = {"accounts": [{"id": 1, "username": "", "password": "p"}], "current_account_id": 1}
    assert c.is_config_complete(bad) is False
