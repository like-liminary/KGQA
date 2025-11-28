######
# 核心逻辑：实体提取、链接、多跳推理
######

# kg_engine.py
from neo4j import GraphDatabase
from config import Config
from llm_client import call_llm, parse_json_from_llm
from prompts import PROMPTS
import concurrent.futures
from ddgs import DDGS # 引入搜索库
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

VALID_SCHEMA = {
    ("故障现象", "发生于", "设备"),
    ("故障现象", "发生于", "部件"),
    ("故障现象", "现象表征", "缺陷类型"),
    ("缺陷类型", "引起原因", "故障原因"),
    ("缺陷类型", "导致", "故障现象"),
    ("设备", "位于", "位置"),
    ("设备", "组成部分", "部件"),
    ("人员", "采取", "处理措施"),
    ("班组", "采取", "处理措施")
}

class KGEngine:
    def __init__(self):
        self.driver = GraphDatabase.driver(
            Config.NEO4J_URI, 
            auth=(Config.NEO4J_USER, Config.NEO4J_PASSWORD)
        )

    def close(self):
        self.driver.close()

    # --- 步骤 1: 实体提取 ---
    def extract_entities(self, question):
        prompt = PROMPTS["entity_extraction"].format(question=question)
        # 温度稍低，保证格式
        resp = call_llm(prompt, temperature=0.1)
        entities = parse_json_from_llm(resp)
        return entities if entities else []

    # --- 步骤 2: 实体链接 ---
    def _get_candidates(self, name, label):
        # """从Neo4j获取候选实体 (模糊匹配)"""
        # query = f"""
        # MATCH (n:`{label}`)
        # WHERE n.name CONTAINS $name
        # RETURN id(n) as id, n.name as name
        # LIMIT 10
        # """
        # with self.driver.session() as session:
        #     result = session.run(query, name=name)
        #     return [{"id": r["id"], "name": r["name"], "label": label} for r in result]
        query = f"""
        MATCH (n:`{label}`)
        WHERE n.name CONTAINS $name
        RETURN elementId(n) AS id, n.name AS name
        LIMIT 10
        """
        with self.driver.session() as session:
            result = session.run(query, name=name)
            return [{"id": r["id"], "name": r["name"], "label": label} for r in result]

    def _score_candidates_batch(self, question, pred_entity, candidates):
        """调用LLM给一批候选实体打分"""
        if not candidates: return []
        
        candidates_str = "\n".join([f"ID: {c['id']}, Name: {c['name']}" for c in candidates])
        prompt = PROMPTS["entity_linking"].format(
            question=question,
            predicted_entity=pred_entity,
            candidates_str=candidates_str
        )
        resp = call_llm(prompt, temperature=0.1)
        scores = parse_json_from_llm(resp)
        
        # 合并分数到候选对象中
        final_candidates = []
        if isinstance(scores, list):
            score_map = {item['id']: item.get('score', 0) for item in scores}
            for cand in candidates:
                cand['score'] = score_map.get(cand['id'], 0)
                final_candidates.append(cand)
        return final_candidates

    def link_entities(self, question, predicted_entities):
        all_candidates = []
        
        # 1. 检索候选实体
        for item in predicted_entities:
            name = item['entity']
            for label in item['types']:
                candidates = self._get_candidates(name, label)
                # 标记属于哪个预测实体，方便后续处理
                for c in candidates:
                    c['pred_name'] = name
                all_candidates.extend(candidates)

        # 去重
        unique_candidates = {c['id']: c for c in all_candidates}.values()
        
        # 2. 多线程评分 (分批，每批5个)
        batch_size = 5
        batches = []
        temp_batch = []
        
        # 简单的按预测实体分组处理会更准确，这里简化为直接列表处理
        # 实际逻辑：每次给LLM 1个预测实体 + 5个候选。
        # 下面逻辑进行了适配：
        
        futures = []
        linked_results = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            for item in predicted_entities:
                pred_name = item['entity']
                # 筛选出当前预测实体的所有候选
                curr_candidates = [c for c in unique_candidates if c['pred_name'] == pred_name]
                
                # 分批提交
                for i in range(0, len(curr_candidates), batch_size):
                    batch = curr_candidates[i:i+batch_size]
                    futures.append(executor.submit(self._score_candidates_batch, question, pred_name, batch))

            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                if res:
                    linked_results.extend(res)

        # 排序并取Top 2
        linked_results.sort(key=lambda x: x['score'], reverse=True)
        
        # 过滤掉分数太低的 (例如 < 0.5)
        valid_results = [r for r in linked_results if r['score'] > 0.5]
        
        return valid_results[:2] # 返回 top 2

    # --- 步骤 3: 关系路径建模 (多跳) ---
    def _get_one_hop_triplets(self, node_id):
        # query = """
        # MATCH (n)-[r]->(m)
        # WHERE id(n) = $id
        # RETURN id(r) as rel_id, type(r) as rel_type, id(m) as target_id, m.name as target_name, labels(m) as target_labels
        # LIMIT 20
        # """
        query = """
        MATCH (n)-[r]->(m)
        WHERE elementId(n) = $id
        RETURN 
            elementId(r) AS rel_id, 
            type(r) AS rel_type, 
            elementId(m) AS target_id, 
            m.name AS target_name, 
            labels(m) AS target_labels
        LIMIT 20
        """
        with self.driver.session() as session:
            res = session.run(query, id=node_id)
            triplets = []
            for r in res:
                # 格式化为易读字符串
                target_label = r['target_labels'][0] if r['target_labels'] else "未知"
                t_str = f"--[{r['rel_type']}]--> {r['target_name']} ({target_label})"
                triplets.append({
                    "id": str(r['rel_id']), 
                    "text": t_str,
                    "target_id": r['target_id'],
                    "target_name": r['target_name'],
                    "full_triplet": t_str
                })
            return triplets

    def _search_subgraph(self, node, question, history_list, depth, max_depth=3):
        """
        递归+分支搜索函数
        :param node: 当前节点对象 {id, name}
        :param history_list: 历史路径列表 [str, str]
        :param depth: 当前深度
        :return: 收集到的有用路径字符串列表
        """
        # 1. 递归终止条件
        if depth >= max_depth:
            return []

        current_id = node['id']
        current_name = node['name']
        
        # 2. 获取候选三元组
        triplets = self._get_one_hop_triplets(current_id)
        if not triplets:
            return []
            
        triplets_map = {t['id']: t for t in triplets}
        triplets_str = "\n".join([f"ID: {t['id']} | {t['text']}" for t in triplets])
        history_context = " -> ".join(history_list) if history_list else "无"

        # 3. 调用LLM进行多路选择
        prompt = PROMPTS["path_selection"].format(
            question=question,
            current_node_name=current_name,
            history_context=history_context,
            triplets_str=triplets_str
        )
        
        # 温度稍低以保证格式，但不需要0，给予一定灵活性
        resp = call_llm(prompt, temperature=0.2)
        selections = parse_json_from_llm(resp)
        
        if not selections or not isinstance(selections, list):
            return []

        collected_paths = []
        futures = []
        
        # 使用线程池处理分支（为了避免递归中频繁创建线程池，建议在外部传入Executor，
        # 但为了代码结构简单，这里在每一层使用简单的处理，或者复用全局逻辑。
        # 考虑到层数少（3层），直接串行+顶层并发 或者是 在这里开启并发都可以。
        # 这里选择：当前层的多个分支，并行去跑下一层。）
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            for item in selections:
                t_id = str(item.get('id'))
                should_continue = item.get('continue_search', False)
                
                if t_id not in triplets_map:
                    continue
                
                selected_t = triplets_map[t_id]
                
                # 构建当前这一步的完整路径文本
                # 格式： ... -> [关系] -> 实体
                current_path_segment = selected_t['text']
                new_history = history_list + [current_path_segment]
                
                # 将当前找到的有价值的三元组存入结果
                # 这里我们保存完整的“从起点到当前的路径”作为一条知识
                full_path_str = f"路径: {node.get('root_name', current_name)} " + " ".join(new_history)
                collected_paths.append(full_path_str)
                
                # 如果大模型觉得需要继续，并且没到最大深度，则递归
                if should_continue:
                    next_node = {
                        'id': selected_t['target_id'], 
                        'name': selected_t['target_name'],
                        'root_name': node.get('root_name', current_name) # 保持根节点名称用于记录
                    }
                    # 提交递归任务到线程池
                    futures.append(executor.submit(
                        self._search_subgraph, 
                        next_node, 
                        question, 
                        new_history, 
                        depth + 1, 
                        max_depth
                    ))
            
            # 收集子分支的结果
            for future in concurrent.futures.as_completed(futures):
                try:
                    sub_results = future.result()
                    if sub_results:
                        collected_paths.extend(sub_results)
                except Exception as e:
                    print(f"Error in subgraph search: {e}")

        return collected_paths
    # def _reason_path(self, start_node, question):
    #     """单条线路的递归/循环推理"""
    #     current_id = start_node['id']
    #     current_name = start_node['name']
    #     path_record = [] # 记录找到的三元组
        
    #     for hop in range(3): # 最多3跳
    #         # 获取一跳三元组
    #         triplets = self._get_one_hop_triplets(current_id)
    #         if not triplets:
    #             break

    #         triplets_map = {t['id']: t for t in triplets}
    #         triplets_str = "\n".join([f"ID: {t['id']} | {t['text']}" for t in triplets])
            
    #         history_context = " -> ".join([p['text'] for p in path_record]) if path_record else "无"

    #         # 调用LLM选择
    #         prompt = PROMPTS["path_selection"].format(
    #             question=question,
    #             current_node_name=current_name,
    #             history_context=history_context,
    #             triplets_str=triplets_str
    #         )
            
    #         resp = call_llm(prompt, temperature=0.2)
    #         decision = parse_json_from_llm(resp)
            
    #         if not decision or not decision.get('selected_triplet_id') or decision['selected_triplet_id'] == "null":
    #             break # 无有用信息，停止
            
    #         sel_id = str(decision['selected_triplet_id'])
    #         if sel_id not in triplets_map:
    #             break
                
    #         selected_t = triplets_map[sel_id]
    #         path_record.append(selected_t)
            
    #         if not decision.get('continue_search', False):
    #             break # LLM认为不需要继续了
            
    #         # 更新当前节点，继续下一跳
    #         current_id = selected_t['target_id']
    #         # 更新名称用于下轮prompt (简单处理，实际可能需再查库)
    #         current_name = selected_t['target_name'] if 'target_name' in selected_t else "Unknown"

    #     return path_record

    # def perform_reasoning(self, question, start_nodes):
    #     """多线程对两个实体进行路径建模"""
    #     final_paths = []
    #     with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
    #         futures = {executor.submit(self._reason_path, node, question): node for node in start_nodes}
    #         for future in concurrent.futures.as_completed(futures):
    #             path = future.result()
    #             if path:
    #                 start_node_name = futures[future]['name']
    #                 # 格式化路径为字符串
    #                 path_str = f"从实体 [{start_node_name}] 出发: " + " ".join([t['text'] for t in path])
    #                 final_paths.append(path_str)
    #     return final_paths

    # --- 新增：生成/更新摘要 ---
    def update_memory(self, msgs, current_summary, summarized_idx):
        """
        检查是否需要更新摘要。
        策略：保留最后6条作为短期记忆。如果未摘要的消息超过6条，则将(未摘要 - 6)的部分压缩进摘要。
        """
        total_count = len(msgs)
        # 保留最近6条不摘要，直接作为短期上下文
        keep_recent = 6
        
        # 计算需要被摘要的终点索引
        # 例如：总共15条，idx=0。end_idx = 15 - 6 = 9。即 msg[0:9] 需要变为摘要。
        target_idx = total_count - keep_recent
        
        if target_idx <= summarized_idx:
            return current_summary, summarized_idx # 不需要更新

        # 提取需要压缩的新片段
        to_be_summarized = msgs[summarized_idx : target_idx]
        if not to_be_summarized:
            return current_summary, summarized_idx

        # 拼接对话文本
        new_lines = ""
        for m in to_be_summarized:
            role = "用户" if m['role'] == 'user' else "助手"
            new_lines += f"{role}: {m['content']}\n"

        print(f"Running summarization for {len(to_be_summarized)} messages...")
        prompt = PROMPTS["summarize_history"].format(
            old_summary=current_summary,
            new_lines=new_lines
        )
        
        new_summary = call_llm(prompt, temperature=0.3)
        # 简单的容错
        if not new_summary: new_summary = current_summary
        
        return new_summary, target_idx

    # --- 新增：分析历史上下文 ---
    def analyze_history_context(self, question, msgs, summary):
        """
        结合 摘要 + 最近6条消息，提取对当前问题有用的信息
        """
        # 获取最近 6 条
        recent_msgs = msgs[-6:] if msgs else []
        recent_str = ""
        for m in recent_msgs:
            role = "用户" if m['role'] == 'user' else "助手"
            recent_str += f"{role}: {m['content']}\n"

        # 如果完全没有历史，直接返回空
        if not summary and not recent_str:
            return "无"

        prompt = PROMPTS["extract_history_info"].format(
            summary=summary,
            recent_history=recent_str,
            question=question
        )
        print(f"Analyzing history context...{prompt}")
        
        # 温度低一点，确保提取准确
        history_info = call_llm(prompt, temperature=0.4)
        return history_info

    def search_web(self, question):
        print(f"Searching web for: {question}")
        try:
            results_text = ""
            # 使用 DuckDuckGo 搜索前10条
            with DDGS() as ddgs:
                results = [r for r in ddgs.text(question, max_results=10)]
                
            if not results:
                return "无联网搜索结果"

            # 拼接结果给大模型筛选
            for i, res in enumerate(results):
                results_text += f"[{i+1}] Title: {res['title']}\nSnippet: {res['body']}\n\n"
            
            # 调用 LLM 提取有用信息
            prompt = PROMPTS["extract_search_info"].format(
                question=question,
                search_results=results_text
            )
            # 温度低一点，确保准确提取
            return call_llm(prompt, temperature=0.2)

        except Exception as e:
            print(f"Web search error: {e}")
            return "联网搜索服务暂时不可用"

    # --- 新增：获取图谱可视化数据 ---
    def get_graph_data(self, limit=300):
        """
        获取用于前端展示的节点和关系。
        为了防止浏览器卡顿，默认限制返回300个节点。
        """
        # 查询所有节点和关系
        query = f"""
        MATCH (n)-[r]->(m)
        RETURN n, r, m
        LIMIT {limit}
        """
        nodes = {}
        edges = []
        
        with self.driver.session() as session:
            result = session.run(query)
            for record in result:
                n = record['n']
                m = record['m']
                r = record['r']
                
                # 处理节点 (去重)
                # Neo4j 的 labels 是个列表，我们取第一个作为分组依据
                n_id = str(n.element_id) if hasattr(n, 'element_id') else str(n.id)
                m_id = str(m.element_id) if hasattr(m, 'element_id') else str(m.id)
                
                if n_id not in nodes:
                    nodes[n_id] = {
                        "id": n_id, 
                        "label": n.get("name", "Unknown"), 
                        "group": list(n.labels)[0] if n.labels else "Other"
                    }
                
                if m_id not in nodes:
                    nodes[m_id] = {
                        "id": m_id, 
                        "label": m.get("name", "Unknown"), 
                        "group": list(m.labels)[0] if m.labels else "Other"
                    }
                
                # 处理关系
                edges.append({
                    "from": n_id,
                    "to": m_id,
                    "label": r.type,
                    "arrows": "to"
                })
        
        return {"nodes": list(nodes.values()), "edges": edges}

    def perform_reasoning(self, question, start_nodes):
        """
        对多个起始实体进行广度优先推理
        """
        final_knowledge = []
        
        # 对每一个起始实体，启动一个搜索过程
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(start_nodes) + 1) as executor:
            future_to_node = {}
            for node in start_nodes:
                # 标记根节点名称，方便后续拼装路径
                node['root_name'] = node['name'] 
                future = executor.submit(self._search_subgraph, node, question, [], 0, 3)
                future_to_node[future] = node
            
            for future in concurrent.futures.as_completed(future_to_node):
                try:
                    paths = future.result()
                    # 去重
                    for p in paths:
                        if p not in final_knowledge:
                            final_knowledge.append(p)
                except Exception as e:
                    print(f"Reasoning error: {e}")
                    
        return final_knowledge
    
    # --- 新增：处理闲聊/无需检索的情况 ---
    def _handle_chitchat(self, question):
        """处理不需要检索实体的场景（闲聊）"""
        prompt = PROMPTS["direct_chat"].format(question=question)
        # 这里的温度可以稍微高一点，让回答更自然
        return call_llm(prompt, temperature=0.7)

    def process_import_task(self, file_id, text, filename):
        """后台任务：分句 -> 抽取 -> 过滤 -> 导入"""
        try:
            print(f"Start processing file: {filename}")
            # 1. 分句 (按句号、分号、换行符分割)
            sentences = re.split(r'[。；;\n]+', text)
            sentences = [s.strip() for s in sentences if len(s.strip()) > 5] # 过滤太短的
            
            # 2. 多线程抽取
            all_triplets = []
            batch_size = 5 
            
            batches = [" ".join(sentences[i:i+batch_size]) for i in range(0, len(sentences), batch_size)]
            
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = [executor.submit(self._extract_triplets_llm, batch) for batch in batches]
                for future in as_completed(futures):
                    res = future.result()
                    print(f"Batch extraction result: {res}")
                    # --- 【修改点1：增强结果处理的健壮性】 ---
                    if not res: 
                        continue

                    # 情况A: 如果LLM返回的是列表，直接添加
                    if isinstance(res, list):
                        all_triplets.extend(res)
                    
                    # 情况B: 如果LLM返回的是字典 (例如 {"result": [...]})
                    elif isinstance(res, dict):
                        # 尝试找到字典里值是列表的那一项
                        found_list = False
                        for val in res.values():
                            if isinstance(val, list):
                                all_triplets.extend(val)
                                found_list = True
                                break
                        if not found_list:
                            print(f"Warning: LLM returned dict but no list found: {res}")

            # 3. 过滤不符合 Schema 的结果
            print(f'all_triplets: {all_triplets}')
            valid_triplets = []
            for t in all_triplets:
                # --- 【修改点2：防止 t 是字符串导致报错】 ---
                if not isinstance(t, dict):
                    print(f"Warning: Invalid triplet: {t}")
                    continue
                
                # 安全使用 get
                key = (t.get('subject_type'), t.get('predicate'), t.get('object_type'))
                if key in VALID_SCHEMA:
                    valid_triplets.append(t)
            
            print(f"Extracted {len(valid_triplets)} valid triplets from {filename}")

            # 4. 导入 Neo4j
            if valid_triplets:
                self._import_to_neo4j(valid_triplets, filename)

            # 更新状态为完成
            from file_manager import update_file_status
            update_file_status(file_id, "done")
            
        except Exception as e:
            # 打印详细错误堆栈，方便调试
            import traceback
            traceback.print_exc()
            print(f"Import Error: {e}")
            from file_manager import update_file_status
            update_file_status(file_id, "error")

    def _extract_triplets_llm(self, text):
        """调用 LLM 抽取"""
        prompt = PROMPTS["triplet_extraction"].format(text=text)
        # resp = call_llm(prompt, temperature=0.7, json_mode=True) # 强制JSON模式
        resp = call_llm(prompt, temperature=0.7) # 强制JSON模式
        print(f"Triplet extraction raw response: {resp}")
        print("#######################")
        
        pr = parse_json_from_llm(resp)
        print(f"Triplet extraction response: {pr}")
        return parse_json_from_llm(resp)

    def _import_to_neo4j(self, triplets, filename):
        """
        批量写入 Neo4j (无 APOC 版本)。
        使用 Python 字符串拼接来处理动态 Label 和 Relationship Type。
        """
        print(f'准备插入，文件名为：{filename}')
        with self.driver.session() as session:
            for t in triplets:
                # 1. 获取动态的 Label 和 Type 字符串
                # 为了安全起见，虽然经过了过滤，建议还是加上反引号 `` 包裹，防止特殊字符报错
                head_label = t['subject_type']
                tail_label = t['object_type']
                relation_type = t['predicate']
                
                # 2. 构造 Cypher 语句
                # 注意：这里使用了 Python 的 f-string 将 label 直接嵌入 SQL 字符串中
                # 属性值 (name, filename) 依然使用 Neo4j 的参数化 ($param) 传递，确保数据安全
                # query = f"""
                # MERGE (h:`{head_label}` {{name: $head_name}})
                # MERGE (t:`{tail_label}` {{name: $tail_name}})
                # MERGE (h)-[r:`{relation_type}`]->(t)
                # SET r.source_file = $filename
                # """
                query = f"""
                MERGE (h:`{head_label}` {{name: $head_name}})
                MERGE (t:`{tail_label}` {{name: $tail_name}})
                MERGE (h)-[r:`{relation_type}`]->(t)
                
                ON CREATE SET r.sources = [$filename]
                ON MATCH SET r.sources = 
                    CASE 
                        WHEN r.sources IS NULL THEN [$filename]
                        WHEN NOT $filename IN r.sources THEN r.sources + $filename
                        ELSE r.sources
                    END
                """
                
                # 3. 执行查询
                try:
                    session.run(
                        query, 
                        head_name=t['subject_type'], 
                        tail_name=t['object_type'], 
                        filename=filename
                    )
                    print(f"插入成功，文件名: {filename}")
                except Exception as e:
                    print(f"Error inserting triplet {t}: {e}")

    def delete_file_knowledge(self, filename):
        """
        删除指定文件的知识。
        逻辑：
        1. 从关系的 sources 列表中移除该文件名。
        2. 如果移除后 sources 列表为空，说明没有任何文件引用该关系，则物理删除关系。
        3. 删除孤立节点。
        """
        # 第一步：从列表中移除文件名
        query_remove_source = """
        MATCH ()-[r]->()
        WHERE $filename IN r.sources
        SET r.sources = [x IN r.sources WHERE x <> $filename]
        """
        
        # 第二步：删除 sources 列表变为空的关系
        # (size(r.sources) = 0 表示没有任何文件还要保留这个关系了)
        query_del_rel = """
        MATCH ()-[r]->()
        WHERE size(r.sources) = 0
        DELETE r
        """
        
        # 第三步：删除孤立节点 (和之前一样)
        query_del_node = """
        MATCH (n)
        WHERE NOT (n)--()
        DELETE n
        """
        
        with self.driver.session() as session:
            # 1. 移除引用
            session.run(query_remove_source, filename=filename)
            # 2. 物理删除关系
            session.run(query_del_rel)
            # 3. 物理删除节点
            session.run(query_del_node)
            
        print(f"Deleted knowledge reference for file: {filename}")


    # --- 主流程 ---
    def qa_pipeline(self, question, history_info="无", use_web=False):

        # 1. 启动联网搜索 (多线程优化：可以和 KG 检索并行，这里为了简单先串行)
        web_info = "未启用联网搜索"
        if use_web:
            web_info = self.search_web(question)

        print(f"Context Info from History: {history_info}")
        # 1. 提取
        print("Step 1: Extraction & Analysis...")
        predicted = self.extract_entities(question)
        
        if not predicted or len(predicted) == 0:
            print("Chat mode detected.")
            full_prompt = f"【历史聊天记录】: {history_info}\n【联网信息】: {web_info}\n用户问题: {question}"
            return self._handle_chitchat(full_prompt)

        # 2. 链接
        print("Step 2: Linking...")
        real_entities = self.link_entities(question, predicted)
        
        if not real_entities:
            return self.generate_final_answer(question, "", history_info, web_info) 
        
        print(f"Linked Entities: {[e['name'] for e in real_entities]}")

        # 3. 推理 (现在返回的是所有收集到的路径列表)
        print("Step 3: Reasoning (BFS+DFS)...")
        paths = self.perform_reasoning(question, real_entities)
        
        # 将路径列表去重并合并
        unique_paths = list(set(paths))
        kg_context = "\n".join(unique_paths)
        
        print(f"Retrieved {len(unique_paths)} paths.")
        
        # 4. 回答
        print("Step 4: Answering...")
        return self.generate_final_answer(question, kg_context, history_info, web_info)

    # --- 修改：generate_final_answer ---
    def generate_final_answer(self, question, kg_context, history_info="无", web_info="无"):
        prompt = PROMPTS["final_answer"].format(
            question=question, 
            kg_context=kg_context,
            history_info=history_info,
            web_info=web_info
        )
        return call_llm(prompt, temperature=0.5)