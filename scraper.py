import os
import json
import re
import time
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd

SEP = "、"

# ========== 1. 网页抓取（带 UA、超时、重试） ==========
url = "https://tohan-corp.com/week-menu/"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.8",
}

response = None
for attempt in range(3):
    try:
        response = requests.get(url, headers=headers, timeout=(10, 30))
        response.raise_for_status()
        break
    except requests.exceptions.RequestException as e:
        print(f"第{attempt + 1}次请求失败: {e}")
        response = None
        time.sleep(5 * (attempt + 1))

if response is None:
    raise SystemExit("无法访问 tohan-corp.com，本次跳过（未修改任何表格数据）")

response.encoding = 'utf-8'
soup = BeautifulSoup(response.text, 'html.parser')

sub_ttl_elem = soup.find('div', class_='topsub_week_menu_ttl')
year_match = re.search(r'(\d{4})年', sub_ttl_elem.text) if sub_ttl_elem else None
current_year = year_match.group(1) if year_match else str(datetime.now().year)

# ========== 2. 食材提取 ==========
SPECIAL_MAP = {
    'カレーコロッケ': ['じゃがいも', 'カレー'],
    '親子丼風煮': ['鶏肉', '卵'],
    '肉団子の素揚げ': ['豚肉'],
}

SUFFIXES = ['の煮付', 'の塩だれ焼き', 'の味噌炒め', 'の辛し和え', 'のマヨネーズサラダ',
            'のナムル', 'の胡麻和え', 'の金平風', '風煮', '素揚げ', 'プレミアム',
            'サラダ', '煮付', '風味']

KEYWORD_MAP = {
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
    'ウインナー': 'ウインナー',
    'ベーコン': 'ベーコン',
    'スパゲティー': 'パスタ・麺類',
    'ペンネ': 'パスタ・麺類',
    'うどん': 'パスタ・麺類',
    # 新增（来自截图里未匹配的词）
    'トマト': 'トマト',
    '青しそ': '青しそ',
    '隠元': 'いんげん',
    'うす揚げ': 'うす揚げ',
}

unmatched_log = set()


def extract_ingredients(dish_name):
    """返回 (食材列表, 未匹配片段列表)。未匹配的片段会原文并入食材列表。"""
    for key, ings in SPECIAL_MAP.items():
        if key in dish_name:
            return list(ings), []

    cleaned = dish_name
    for s in SUFFIXES:
        cleaned = cleaned.replace(s, '')

    parts = re.split(r'[と＆&]', cleaned)

    found, unmapped = [], []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        matched = False
        for keyword, ingredient in KEYWORD_MAP.items():
            if keyword in part:
                if ingredient not in found:
                    found.append(ingredient)
                matched = True
        if not matched:
            if part not in unmapped:
                unmapped.append(part)
            unmatched_log.add((dish_name, part))

    # 未匹配的片段原文写出，方便维护 mapping
    ingredients = found + unmapped
    if not ingredients:          # 极端情况：切完什么都不剩，用原菜名
        ingredients = [dish_name]
        unmapped = [dish_name]
    return ingredients, unmapped


# ========== 3. 解析表格（1 道菜 = 1 行） ==========
scraped_rows = []   # [date, weekday, dish, "食材A、食材B", "未匹配A、未匹配B"]
table = soup.find('table', class_='week_menu_table')
if table:
    for tr in table.find_all('tr'):
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
                line = line.strip()
                if line:
                    dishes.append(line)

        for dish in dishes:
            ings, unmapped = extract_ingredients(dish)
            scraped_rows.append([formatted_date, weekday, dish, SEP.join(ings), SEP.join(unmapped)])

# 本次去重（同一天同一道菜只保留一行）
seen = set()
unique_rows = []
for r in scraped_rows:
    key = (r[0], r[2])
    if key not in seen:
        seen.add(key)
        unique_rows.append(r)
scraped_rows = unique_rows

if not scraped_rows:
    raise SystemExit("没有解析到任何菜单数据（页面结构可能变了），本次不修改表格")

print(f"本次解析到 {len(scraped_rows)} 道菜")
if unmatched_log:
    print("【未匹配到食材的片段，建议补进 KEYWORD_MAP】")
    for dish, part in sorted(unmatched_log):
        print(f"  菜名: {dish}  片段: {part}")

# ========== 4. 连接 Google Sheets ==========
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds_dict = json.loads(os.environ.get("GOOGLE_SHEETS_CREDENTIALS"))
creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(os.environ.get("SPREADSHEET_ID"))

HEADER1 = ["date", "day_of_week", "dish", "ingredient", "unmapped"]  # [unmapped列]

# ---- Sheet 1：每天的食物 ----
try:
    sheet1 = sheet.worksheet("每天的食物")
except gspread.exceptions.WorksheetNotFound:
    sheet1 = sheet.add_worksheet(title="每天的食物", rows="1000", cols="10")
    sheet1.append_row(HEADER1)

existing = sheet1.get_all_values()

# 读取旧数据，并把旧格式（一道菜多行）合并为一道菜一行
merged = {}   # (date, dish) -> [date, weekday, dish, [ingredients], [unmapped]]
for r in existing[1:]:
    r = (r + [""] * 5)[:5]          # [unmapped列] 补齐到 5 列
    if not any(r):
        continue
    key = (r[0], r[2])
    if key not in merged:
        merged[key] = [r[0], r[1], r[2], [], []]
    for x in r[3].split(SEP):
        x = x.strip()
        if x and x not in merged[key][3]:
            merged[key][3].append(x)
    for x in r[4].split(SEP):         # [unmapped列]
        x = x.strip()
        if x and x not in merged[key][4]:
            merged[key][4].append(x)

old_rows = [[v[0], v[1], v[2], SEP.join(v[3]), SEP.join(v[4])] for v in merged.values()]

# 本次抓到的 (date, dish)：旧行删除，用新行替换
refresh_keys = {(r[0], r[2]) for r in scraped_rows}
kept_rows = [r for r in old_rows if (r[0], r[2]) not in refresh_keys]
removed = len(old_rows) - len(kept_rows)

final_rows = kept_rows + scraped_rows
final_rows.sort(key=lambda r: (r[0], r[2]))

sheet1.clear()
sheet1.update(range_name="A1", values=[HEADER1] + final_rows)
print(f"Sheet 1：替换旧行 {removed} 条，写入新行 {len(scraped_rows)} 条，当前共 {len(final_rows)} 条")

# ---- Sheet 2 / Sheet 3 ----
df = pd.DataFrame(final_rows, columns=HEADER1)

if not df.empty:
    # Sheet 2：每种菜品出现次数（按天去重）
    df_dish_unique = df[['date', 'dish']].drop_duplicates()
    dish_counts = df_dish_unique['dish'].value_counts().reset_index()
    dish_counts.columns = ['菜品名称', '出现次数']

    try:
        sheet2 = sheet.worksheet("菜品出现次数统计")
    except gspread.exceptions.WorksheetNotFound:
        sheet2 = sheet.add_worksheet(title="菜品出现次数统计", rows="100", cols="5")

    sheet2.clear()
    sheet2_data = [["菜品名称", "出现次数"]] + \
                  [[r['菜品名称'], int(r['出现次数'])] for _, r in dish_counts.iterrows()]
    sheet2.update(range_name="A1", values=sheet2_data)

    # Sheet 3：按月食材频率（先按「、」拆开再统计）
    df['月份'] = pd.to_datetime(df['date'], errors='coerce').dt.to_period('M').astype(str)
    df_valid = df[df['月份'] != 'NaT'].copy()
    df_valid['ingredient'] = df_valid['ingredient'].str.split(SEP)
    df_ex = df_valid.explode('ingredient')
    df_ex['ingredient'] = df_ex['ingredient'].str.strip()
    df_ex = df_ex[df_ex['ingredient'] != '']
    ing_freq = (df_ex.drop_duplicates(['date', 'dish', 'ingredient'])
                .groupby(['月份', 'ingredient']).size().reset_index(name='出现频率'))
    ing_freq.columns = ['月份', '食材', '出现频率']

    try:
        sheet3 = sheet.worksheet("按月食材频率统计")
    except gspread.exceptions.WorksheetNotFound:
        sheet3 = sheet.add_worksheet(title="按月食材频率统计", rows="500", cols="5")

    sheet3.clear()
    sheet3_data = [["月份", "食材", "出现频率"]] + \
                  [[r['月份'], r['食材'], int(r['出现频率'])] for _, r in ing_freq.iterrows()]
    sheet3.update(range_name="A1", values=sheet3_data)

print("历史数据累加与全量统计更新完成！")
