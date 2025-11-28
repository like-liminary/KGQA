#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
功能：
1. 连接 Neo4j
2. 清空库中所有数据
3. 将 pds_val.json 中的所有三元组导入 Neo4j
   - 实体类型(subject_type / object_type) 作为节点标签
   - 实体文本(subject / object) 作为节点属性 name
   - 关系(predicate) 作为关系类型
"""

import json
import re
from neo4j import GraphDatabase
from tqdm import tqdm

# ====================== 配 置 区 ======================

# Neo4j 连接配置
NEO4J_URI = "bolt://localhost:7687"     # 按实际情况修改
NEO4J_USER = "neo4j"                    # 按实际情况修改
NEO4J_PASSWORD = "12345678"             # 按实际情况修改

# 数据文件路径
PDS_VAL_PATH = "./data/pds_val.json"   # 如果不在这个路径，改成你自己的 pds_val.json 路径

# ====================== 工 具 函 数 ======================

def escape_for_cypher_name(s: str) -> str:
    """
    去掉字符串里的反引号，避免和 Cypher 里的 ` 冲突。
    其他字符（中文、空格）在反引号包裹下是允许的。
    """
    if s is None:
        return ""
    return s.replace("`", "")

def extract_text_or_value(x):
    """
    有些数据结构中 object / object_type 可能是：
      - 字符串，例如 "人物"
      - dict，例如 {"@value": "人物"}
    这个函数统一取出真正的字符串值。
    """
    if isinstance(x, dict):
        return x.get("@value", "")
    return x

def clear_neo4j(session):
    """清空 Neo4j 中的所有节点和关系。"""
    print("⚠️  正在清空 Neo4j 中的所有数据 ...")
    session.run("MATCH (n) DETACH DELETE n")
    print("✅ 已清空所有节点和关系。")

def create_triple(tx, subject, subject_type, predicate, obj, object_type):
    """
    在一个事务中写入单个三元组：
      (subject:subject_type)-[:predicate]->(object:object_type)
    """
    # 确保都是字符串
    subject = str(subject).strip()
    obj = str(obj).strip()
    subject_type = escape_for_cypher_name(str(subject_type).strip())
    object_type = escape_for_cypher_name(str(object_type).strip())
    predicate = escape_for_cypher_name(str(predicate).strip())

    if not subject or not obj or not subject_type or not object_type or not predicate:
        return

    # 使用反引号包裹 Label 和 Relationship Type，
    # 这样可以直接使用中文、空格等特殊字符。
    cypher = f"""
    MERGE (s:`{subject_type}` {{name: $s_name}})
    MERGE (o:`{object_type}` {{name: $o_name}})
    MERGE (s)-[r:`{predicate}`]->(o)
    """
    tx.run(cypher, s_name=subject, o_name=obj)

# ====================== 主 逻 辑 ======================

def main():
    # 连接 Neo4j
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    with driver.session() as session:
        # 1. 清空全部数据
        clear_neo4j(session)

        # 2. 统计总行数（仅用于进度条显示）
        print("🔍 正在统计 pds_val.json 行数，用于进度显示 ...")
        total_lines = 0
        with open(PDS_VAL_PATH, "r", encoding="utf-8") as f:
            for _ in f:
                total_lines += 1
        print(f"✅ 文件总行数：{total_lines}")

        # 3. 逐行读取 pds_val.json，并导入三元组
        print("🚀 开始导入三元组到 Neo4j ...")
        with open(PDS_VAL_PATH, "r", encoding="utf-8") as f:
            for line in tqdm(f, total=total_lines, desc="导入进度", unit="行"):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    # 如果有异常行（比如最后一行是文字说明），直接跳过
                    continue

                spo_list = data.get("spo_list") or []
                if not isinstance(spo_list, list):
                    continue

                # 对该行里的所有 spo 做导入
                def write_tx(tx):
                    for spo in spo_list:
                        if not isinstance(spo, dict):
                            continue
                        subject = spo.get("subject", "")
                        subject_type = spo.get("subject_type", "")
                        predicate = spo.get("predicate", "")
                        obj = extract_text_or_value(spo.get("object", ""))
                        object_type = extract_text_or_value(spo.get("object_type", ""))

                        create_triple(tx, subject, subject_type, predicate, obj, object_type)

                session.write_transaction(write_tx)

        print("🎉 导入完成！所有三元组已写入 Neo4j。")

    driver.close()


if __name__ == "__main__":
    main()
