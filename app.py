# -----------------------------------------------------------------------------
# 聯盟爭霸賽分析 - Flask 後端伺服器 (app.py) - 最終完整版
# -----------------------------------------------------------------------------
# 功能：
# 1. 提供 Web UI 介面 (渲染 index.html)。
# 2. 建立 API 端點，處理上傳的 Excel 檔案並轉換為文字。
# 3. 建立核心 API 端點，執行精確分組演算法並回傳結果。
# -----------------------------------------------------------------------------

from flask import Flask, request, jsonify, render_template
import re
import pandas as pd

# --- 1. 初始化 Flask 應用 ---
app = Flask(__name__)
# 讓回傳的 JSON 直接顯示中文，而不是 ASCII 編碼
app.config['JSON_AS_ASCII'] = False

# --- 2. 核心邏輯函數 ---

def parse_power_data(text_data: str) -> list:
    """
    從字串中解析出 (編號, 戰力) 的元組列表，並按戰力從高到低排序。
    """
    if not text_data or not isinstance(text_data, str):
        return []
    
    numbers = re.findall(r'\d+', text_data)
    
    power_list = []
    for i in range(0, len(numbers), 2):
        if i + 1 < len(numbers):
            key = int(numbers[i])
            value = int(numbers[i+1])
            power_list.append((key, value))
            
    # 返回按戰力從高到低排序的列表
    return sorted(power_list, key=lambda x: x[1], reverse=True)

def find_best_allocation(our_teams, targets):
    """
    使用「差額優先貪心演算法」尋找一組可行的分組方案。
    
    Args:
        our_teams (list): 我方所有隊伍的列表 [(編號, 戰力), ...], 已按戰力從高到低排序。
        targets (dict): 三路的目標戰力 {'left': int, 'center': int, 'right': int}

    Returns:
        dict: 包含分配結果的字典。
    """
    # 初始化三路的狀態
    lanes = {
        'left': {'teams': [], 'current_power': 0, 'target': targets['left']},
        'center': {'teams': [], 'current_power': 0, 'target': targets['center']},
        'right': {'teams': [], 'current_power': 0, 'target': targets['right']}
    }
    
    # 逐一分配我方隊伍
    for team_id, team_power in our_teams:
        deficits = {}
        for name, data in lanes.items():
            if len(data['teams']) < 20: # 只有在名額未滿時才考慮
                deficits[name] = data['target'] - data['current_power']
            else:
                deficits[name] = float('-inf') # 如果滿了，優先度設為最低

        if not deficits or all(v == float('-inf') for v in deficits.values()):
            continue

        # 找到缺口最大的那一路
        best_lane_to_assign = max(deficits, key=deficits.get)
        
        # 分配隊伍
        lanes[best_lane_to_assign]['teams'].append((team_id, team_power))
        lanes[best_lane_to_assign]['current_power'] += team_power

    # 演算法結束後，整理並驗證結果
    final_allocation = {'success': False, 'lanes': {}}
    all_lanes_success = True

    for name, data in lanes.items():
        is_success = data['current_power'] >= data['target']
        if not is_success:
            all_lanes_success = False
        
        final_allocation['lanes'][name] = {
            'teams': sorted(data['teams'], key=lambda x: x[0]),
            'total_power': data['current_power'],
            'target': data['target'],
            'difference': data['current_power'] - data['target'],
            'count': len(data['teams']),
            'is_success': is_success
        }
    
    final_allocation['success'] = all_lanes_success
    
    return final_allocation

def run_precise_optimization_logic(our_data_list, enemy_data, advantages):
    """
    主邏輯函式，整合初步分析與分組演算法。
    """
    report = {
        "title": "精確分組優化分析報告",
        "analysis": [],
        "summary": "",
        "best_allocation": None
    }
    
    our_teams_sorted = our_data_list
    enemy_total_powers = { name: sum(v for k,v in teams) for name, teams in enemy_data.items() }
    
    our_target_powers = {
        'left': enemy_total_powers['left'] + advantages.get('left', 0),
        'center': enemy_total_powers['center'] + advantages.get('center', 0),
        'right': enemy_total_powers['right'] + advantages.get('right', 0)
    }

    report['analysis'].append({"step": "目標計算", "enemy_totals": enemy_total_powers, "our_targets": our_target_powers})
    
    our_total_power = sum(p for _, p in our_teams_sorted)
    required_total_power = sum(our_target_powers.values())
    power_diff = our_total_power - required_total_power

    report['analysis'].append({
        "step": "總戰力評估",
        "our_total_power": our_total_power,
        "required_total_power": required_total_power,
        "power_difference": power_diff
    })

    if len(our_teams_sorted) != 60:
        report['summary'] = f"錯誤：我方隊伍數量為 {len(our_teams_sorted)}，不等於60組，無法進行分組。"
        return report

    if power_diff < 0:
        report['summary'] = f"警告：我方總戰力不足！距離需求還差 {-power_diff:,} 點，無法找到滿足條件的方案。"
        return report

    allocation_result = find_best_allocation(our_teams_sorted, our_target_powers)
    report['best_allocation'] = allocation_result

    if allocation_result['success']:
        summary_text = "成功找到一組可行的分配方案！詳情如下。"
    else:
        summary_text = "警告：雖然總戰力充足，但此演算法未能找到滿足所有路需求的分配方案。\n可能是強隊過於集中導致部分路戰力不足，可以嘗試調整優勢值或手動優化。"
        
    report['summary'] = summary_text
    
    return report

# --- 3. Flask 路由 (API 端點) ---

@app.route('/api/upload_excel', methods=['POST'])
def api_upload_excel():
    if 'excel_file' not in request.files: return jsonify({"error": "請求中找不到檔案部分"}), 400
    file = request.files['excel_file']
    if file.filename == '': return jsonify({"error": "未選擇任何檔案"}), 400
    try:
        df = pd.read_excel(file, header=None)
        processed_text_parts = []
        for _, row in df.iterrows():
            if len(row) >= 2 and pd.notna(row[0]) and pd.notna(row[1]):
                processed_text_parts.append(str(int(row[0])))
                processed_text_parts.append(str(int(row[1])))
        return jsonify({"power_text": " ".join(processed_text_parts)})
    except Exception as e: return jsonify({"error": f"處理 Excel 檔案時發生錯誤: {e}"}), 500

@app.route('/api/precise_optimization', methods=['POST'])
def api_precise_optimization():
    try:
        data = request.get_json()
    except Exception as e:
        return jsonify({"error": f"請求格式錯誤: {e}"}), 400
    
    our_power_data = parse_power_data(data.get('our_power'))
    enemy_power_data = {
        'left': parse_power_data(data.get('enemy_left')),
        'center': parse_power_data(data.get('enemy_center')),
        'right': parse_power_data(data.get('enemy_right'))
    }
    try:
        advantages = {
            'left': int(data.get('left_advantage', 0)),
            'center': int(data.get('center_advantage', 0)),
            'right': int(data.get('right_advantage', 0))
        }
    except (ValueError, TypeError):
         return jsonify({"error": "優勢值必須是有效的數字"}), 400

    result = run_precise_optimization_logic(our_power_data, enemy_power_data, advantages)
    return jsonify(result)


@app.route('/')
def main_index():
    return render_template('main_index.html')

@app.route('/simulator')
def simulator_home():
    return render_template('index.html')

@app.route('/view')
def view_map():
    """ 顯示觀看地圖頁面 """
    return render_template('view.html')

@app.route('/edit')
def edit_map():
    """ 顯示編輯地圖頁面 """
    return render_template('edit.html')    

    
    
    
# --- 4. 啟動伺服器 ---

if __name__ == '__main__':
    print("伺服器即將啟動於 http://127.0.0.1:5000")
    print("若要在同一個網路下的其他裝置連線，請使用電腦的區域IP位址。")
    app.run(host='0.0.0.0', port=5000, debug=True)