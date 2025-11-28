# file_manager.py
import os
import json
import uuid
import threading
from docx import Document
from pypdf import PdfReader
from werkzeug.utils import secure_filename

UPLOAD_FOLDER = 'uploads'
FILES_DB = 'files.json'
file_lock = threading.Lock()

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# --- 状态管理 ---
def load_file_records():
    with file_lock:
        if not os.path.exists(FILES_DB): return []
        try:
            with open(FILES_DB, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: return []

def save_file_records(records):
    with file_lock:
        with open(FILES_DB, 'w', encoding='utf-8') as f:
            json.dump(records, f, ensure_ascii=False, indent=4)

def add_file_record(filename):
    records = load_file_records()
    file_id = str(uuid.uuid4())
    record = {
        "id": file_id,
        "name": filename,
        "status": "processing", # uploading, processing, done, error
        "path": "" 
    }
    records.append(record)
    save_file_records(records)
    return file_id

def update_file_status(file_id, status):
    records = load_file_records()
    for r in records:
        if r['id'] == file_id:
            r['status'] = status
            break
    save_file_records(records)

def delete_file_record(file_id):
    records = load_file_records()
    target = next((r for r in records if r['id'] == file_id), None)
    if target:
        # 删除物理文件
        if target['path'] and os.path.exists(target['path']):
            try:
                os.remove(target['path'])
            except: pass
        
        # 更新记录
        new_records = [r for r in records if r['id'] != file_id]
        save_file_records(new_records)
        return target['name'] # 返回原始文件名用于删除图谱数据
    return None

# --- 文件转换逻辑 ---
def extract_text_from_file(file_storage, file_id):
    """保存文件并提取文本内容"""
    # 获取原始扩展名
    ext = os.path.splitext(file_storage.filename)[1].lower()
    # 使用ID作为物理文件名，避免中文乱码问题
    save_name = f"{file_id}{ext}"
    save_path = os.path.join(UPLOAD_FOLDER, save_name)
    
    file_storage.save(save_path)
    
    # 更新记录中的路径
    records = load_file_records()
    for r in records:
        if r['id'] == file_id:
            r['path'] = save_path
            break
    save_file_records(records)

    text = ""
    try:
        if ext == '.txt':
            with open(save_path, 'r', encoding='utf-8') as f:
                text = f.read()
        elif ext == '.docx':
            doc = Document(save_path)
            text = "\n".join([para.text for para in doc.paragraphs])
        elif ext == '.pdf':
            reader = PdfReader(save_path)
            for page in reader.pages:
                text += page.extract_text() + "\n"
    except Exception as e:
        print(f"File parse error: {e}")
        return None
        
    return text