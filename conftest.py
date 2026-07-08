"""Pytest configuration — ensure llm_harness_py is importable.

pytest 配置：确保 llm_harness_py 可导入。
将运行时仓库 venv 的 site-packages 加入 sys.path，并将项目根目录加入 sys.path。
"""
import sys
import os

_HARNESS_VENV = os.path.expanduser(
    "~/Documents/projs/oh-my-harness/REDACTED/llm-harness-py/.venv/lib"
)
if os.path.isdir(_HARNESS_VENV):
    for entry in os.listdir(_HARNESS_VENV):
        sp = os.path.join(_HARNESS_VENV, entry, "site-packages")
        if os.path.isdir(sp):
            sys.path.insert(0, sp)

# 让 `from blender_scene.bridge import BlenderBridge` 可导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
