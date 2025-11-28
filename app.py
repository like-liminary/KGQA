######
# Flask 主程序 (后端API + 页面路由)
######

# app.py
import threading # 引入线程模块
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from auth_service import register_user, login_user, get_user_history, save_chat_record, delete_chat
from kg_engine import KGEngine
import secrets
from auth_service import (
    register_user, login_user, get_user_history, save_chat_record, delete_chat,
    get_chat_context_data, update_chat_summary_data # 导入新函数
)

from flask import request
from file_manager import (
    add_file_record, update_file_status, extract_text_from_file, 
    load_file_records, delete_file_record
)

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

kg_engine = KGEngine()


# 定义后台任务函数
def background_summary_task(username, chat_id, msgs, current_summary, current_idx):
    """后台运行：调用LLM生成摘要并保存"""
    try:
        # 调用 kg_engine 的逻辑生成新摘要
        new_summary, new_idx = kg_engine.update_memory(msgs, current_summary, current_idx)
        
        # 如果有变化，写入数据库
        if new_idx != current_idx:
            update_chat_summary_data(username, chat_id, new_summary, new_idx)
            print(f"Background task: Summary updated for chat {chat_id}")
    except Exception as e:
        print(f"Background task error: {e}")

@app.route('/')
def index():
    if 'username' not in session:
        return redirect(url_for('login'))
    return redirect(url_for('chat'))

# --- 认证路由 ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.json
        success, msg = login_user(data.get('username'), data.get('password'))
        if success:
            session['username'] = data.get('username')
            return jsonify({"status": "success"})
        return jsonify({"status": "fail", "msg": msg})
    return render_template('login.html')

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    success, msg = register_user(data.get('username'), data.get('password'))
    return jsonify({"status": "success" if success else "fail", "msg": msg})

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

# --- 问答路由 ---
@app.route('/chat')
def chat():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('chat.html', username=session['username'])

@app.route('/api/history')
def api_history():
    if 'username' not in session: return jsonify([])
    return jsonify(get_user_history(session['username']))

@app.route('/api/ask', methods=['POST'])
def api_ask():
    if 'username' not in session: return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    question = data.get('message')
    chat_id = data.get('chat_id')
    use_context = data.get('use_context', False) # 获取前端开关状态
    use_web = data.get('use_web', False) # 新增获取 web 开关
    username = session['username']

    history_info = "无"

    # # 1. 获取历史数据 (全量消息, 摘要, 指针)
    # msgs, summary, idx = get_chat_context_data(username, chat_id)
    
    # # 2. 尝试更新摘要 (为了不阻塞用户，这一步其实可以异步做，或者每隔几次对话做一次)
    # # 这里为了逻辑严谨，每次都检查一下
    # # 注意：我们传入的是当前的 msgs。
    # new_summary, new_idx = kg_engine.update_memory(msgs, summary, idx)
    
    # # 如果发生了变化，保存回数据库
    # if new_idx != idx:
    #     update_chat_summary_data(username, chat_id, new_summary, new_idx)
    #     summary = new_summary # 更新当前变量用于下一步
    
    # # 3. 从（摘要 + 最近消息）中提取对本次问题有用的信息
    # # 注意：此时 msgs 还没包含用户刚才发的这条 question，这是对的，
    # # 我们要看“过去”对“现在”的影响。
    # history_info = kg_engine.analyze_history_context(question, msgs, summary)
    if use_context:
        # 1. 获取历史数据
        msgs, summary, idx = get_chat_context_data(username, chat_id)
        
        # 2. 异步触发摘要更新 (Fire and Forget)
        # 开启一个新线程去跑 update_memory，不阻塞当前请求
        task_thread = threading.Thread(
            target=background_summary_task,
            args=(username, chat_id, msgs, summary, idx)
        )
        task_thread.start()
        
        # 3. 提取上下文 (这一步必须同步，因为回答需要用到)
        history_info = kg_engine.analyze_history_context(question, msgs, summary)
        print(f"History info app: {history_info}")

    # 调用核心逻辑
    answer = kg_engine.qa_pipeline(question, history_info, use_web=use_web)
    
    # 保存记录
    save_chat_record(username, chat_id, question, answer)
    
    return jsonify({"answer": answer})

# --- 新增：图谱页面路由 ---
@app.route('/graph')
def graph_page():
    if 'username' not in session: return redirect(url_for('login'))
    return render_template('graph.html')

@app.route('/api/graph_data')
def api_graph_data():
    if 'username' not in session: return jsonify({"error": "Unauthorized"}), 401
    data = kg_engine.get_graph_data(limit=300) # 可调整限制
    return jsonify(data)

@app.route('/api/delete_chat', methods=['POST'])
def api_delete_chat():
    if 'username' not in session: return jsonify({}), 401
    chat_id = request.json.get('chat_id')
    delete_chat(session['username'], chat_id)
    return jsonify({"status": "success"})

# --- 新增：文件上传 API ---
@app.route('/api/upload', methods=['POST'])
def upload_file():
    if 'username' not in session: return jsonify({"error": "Unauthorized"}), 401
    
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    # 1. 记录文件状态
    file_id = add_file_record(file.filename)
    
    # 2. 提取文本
    text = extract_text_from_file(file, file_id)
    
    if text:
        # 3. 启动后台处理线程 (抽取+入库)
        task_thread = threading.Thread(
            target=kg_engine.process_import_task,
            args=(file_id, text, file.filename)
        )
        task_thread.start()
        return jsonify({"status": "success", "msg": "上传成功，正在后台处理..."})
    else:
        update_file_status(file_id, "error")
        return jsonify({"error": "文件解析失败"}), 500

# --- 新增：获取文件列表 API ---
@app.route('/api/files', methods=['GET'])
def list_files():
    if 'username' not in session: return jsonify([]), 401
    records = load_file_records()
    # 倒序排列，新的在上面
    return jsonify(records[::-1])

# --- 新增：删除文件 API ---
@app.route('/api/files/delete', methods=['POST'])
def delete_file():
    if 'username' not in session: return jsonify({"error": "Unauthorized"}), 401
    file_id = request.json.get('file_id')
    
    # 1. 删除记录和物理文件
    filename = delete_file_record(file_id)
    
    if filename:
        # 2. 删除图谱中的数据
        kg_engine.delete_file_knowledge(filename)
        return jsonify({"status": "success"})
    return jsonify({"error": "File not found"}), 404

if __name__ == '__main__':
    app.run(debug=True, port=5000)