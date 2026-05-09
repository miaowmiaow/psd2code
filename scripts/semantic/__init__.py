# -*- coding: utf-8 -*-
"""语义命名流水线（Semantic Naming Pipeline）

把"原始 PSD 图层名 → 简短英文语义 token"这件事，从 ``common/semantic.py``
单一关键词函数升级为**多层次、可仲裁、可视觉兜底**的流水线。

设计目标：
    1. 让 ``common/utils.make_image_filename`` 与 ``targets/html/codegen/naming.py``
       共享同一套命名实现（保证图片名 ≡ class 名"去 hash 后缀"）。
    2. 行为可逐步替换：第一阶段（PR-1）只是把分散在两处的 ``extract_semantic_token``
       调用统一收敛到 ``NameResolver`` 这一个入口，**不改变任何输出**。
    3. 在 ``NameResolver`` 内部叠加多层来源（已落地）：
        - Layer 1 清洗 + 词典扩展（``layer1_cleaner.Layer1Cleaner``）
        - Layer 2 DOM 角色推断（``layer2_role_inferer.Layer2RoleInferer``）
        - Fallback 关键词表 + 拼音兜底（``common/semantic.extract_semantic_token``）
       任何一层失败都退到下一层，最终兜底到 ``extract_semantic_token``，
       保证 pipeline 永远不会因为命名层失败而崩溃。

       说明：早期设计文档提到的 "Layer 4 图片名 ≡ class 名同步" 由写出阶段
       天然满足、不需要独立模块；"Layer 5 视觉兜底（OpenCV/pHash）" 仅为
       未来规划，**当前未实现**，本子包不依赖任何视觉库。

对外 API（**调用方只需要这两个**）：
    * ``NameResolver``        —— 统一命名仲裁器（带状态：缓存 + 上下文）
    * ``NameCandidate``       —— 各层产出的统一数据结构

向后兼容：
    * ``extract_semantic_token`` 仍然作为 Layer 1 的核心实现存在于
      ``common/semantic.py``，本流水线只是上层调度者，不会"屏蔽"它。
    * 调用方升级路径：``extract_semantic_token(name, ltype)`` →
      ``NameResolver().resolve_token(name, ltype)``，返回值语义完全一致
      （空串表示"无语义"，调用方继续用 ltype 兜底）。
"""

from __future__ import annotations

from .name_resolver import NameResolver, NameCandidate

__all__ = ["NameResolver", "NameCandidate"]
