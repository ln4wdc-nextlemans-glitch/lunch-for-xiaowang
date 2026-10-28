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

# 遍历表格中的每一天
table = soup.find('table', class_='week_menu_table')
if table:
    rows = table.find_all('tr')
    for tr in rows:
        th = tr.find('th')
        td = tr.find('td')
        if not th or not td:
            continue
            
        # 提取日期和星期，例如 "10月26日（月）"
        date_str_raw = th.text.strip()
        date_match = re.search(r'(\d+)月(\d+)日[（\(]([月火水木金土日])[）\)]', date_str_raw)
        
        if date_match:
            month, day, weekday = date_match.groups()
            formatted_date = f"{current_year}-{int(month):02d}-{int(day):02d}"
        else:
            formatted_date = date_str_raw
            weekday = ""

        # 详细菜品列表（按 <br> 拆分）
        txt_elem = td.find('div', class_='txt_week_menu')
        dishes = []
        if txt_elem:
            for line in txt_elem.stripped_strings:
                cleaned_line = line.strip()
                if cleaned_line:
                    dishes.append(cleaned_line)

        # 收集当天所有菜品
        for dish in dishes:
            scraped_rows.append({
                "date": formatted_date,
                "day_of_week": weekday,
                "dish": dish,
                "ingredients": dish
            })

# 2. 连接 Google Sheets 并写入
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
creds_dict = json.loads(creds_json)
creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
client = gspread.authorize(creds)

spreadsheet_id = os.environ.get("SPREADSHEET_ID")
sheet = client.open_by_key(spreadsheet_id)

# --- 写入 Sheet 1：每天的食物 ---
try:
    sheet1 = sheet.worksheet("每天的食物")
except gspread.exceptions.WorksheetNotFound:
    sheet1 = sheet.add_worksheet(title="每天的食物", rows="1000", cols="10")
    sheet1.append_row(["date", "day_of_week", "dish", "ingredients"])

# 检查 Sheet1 是否为空，如果第一行为空或没有表头，先写表头
existing_values = sheet1.get_all_values()
if not existing_values:
    sheet1.append_row(["date", "day_of_week", "dish", "ingredients"])

# 为了避免每次运行都重复追加相同的数据，可以先清空 Sheet1 或者做去重
# 这里我们选择每次覆盖写入最新抓取的数据（保持 Sheet1 始终是最新一周的数据，或者累加）
# 如果你想累加，保留下面这行：
for item in scraped_rows:
    sheet1.append_row([item["date"], item["day_of_week"], item["dish"], item["ingredients"]])

# --- 读取并更新统计 Sheet 2 和 Sheet 3 ---
data = sheet1.get_all_records()
df = pd.DataFrame(data)

# 兼容可能存在的中文表头或空白行
if not df.empty:
    # 如果列名里包含“日期”，将其重命名为 “date” 以防万一
    df = df.rename(columns={"日期": "date", "菜品名称": "dish", "食材": "ingredients"})
    
    if 'date' in df.columns and 'dish' in df.columns:
        # Sheet 2：统计每种菜品出现的次数
        dish_counts = df['dish'].value_counts().reset_index()
        dish_counts.columns = ['菜品名称', '出现次数']
        
        try:
            sheet2 = sheet.worksheet("菜品出现次数统计")
        except gspread.exceptions.WorksheetNotFound:
            sheet2 = sheet.add_worksheet(title="菜品出现次数统计", rows="100", cols="5")
        
        sheet2.clear()
        sheet2.append_row(["菜品名称", "出现次数"])
        for _, row in dish_counts.iterrows():
            sheet2.append_row([row['菜品名称'], int(row['出现次数'])])

        # Sheet 3：按月分为周期，统计每种食材出现的频率
        df['月份'] = pd.to_datetime(df['date']).dt.to_period('M').astype(str)
        ing_col = 'ingredients' if 'ingredients' in df.columns else 'dish'
        ing_freq = df.groupby(['月份', ing_col]).size().reset_index(name='出现频率')
        ing_freq.columns = ['月份', '食材', '出现频率']
        
        try:
            sheet3 = sheet.worksheet("按月食材频率统计")
        except gspread.exceptions.WorksheetNotFound:
            sheet3 = sheet.add_worksheet(title="按月食材频率统计", rows="500", cols="5")
            
        sheet3.clear()
        sheet3.append_row(["月份", "食材", "出现频率"])
        for _, row in ing_freq.iterrows():
            sheet3.append_row([row['月份'], row['食材'], int(row['出现频率'])])

print("菜单数据爬取与 Google Sheets 更新成功！")
