import os
import json
import re
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd

# 1. 网页抓取与解析
url = "https://tohan-corp.com/week-menu/"
response = requests.get(url)
response.encoding = 'utf-8'
soup = BeautifulSoup(response.text, 'html.parser')

# 获取年份（从副标题提取，如 "2026年"）
sub_ttl_elem = soup.find('div', class_='topsub_week_menu_ttl')
year_match = re.search(r'(\d{4})年', sub_ttl_elem.text) if sub_ttl_elem else None
current_year = year_match.group(1) if year_match else str(datetime.now().year)

scraped_rows = []

# 定义更精准的食材提取与拆解函数
def extract_ingredients(dish_name):
    # 1. 特殊复合菜名精准映射（直接指定多种核心食材）
    special_map = {
        'カレーコロッケ': ['じゃがいも', 'カレー'],
        '親子丼風煮': ['鶏肉', '卵'],
        '肉団子の素揚げ': ['豚肉'],
    }
    for key, ings in special_map.items():
        if key in dish_name:
            return ings

    # 2. 包含 "と" 或 "＆" 的多食材菜名自动拆分（如 "チンゲン菜とさつま揚げ"）
    cleaned_name = dish_name
    suffixes = ['の煮付', 'の塩だれ焼き', 'の味噌炒め', 'の辛し和え', 'のマヨネーズサラダ', 
                'のナムル', 'の胡麻和え', 'の金平風', '風煮', '素揚げ', 'プレミアム', 'サラダ', '煮付', '風味']
    for s in suffixes:
        cleaned_name = cleaned_name.replace(s, '')
    
    # 按 "と" 拆分多重食材
    parts = re.split(r'[と＆]', cleaned_name)
    
    found_ingredients = set()
    
    # 3. 基础核心食材词库
    keyword_map = {
        '豚肉': '豚肉',
        'キャベツ': 'キャベツ',
        'えび': 'えび',
        '海老': 'えび',
        '白身': '白身魚',
        '鮭': '鮭',
        '鶏': '鶏肉',
        'チキン': '鶏肉',
        'こんにゃく': 'こんにゃく',
        'しいたけ': 'しいたけ',
        '大根': '大根',
        'さつまいも': 'さつまいも',
        'ほうれん草': 'ほうれん草',
        '小松菜': '小松菜',
        '茄子': '茄子',
        '豆腐': '豆腐',
        '白菜': '白菜',
        '春雨': '春雨',
        'グリンピース': 'グリンピース',
        '玉子': '卵',
        '卵': '卵',
        'たこ': 'たこ',
        'ツナ': 'ツナ',
        'レンコン': 'レンコン',
        'ポテト': 'じゃがいも',
        'チンゲン菜': 'チンゲン菜',
        'さつま揚げ': 'さつま揚げ',
    }

    for part in parts:
        part = part.strip()
        matched = False
        for keyword, ingredient in keyword_map.items():
            if keyword in part:
                found_ingredients.add(ingredient)
                matched = True
        # 如果切出来的一部分不在字典里，但它本身是一个干净的词，也可以视情况添加
        if not matched and len(part) > 1:
            # 过滤掉纯烹饪修饰词
            if part not in ['煮付', '炒め', '焼き', '和え', 'ナムル']:
                found_ingredients.add(part)
                
    # 如果实在没匹配到，兜底返回原菜名或忽略
    if not found_ingredients:
        found_ingredients.add(dish_name)
        
    return list(found_ingredients)

# 遍历表格中的每一天
table = soup.find('table', class_='week_menu_table')
if table:
    rows = table.find_all('tr')
    for tr in rows:
        th = tr.find('th')
        td = tr.find('td')
        if not th or not td:
            continue
            
        date_str_raw = th.text.strip()
        date_match = re.search(r'(\d+)月(\d+)日[（\(]([月火水木金土日])[）\)]', date_str_raw)
        
        if date_match:
            month, day, weekday = date_match.groups()
            formatted_date = f"{current_year}-{int(month):02d}-{int(day):02d}"
        else:
            formatted_date = date_str_raw
            weekday = ""

        txt_elem = td.find('div', class_='txt_week_menu')
        dishes = []
        if txt_elem:
            for line in txt_elem.stripped_strings:
                cleaned_line = line.strip()
                if cleaned_line:
                    dishes.append(cleaned_line)

        for dish in dishes:
            ingredients = extract_ingredients(dish)
            for ing in ingredients:
                scraped_rows.append([formatted_date, weekday, dish, ing])

# 2. 连接 Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
creds_dict = json.loads(creds_json)
creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
client = gspread.authorize(creds)

spreadsheet_id = os.environ.get("SPREADSHEET_ID")
sheet = client.open_by_key(spreadsheet_id)

# --- 写入 Sheet 1：每天的食物（增量追加 + 去重） ---
try:
    sheet1 = sheet.worksheet("每天的食物")
except gspread.exceptions.WorksheetNotFound:
    sheet1 = sheet.add_worksheet(title="每天的食物", rows="1000", cols="10")
    sheet1.append_row(["date", "day_of_week", "dish", "ingredient"])

existing_data = sheet1.get_all_values()
existing_set = set()
if len(existing_data) > 1:
    for row in existing_data[1:]:
        if len(row) >= 4:
            existing_set.add((row[0], row[2], row[3]))

new_rows = []
for row in scraped_rows:
    row_key = (row[0], row[2], row[3])
    if row_key not in existing_set:
        new_rows.append(row)

if new_rows:
    sheet1.append_rows(new_rows)
    print(f"成功追加了 {len(new_rows)} 条新数据到 Sheet 1")
else:
    print("没有发现需要补充的新数据（已存在）")

# --- 读取 Sheet 1 的全部历史数据，全量更新统计 Sheet 2 和 Sheet 3 ---
all_data = sheet1.get_all_records()
df = pd.DataFrame(all_data)

if not df.empty:
    df = df.rename(columns={"日期": "date", "菜品名称": "dish", "食材": "ingredient", "ingredients": "ingredient"})
    
    if 'date' in df.columns and 'dish' in df.columns:
        # Sheet 2：基于全部历史数据统计每种菜品出现的总次数
        df_dish_unique = df[['date', 'dish']].drop_duplicates()
        dish_counts = df_dish_unique['dish'].value_counts().reset_index()
        dish_counts.columns = ['菜品名称', '出现次数']
        
        try:
            sheet2 = sheet.worksheet("菜品出现次数统计")
        except gspread.exceptions.WorksheetNotFound:
            sheet2 = sheet.add_worksheet(title="菜品出现次数统计", rows="100", cols="5")
        
        sheet2.clear()
        sheet2_data = [["菜品名称", "出现次数"]] + [[row['菜品名称'], int(row['出现次数'])] for _, row in dish_counts.iterrows()]
        sheet2.append_rows(sheet2_data)

        # Sheet 3：按月分为周期，基于全部历史数据统计每種食材出现的频率
        df['月份'] = pd.to_datetime(df['date']).dt.to_period('M').astype(str)
        ing_freq = df.groupby(['月份', 'ingredient']).size().reset_index(name='出现频率')
        ing_freq.columns = ['月份', '食材', '出现频率']
        
        try:
            sheet3 = sheet.worksheet("按月食材频率统计")
        except gspread.exceptions.WorksheetNotFound:
            sheet3 = sheet.add_worksheet(title="按月食材频率统计", rows="500", cols="5")
            
        sheet3.clear()
        sheet3_data = [["月份", "食材", "出现频率"]] + [[row['月份'], row['食材'], int(row['出现频率'])] for _, row in ing_freq.iterrows()]
        sheet3.append_rows(sheet3_data)

print("历史数据累加与全量统计更新完成！")
