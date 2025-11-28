let currentChatId = null;

document.addEventListener('DOMContentLoaded', () => {
    loadHistory();
    startNewChat(); // 默认进入新对话状态
    
    // 文本框回车发送
    document.getElementById('userJsonInput').addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
});

async function loadHistory() {
    const res = await fetch('/api/history');
    const list = await res.json();
    const container = document.getElementById('historyList');
    container.innerHTML = '';
    
    list.forEach(chat => {
        const div = document.createElement('div');
        div.className = `history-item ${chat.id === currentChatId ? 'active' : ''}`;
        div.innerHTML = `
            <span onclick="loadChat('${chat.id}')">${chat.title}</span>
            <i class="fas fa-trash del-chat" onclick="deleteChat('${chat.id}', event)"></i>
        `;
        container.appendChild(div);
    });
}

function startNewChat() {
    currentChatId = Date.now().toString(); // 临时ID
    document.getElementById('chatContainer').innerHTML = `
        <div class="message bot">
            <div class="avatar"><i class="fas fa-robot"></i></div>
            <div class="text">你好！请问有什么关于设备故障的问题吗？</div>
        </div>
    `;
    loadHistory(); // 刷新侧边栏状态
}

async function loadChat(chatId) {
    currentChatId = chatId;
    const res = await fetch('/api/history');
    const list = await res.json();
    const chat = list.find(c => c.id === chatId);
    
    if (!chat) return;
    
    const container = document.getElementById('chatContainer');
    container.innerHTML = '';
    chat.msgs.forEach(msg => appendMessage(msg.role, msg.content));
    
    loadHistory(); // 更新高亮
}


async function sendMessage() {
    const input = document.getElementById('userJsonInput');
    const msg = input.value.trim();
    // 获取开关状态
    const contextSwitch = document.getElementById('contextSwitch');
    const webSwitch = document.getElementById('webSwitch'); // 新增

    const useContext = contextSwitch.checked;
    const useWeb = webSwitch ? webSwitch.checked : false; // 新增

    if (!msg) return;
    input.value = '';
    input.style.height = 'auto'; // 重置高度
    appendMessage('user', msg);
    const loadingId = 'loading-' + Date.now();
    // 提示信息根据是否联网动态变化
    const statusText = useWeb ? "正在联网搜索并思考..." : "正在检索图谱并思考...";
    // appendMessage('bot', '<i class="fas fa-spinner fa-spin"></i> ${statusText}', loadingId);
    appendMessage('bot', `<i class="fas fa-spinner fa-spin"></i> ${statusText}`, loadingId);
    
    try {
        const res = await fetch('/api/ask', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ 
                message: msg, 
                chat_id: currentChatId,
                use_context: useContext, // 传递开关状态
                use_web: useWeb // 发送 web 参数
            })
        });
        const data = await res.json();
        
        document.getElementById(loadingId).remove();
        appendMessage('bot', data.answer);
        
        loadHistory();
    } catch (e) {
        console.error(e);
        document.getElementById(loadingId).innerHTML = 'Error occurred.';
    }
}
function appendMessage(role, text, id=null) {
    const container = document.getElementById('chatContainer');
    const div = document.createElement('div');
    div.className = `message ${role}`;
    if(id) div.id = id;
    
    const icon = role === 'bot' ? 'fa-robot' : 'fa-user';
    
    // 简单的Markdown转HTML处理 (这里仅处理换行，实际可用marked.js)
    const formattedText = text.replace(/\n/g, '<br>');
    
    div.innerHTML = `
        <div class="avatar"><i class="fas ${icon}"></i></div>
        <div class="text">${formattedText}</div>
    `;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

async function deleteChat(chatId, event) {
    event.stopPropagation();
    if(!confirm('确定删除此对话吗？')) return;
    
    await fetch('/api/delete_chat', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({chat_id: chatId})
    });
    
    if(chatId === currentChatId) startNewChat();
    else loadHistory();
}

document.addEventListener('DOMContentLoaded', () => {
    // ... 原有初始化 ...
    loadFiles(); // 加载文件列表
    
    // 轮询文件状态 (每5秒刷新一次，简单实现)
    setInterval(loadFiles, 5000);
});

function triggerUpload() {
    document.getElementById('fileInput').click();
}

async function handleFileUpload(input) {
    const file = input.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    // 立即在界面显示一个"上传中"的临时状态
    renderTempFile(file.name);

    try {
        const res = await fetch('/api/upload', {
            method: 'POST',
            body: formData
        });
        const data = await res.json();
        if (data.status === 'success') {
            loadFiles(); // 刷新列表
        } else {
            alert("上传失败: " + data.error);
        }
    } catch (e) {
        alert("上传出错");
    }
    input.value = ''; // 重置
}

async function loadFiles() {
    const res = await fetch('/api/files');
    const files = await res.json();
    const container = document.getElementById('fileList');
    container.innerHTML = '';

    files.forEach(f => {
        const div = document.createElement('div');
        div.className = 'file-item';
        
        let statusHtml = '';
        let delHtml = '';

        if (f.status === 'processing') {
            statusHtml = `<span class="status-badge status-processing"><i class="fas fa-spinner fa-spin"></i> 处理中</span>`;
        } else if (f.status === 'done') {
            statusHtml = `<span class="status-badge status-done">完成</span>`;
            delHtml = `<i class="fas fa-trash file-del-btn" onclick="deleteFile('${f.id}')" title="删除"></i>`;
        } else {
            statusHtml = `<span class="status-badge status-error">失败</span>`;
            delHtml = `<i class="fas fa-trash file-del-btn" onclick="deleteFile('${f.id}')" title="删除"></i>`;
        }

        div.innerHTML = `
            <div class="file-info" title="${f.name}">
                <i class="fas fa-file-alt file-icon"></i>
                <span class="file-name">${f.name}</span>
            </div>
            ${statusHtml}
            ${delHtml}
        `;
        container.appendChild(div);
    });
}

function renderTempFile(name) {
    const container = document.getElementById('fileList');
    const div = document.createElement('div');
    div.className = 'file-item';
    div.innerHTML = `
        <div class="file-info">
            <i class="fas fa-file-alt file-icon"></i>
            <span class="file-name">${name}</span>
        </div>
        <span class="status-badge status-processing">上传中...</span>
    `;
    container.prepend(div);
}

async function deleteFile(fileId) {
    if (!confirm("确定删除此文件吗？相关知识图谱数据也将被移除。")) return;
    
    await fetch('/api/files/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({file_id: fileId})
    });
    loadFiles();
}