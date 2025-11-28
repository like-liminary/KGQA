######
# 配置文件 (Neo4j, API Key等)
######

# config.py
import os

class Config:
    # Neo4j 配置
    NEO4J_URI = "bolt://localhost:7687"
    NEO4J_USER = "neo4j"
    NEO4J_PASSWORD = "12345678"

    # LLM 配置 (以OpenAI协议为例，可换DeepSeek, Zhipu等)
    LLM_API_KEY = "sk-QVoq8nlZl0sNZA8hNG3hWEFb4pyye4RGvJRAcoS27ZGoEFO9"
    LLM_BASE_URL = "http://chatapi.littlewheat.com/v1" # 或者你的中转地址
    LLM_MODEL = "gpt-4o" # 选择适合的模型

    # 实体类型定义
    ENTITY_TYPES = [
        "故障现象", "设备", "部件", "缺陷类型", 
        "故障原因", "位置", "人员", "处理措施", "班组"
    ]