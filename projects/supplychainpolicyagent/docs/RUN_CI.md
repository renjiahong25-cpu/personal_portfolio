# Run CI（P7：可维护性一键门禁）

本地 / 部署机统一执行三项门禁：**ruff 静态检查 → pytest 单元测试 → 全量语法编译**。

## 一键运行

Windows（开发机）：

```bat
run_ci.bat
```

Linux / 部署机：

```bash
./run_ci.sh            # 默认 python3；可 PYTHON=xxx ./run_ci.sh 指定解释器
```

全部通过输出 `CI PASS`，任一失败立即以非 0 退出。

## 各部分说明

| 步骤 | 命令 | 作用 |
|------|------|------|
| 1 ruff | `ruff check .` | 规则集见 `pyproject.toml [tool.ruff.lint]`：`E4/E7/E9/F`（缩进、语句、运行时错误、未定义/未使用）。样式类规则有意不启用，避免历史代码大改。历史一次性脚本与临测文件豁免（per-file-ignores）。 |
| 2 pytest | `pytest tests/unit` | 单元测试（`pytest.ini` 指向 `tests/unit`）。`test_dist_lock` 无 MySQL/Redis 时自动 skip。 |
| 3 compileall | `compileall`（排除 `.git/.venv/frontend`）| 全量语法编译门禁。 |

## GitHub Actions

`.github/workflows/ci.yml`：push / PR 时跑同样三步骤（`ruff → pytest tests/unit → compileall`），Python 3.11。

- 本地运行：
```bash
pip install ruff
python -m ruff check .
python -m pytest tests/unit
```

## 新增交付脚本的持续约束

`scripts/p6_inflate_data.py`、`scripts/p6_load_test.py` 不享受历史脚本豁免，与生产代码同标准保持 ruff 干净——修改它们后请重新跑一次 `run_ci`。