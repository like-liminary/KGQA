######
# 大模型API调用封装
######

# llm_client.py
from openai import OpenAI
import json
import re
from config import Config

client = OpenAI(api_key=Config.LLM_API_KEY, base_url=Config.LLM_BASE_URL)

def call_llm(prompt, temperature=0.3, json_mode=False):
    """通用LLM调用函数"""
    try:
        messages = [{"role": "user", "content": prompt}]
        response = client.chat.completions.create(
            model=Config.LLM_MODEL,
            messages=messages,
            temperature=temperature,
            response_format={"type": "json_object"} if json_mode else None
        )
        content = response.choices[0].message.content
        return content
    except Exception as e:
        print(f"LLM Call Error: {e}")
        return None

def parse_json_from_llm(content):
    """尝试从LLM输出中解析JSON，处理Markdown包裹的情况"""
    try:
        # 去除可能的 ```json ... ```
        content = re.sub(r"```json\n|\n```", "", content).strip()
        content = re.sub(r"```", "", content).strip()
        return json.loads(content)
    except json.JSONDecodeError:
        print(f"JSON Parse Error. Raw content: {content}")
        return None