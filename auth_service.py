######
# 用户注册/登录逻辑 (基于JSON)
######

# auth_service.py
import json
import os
from werkzeug.security import generate_password_hash, check_password_hash
import threading # 新增

USER_FILE = 'users.json'
file_lock = threading.RLock()

def load_users():
    with file_lock: # 加锁读取
        if not os.path.exists(USER_FILE):
            return {}
        try:
            with open(USER_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}

def save_users(users):
    with file_lock: # 加锁写入
        with open(USER_FILE, 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=4)

def register_user(username, password):
    with file_lock:
        users = load_users()
        if username in users:
            return False, "用户名已存在"
        
        users[username] = {
            "password": generate_password_hash(password),
            "history": [] # 聊天记录结构: [{"id": 1, "title": "...", "msgs": [...]}]
        }
        save_users(users)
        return True, "注册成功"

def login_user(username, password):
    with file_lock:
        users = load_users()
        if username not in users:
            return False, "用户不存在"
        
        if check_password_hash(users[username]["password"], password):
            return True, "登录成功"
        return False, "密码错误"

def get_user_history(username):
    with file_lock:
        users = load_users()
        return users.get(username, {}).get("history", [])

# def save_chat_record(username, chat_id, user_msg, bot_msg):
#     users = load_users()
#     if username not in users: return
    
#     history = users[username]["history"]
    
#     # 查找是否存在该对话，不存在则创建
#     chat = next((c for c in history if c["id"] == chat_id), None)
#     if not chat:
#         chat = {"id": chat_id, "title": user_msg[:10]+"...", "msgs": []}
#         history.insert(0, chat) # 新对话放前面
    
#     chat["msgs"].append({"role": "user", "content": user_msg})
#     chat["msgs"].append({"role": "bot", "content": bot_msg})
    
#     save_users(users)
def save_chat_record(username, chat_id, user_msg, bot_msg):
    # users = load_users()
    # if username not in users: return
    
    # history = users[username]["history"]
    # chat = next((c for c in history if c["id"] == chat_id), None)
    
    # if not chat:
    #     # 初始化新对话结构，增加 summary 和 summary_idx
    #     chat = {
    #         "id": chat_id, 
    #         "title": user_msg[:10]+"...", 
    #         "msgs": [],
    #         "summary": "",          # 长期记忆摘要
    #         "summary_idx": 0        # 指针：msgs中已经被摘要处理过的索引位置
    #     }
    #     history.insert(0, chat)
    
    # # 确保旧数据也有这俩字段 (兼容性处理)
    # if "summary" not in chat: chat["summary"] = ""
    # if "summary_idx" not in chat: chat["summary_idx"] = 0

    # chat["msgs"].append({"role": "user", "content": user_msg})
    # chat["msgs"].append({"role": "bot", "content": bot_msg})
    
    # save_users(users)
    with file_lock: # 【1】锁住整个"读取-修改-保存"的过程
        users = load_users() # 【2】调用内部也加锁的函数，RLock允许这样做
        if username not in users: return
        
        history = users[username]["history"]
        chat = next((c for c in history if c["id"] == chat_id), None)
        
        if not chat:
            chat = {
                "id": chat_id, 
                "title": user_msg[:10]+"...", 
                "msgs": [],
                "summary": "",
                "summary_idx": 0
            }
            history.insert(0, chat)
        
        # 兼容字段
        if "summary" not in chat: chat["summary"] = ""
        if "summary_idx" not in chat: chat["summary_idx"] = 0
        
        chat["msgs"].append({"role": "user", "content": user_msg})
        chat["msgs"].append({"role": "bot", "content": bot_msg})
        
        save_users(users) # 【3】保存，RLock允许再次加锁


def create_new_chat(username):
    users = load_users()
    import time
    chat_id = str(int(time.time()))
    # 仅返回ID，实际存储在发第一条消息时进行
    return chat_id

def delete_chat(username, chat_id):
    with file_lock:
        users = load_users()
        if username in users:
            users[username]["history"] = [c for c in users[username]["history"] if c["id"] != chat_id]
            save_users(users)


# --- 新增：获取用于推理的上下文数据 ---
def get_chat_context_data(username, chat_id):
    """返回：全量消息列表, 当前摘要, 已摘要的索引"""
    # users = load_users()
    # history = users.get(username, {}).get("history", [])
    # chat = next((c for c in history if c["id"] == chat_id), None)
    
    # if not chat:
    #     return [], "", 0
    
    # # 兼容旧数据
    # return chat.get("msgs", []), chat.get("summary", ""), chat.get("summary_idx", 0)
    with file_lock:
        users = load_users()
        history = users.get(username, {}).get("history", [])
        chat = next((c for c in history if c["id"] == chat_id), None)
        if not chat:
            return [], "", 0
        return chat.get("msgs", []), chat.get("summary", ""), chat.get("summary_idx", 0)

# --- 新增：更新摘要 ---
def update_chat_summary_data(username, chat_id, new_summary, new_idx):
    # users = load_users()
    # history = users.get(username, {}).get("history", [])
    # chat = next((c for c in history if c["id"] == chat_id), None)
    # if chat:
    #     chat["summary"] = new_summary
    #     chat["summary_idx"] = new_idx
    #     save_users(users)
    with file_lock: # 锁住
        users = load_users()
        history = users.get(username, {}).get("history", [])
        chat = next((c for c in history if c["id"] == chat_id), None)
        if chat:
            chat["summary"] = new_summary
            chat["summary_idx"] = new_idx
            save_users(users)
        else:
            print(f"Warning: Chat {chat_id} not found during summary update.")