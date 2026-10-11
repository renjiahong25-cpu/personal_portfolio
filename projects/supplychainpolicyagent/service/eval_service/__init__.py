# -*- coding: utf-8 -*-
"""
模型评测服务
- 评测集生成（eval_set_generator）：AI 自动生成三级配比评测集
- 回归测试（regression_tester）：版本迭代自动回归 + 结果入库
- 指标计算（metrics_calculator）：红线/核心/体验指标 + 恶化告警
- BadCase 管理（badcase_manager）：自动入库/分类/评测集扩充
- 评测报告（report_generator）：单次/对比/趋势报告
"""
from .eval_set_generator import EvalSetGenerator, generate_eval_set
from .regression_tester import RegressionTester, run_regression
from .metrics_calculator import MetricsCalculator, calculate_metrics
from .badcase_manager import BadCaseManager
from .report_generator import ReportGenerator, build_single_report

__all__ = [
    "EvalSetGenerator",
    "generate_eval_set",
    "RegressionTester",
    "run_regression",
    "MetricsCalculator",
    "calculate_metrics",
    "BadCaseManager",
    "ReportGenerator",
    "build_single_report",
]