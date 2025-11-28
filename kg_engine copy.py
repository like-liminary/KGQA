######
# 核心逻辑：实体提取、链接、多跳推理
######

# kg_engine.py
from neo4j import GraphDatabase
from config import Config
from llm_client import call_llm, parse_json_from_llm
from prompts import PROMPTS
import concurrent.futures

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
        """从Neo4j获取候选实体 (模糊匹配)"""
        query = f"""
        MATCH (n:`{label}`)
        WHERE n.name CONTAINS $name
        RETURN id(n) as id, n.name as name
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
        query = """
        MATCH (n)-[r]->(m)
        WHERE id(n) = $id
        RETURN id(r) as rel_id, type(r) as rel_type, id(m) as target_id, m.name as target_name, labels(m) as target_labels
        LIMIT 20
        """
        with self.driver.session() as session:
            res = session.run(query, id=node_id)
            triplets = []
            for r in res:
                # 格式化为易读字符串
                t_str = f"--[{r['rel_type']}]--> {r['target_name']} ({r['target_labels'][0]})"
                triplets.append({
                    "id": str(r['rel_id']), 
                    "text": t_str,
                    "target_id": r['target_id'],
                    "full_triplet": t_str
                })
            return triplets

    def _reason_path(self, start_node, question):
        """单条线路的递归/循环推理"""
        current_id = start_node['id']
        current_name = start_node['name']
        path_record = [] # 记录找到的三元组
        
        for hop in range(3): # 最多3跳
            # 获取一跳三元组
            triplets = self._get_one_hop_triplets(current_id)
            if not triplets:
                break

            triplets_map = {t['id']: t for t in triplets}
            triplets_str = "\n".join([f"ID: {t['id']} | {t['text']}" for t in triplets])
            
            history_context = " -> ".join([p['text'] for p in path_record]) if path_record else "无"

            # 调用LLM选择
            prompt = PROMPTS["path_selection"].format(
                question=question,
                current_node_name=current_name,
                history_context=history_context,
                triplets_str=triplets_str
            )
            
            resp = call_llm(prompt, temperature=0.2)
            decision = parse_json_from_llm(resp)
            
            if not decision or not decision.get('selected_triplet_id') or decision['selected_triplet_id'] == "null":
                break # 无有用信息，停止
            
            sel_id = str(decision['selected_triplet_id'])
            if sel_id not in triplets_map:
                break
                
            selected_t = triplets_map[sel_id]
            path_record.append(selected_t)
            
            if not decision.get('continue_search', False):
                break # LLM认为不需要继续了
            
            # 更新当前节点，继续下一跳
            current_id = selected_t['target_id']
            # 更新名称用于下轮prompt (简单处理，实际可能需再查库)
            current_name = selected_t['target_name'] if 'target_name' in selected_t else "Unknown"

        return path_record

    def perform_reasoning(self, question, start_nodes):
        """多线程对两个实体进行路径建模"""
        final_paths = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(self._reason_path, node, question): node for node in start_nodes}
            for future in concurrent.futures.as_completed(futures):
                path = future.result()
                if path:
                    start_node_name = futures[future]['name']
                    # 格式化路径为字符串
                    path_str = f"从实体 [{start_node_name}] 出发: " + " ".join([t['text'] for t in path])
                    final_paths.append(path_str)
        return final_paths
    
    # --- 新增：处理闲聊/无需检索的情况 ---
    def _handle_chitchat(self, question):
        """处理不需要检索实体的场景（闲聊）"""
        prompt = PROMPTS["direct_chat"].format(question=question)
        # 这里的温度可以稍微高一点，让回答更自然
        return call_llm(prompt, temperature=0.7)

    # --- 主流程 ---
    def qa_pipeline(self, question):
        # 1. 提取
        print("Step 1: Extraction...")
        predicted = self.extract_entities(question)
        # --- 修改点开始：检测是否需要检索 ---
        # 如果提取结果为空列表，说明大模型认为不需要检索实体
        if not predicted or len(predicted) == 0:
            print("Chat mode detected (No entities to retrieve).")
            return self._handle_chitchat(question)
        # --- 修改点结束 ---

        # 2. 链接
        print("Step 2: Linking...")
        real_entities = self.link_entities(question, predicted)
        if not real_entities:
            return self.generate_final_answer(question, "") # 返回空，触发LLM自有知识
        
        print(f"Linked Entities: {[e['name'] for e in real_entities]}")

        # 3. 推理
        print("Step 3: Reasoning...")
        paths = self.perform_reasoning(question, real_entities)
        kg_context = "\n".join(paths)
        
        # 4. 回答
        print("Step 4: Answering...")
        return self.generate_final_answer(question, kg_context)

    def generate_final_answer(self, question, kg_context):
        prompt = PROMPTS["final_answer"].format(question=question, kg_context=kg_context)
        return call_llm(prompt, temperature=0.5) # 温度稍高，回答更自然