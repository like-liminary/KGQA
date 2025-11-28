######
# 统一管理所有提示词
######

# prompts.py
from config import Config

TYPES_STR = ", ".join(Config.ENTITY_TYPES)

PROMPTS = {

    # --- 新增：严格三元组抽取 ---
    "triplet_extraction": """
你是一个知识图谱构建专家。请从下面的文本中提取实体关系三元组。
文本内容: "{text}"

请严格遵守以下抽取模式（Schema），不要提取模式之外的关系：
1. ("subject_type": "故障现象", "predicate": "发生于", "object_type": "设备")
2. ("subject_type": "故障现象", "predicate": "发生于", "object_type": "部件")
3. ("subject_type": "故障现象", "predicate": "现象表征", "object_type": "缺陷类型")
4. ("subject_type": "缺陷类型", "predicate": "引起原因", "object_type": "故障原因")
5. ("subject_type": "缺陷类型", "predicate": "导致", "object_type": "故障现象")
6. ("subject_type": "设备", "predicate": "位于", "object_type": "位置")
7. ("subject_type": "设备", "predicate": "组成部分", "object_type": "部件")
8. ("subject_type": "人员", "predicate": "采取", "object_type": "处理措施")
9. ("subject_type": "班组", "predicate": "采取", "object_type": "处理措施")

返回格式要求：
1. 只返回符合上述模式的三元组。
每个三元组必须包含：
    - 主语及其类型
    - 关系
    - 宾语及其类型
返回结果必须为列表包含字典的 JSON 列表格式，结构如下：
    [
        {{
            "predicate": "关系类型",
            "subject": "主语",
            "subject_type": "主语实体类型",
            "object": "宾语",
            "object_type": "宾语实体类型"
        }},
        {{
            "predicate": "关系类型",
            "subject": "主语",
            "subject_type": "主语实体类型",
            "object": "宾语",
            "object_type": "宾语实体类型"
        }}
    ]
3. 如果没有符合模式的关系，返回空列表 []。
注意：请勿返回其他多余内容，只返回JSON列表。
""",
    # --- 新增：从搜索结果提取信息 ---
    "extract_search_info": """
你是一个信息整合专家。
用户问题: "{question}"
联网搜索到的原始信息片段:
{search_results}

请分析上述搜索结果：
1. 提取与用户问题紧密相关的事实、数据或最新进展。
2. 忽略广告、无关内容或重复信息。

请输出提取后的精炼信息，不要输出多余的文字。
""",
    # --- 新增：聊天记录摘要 ---
    "summarize_history": """
你是一个对话记录员。
已有历史摘要: "{old_summary}"
新发生的对话片段:
{new_lines}

请将新对话片段的内容合并到历史摘要中，生成一个新的、简练的摘要。
重点保留：用户提到的设备名称、发生的故障、采取的措施、以及重要的上下文信息。
尽量控制字数，不要遗漏关键实体。
请直接输出新的摘要内容，不要包含多余文字。
""",

    # --- 新增：从历史中提取有用信息 ---
    "extract_history_info": """
你是一个助手。
历史对话摘要: "{summary}"
最近的对话记录:
{recent_history}

用户当前问题: "{question}"

请分析历史信息，提取出对回答当前问题有帮助的背景信息。
如果历史记录与当前问题无关，请返回“无”。
请直接输出你提取到的有用的背景信息内容，不要输出多余的文字。
""",
    # 1. 实体提取与类型预测 (已修改)
    "entity_extraction": """
你是一个知识图谱实体提取专家。
用户问题: "{question}"

请先判断该问题是否需要检索知识图谱中的实体来回答：
1. 如果是简单的寒暄（如"你好"、"你是谁"）、无意义的字符、或者完全不需要专业知识的问题，请直接返回空列表: []
2. 如果问题涉及具体的设备、故障、操作等，请提取需要检索的实体。

对于提取的每个实体，从以下类型中选出最可能的2个类型: [{types_str}]。
请严格按JSON格式返回，不要包含markdown标记:
[
    {{"entity": "实体名1", "types": ["类型A", "类型B"]}},
    ...
]
""".replace("{types_str}", TYPES_STR), 

    # 2. 实体链接判别 (保持不变)
    "entity_linking": """
用户问题: "{question}"
预测实体: "{predicted_entity}"
候选实体列表:
{candidates_str}

请判断预测实体与哪个候选实体是指同一个事物。
请返回JSON格式，包含所有候选实体的链接系数(0.0-1.0):
[
    {{"id": 候选实体ID, "name": "候选实体名", "score": 0.95}},
    ...
]
注意：务必返回JSON格式，不要包含任何多余文字或markdown标记。
""",

    # 3. 路径选择 (保持不变)
    "path_selection": """
用户问题: "{question}"
当前实体: "{current_node_name}"
当前已走过的路径: {history_context}

可选的下一跳三元组 (ID: 三元组内容):
{triplets_str}

请分析：
1. 从上述三元组中，**挑选出所有**对回答问题有帮助的三元组（可以选择多个，如果都没有帮助返回空列表）。
2. 对于每个选中的三元组，判断是否需要沿着该路径继续向下检索更多细节？(True/False)

请严格按JSON列表格式返回:
[
    {{"id": "三元组ID1", "continue_search": true}},
    {{"id": "三元组ID2", "continue_search": false}}
]
""",

    # 4. 最终回答 (保持不变)
    "final_answer": """
用户问题: "{question}"

三方面信息:
【历史上下文信息】: {history_info}
【知识图谱检索结果】:
{kg_context}
【联网搜索补充信息】:
{web_info}

指令:
1. 综合上述三方面信息回答问题。
2. 知识图谱提供内部专业数据，联网信息提供外部最新数据，历史上下文提供对话背景。
3. 如果知识图谱中没有相关信息，尝试用上下文信息或联网搜索补充信息或你自己的知识回答，并注明来源。
4. 请条理清晰地组织答案。
""",

    # 5. 直接问答/闲聊 (新增)
    "direct_chat": """
你是一个“配电室故障问答小助手”。
用户问题与相关历史聊天记录: "{question}"

请以该身份直接、亲切、专业地回答用户的问题。不需要检索知识库。
"""
}