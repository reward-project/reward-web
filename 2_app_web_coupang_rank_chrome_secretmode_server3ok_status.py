from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import time
import random
import urllib.parse
import pandas as pd
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from jinja2 import Environment, PackageLoader, select_autoescape
from jinja2 import StrictUndefined
from apscheduler.schedulers.background import BackgroundScheduler
from flask_socketio import SocketIO, emit
import eventlet
import sys
sys.setrecursionlimit(10000)  # 재귀 제한 증가
eventlet.monkey_patch()
import os

# Flask 앱과 SocketIO 초기화
app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
app.jinja_env.undefined = StrictUndefined
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")

# 스케줄러 초기화
scheduler = BackgroundScheduler({
    'apscheduler.jobstores.default': {
        'type': 'memory'
    },
    'apscheduler.executors.default': {
        'class': 'apscheduler.executors.pool:ThreadPoolExecutor',
        'max_workers': '20'
    },
    'apscheduler.job_defaults.coalesce': 'false',
    'apscheduler.job_defaults.max_instances': '3'
})

# 로그 파일 경로 설정
LOG_DIR = 'logs'
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# 전역 변수 설정
log_messages = []
search_active = False

def get_log_filename():
    """오늘 날짜의 로그 파일명 반환"""
    return os.path.join(LOG_DIR, f"search_log_{datetime.now().strftime('%Y%m%d')}.txt")

def emit_log(message):
    """로그 메시지를 클라이언트에 전송하고 파일에 저장"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    formatted_message = f"[{timestamp}] {message}"
    
    # 메모리에 저장 (최대 1000개까지만 저장)
    log_messages.append(formatted_message)
    if len(log_messages) > 1000:
        log_messages.pop(0)
    
    # 파일에 저장
    try:
        with open(get_log_filename(), 'a', encoding='utf-8') as f:
            f.write(formatted_message + '\n')
    except Exception as e:
        print(f"로그 파일 저장 중 오류: {e}")
    
    # 클라이언트에 전송
    socketio.emit('log_message', {'message': formatted_message})

def load_excel():
    """엑셀 파일 읽기"""
    try:
        emit_log("엑셀 파일 로딩 시작...")
        
        if not os.path.exists('coupang_rank.xlsx'):
            emit_log("엑셀 파일이 존재하지 않습니다. 새로운 파일을 생성합니다.")
            # 기본 구조로 새로운 엑셀 파일 생성
            df = pd.DataFrame(columns=[
                'number', 'keyword', 'product_id', 'page', 
                'rank', 'ad', 'page_rank', 'date', 'time'
            ])
            # 초기값 설정
            df['page'] = 0
            df['rank'] = 0
            df['ad'] = '0'
            df['page_rank'] = 0
            
            df.to_excel('coupang_rank.xlsx', index=False)
            emit_log("새로운 엑셀 파일이 생성되었습니다.")
            return df
            
        df = pd.read_excel('coupang_rank.xlsx')
        
        # NaN 값을 0으로 변경
        df['page'] = df['page'].fillna(0).astype(int)
        df['rank'] = df['rank'].fillna(0).astype(int)
        df['ad'] = df['ad'].fillna('0')
        df['page_rank'] = df['page_rank'].fillna(0).astype(int)
        
        # 데이터 유효성 검사
        required_columns = ['number', 'keyword', 'product_id']
        missing_columns = [col for col in required_columns if col not in df.columns]
        
        if missing_columns:
            emit_log(f"엑셀 파일에 필수 컬럼이 없습니다: {', '.join(missing_columns)}")
            return None
            
        if df.empty:
            emit_log("엑셀 파일에 데이터가 없습니다.")
            return df
            
        # 데이터 전처리
        df['keyword'] = df['keyword'].astype(str).str.strip()
        df['product_id'] = df['product_id'].astype(str).str.strip()
        
        valid_rows = df[df['keyword'].notna() & (df['keyword'] != '') & 
                       df['product_id'].notna() & (df['product_id'] != '')]
                       
        if len(valid_rows) == 0:
            emit_log("유효한 검색어와 상품 ID가 없습니다.")
            socketio.emit('search_status', {
                'status': 'waiting',
                'current': 0,
                'total': 0,
                'keyword': '',
                'message': '검색 대기 중'
            })
        else:
            emit_log(f"총 {len(valid_rows)}개의 유효한 데이터를 읽었습니다.")
            socketio.emit('search_status', {
                'status': 'waiting',
                'current': 0,
                'total': len(valid_rows),
                'keyword': '',
                'message': '검색 대기 중'
            })
            
        return df
        
    except Exception as e:
        emit_log(f"엑셀 파일 읽기 오류: {str(e)}")
        socketio.emit('search_status', {
            'status': 'error',
            'current': 0,
            'total': 0,
            'keyword': '',
            'message': f'엑셀 파일 읽기 오류: {str(e)}'
        })
        return None

def scheduled_search():
    """1시간마다 실행될 검색 함수"""
    try:
        emit_log("\n=== 정기 검색 시작 ===")
        perform_search()  # main() 대신 새로운 함수 호출
    except Exception as e:
        emit_log(f"정기 검색 중 오류 발생: {str(e)}")

def perform_search():
    """실제 검색을 수행하는 함수"""
    global search_active
    try:
        if not search_active:
            emit_log("검색이 이미 중지되었습니다.")
            socketio.emit('search_status', {'status': 'waiting'})
            return
        
        emit_log("\n=== 검색 프로세스 초기화 중... ===")
        socketio.emit('search_status', {'status': 'searching'})  # 검색 상태 유지
        
        # 1. 엑셀 파일 로드 및 데이터 검증
        rank_df = load_excel()
        if rank_df is None:
            emit_log("엑셀 파일을 읽을 수 없습니다.")
            search_active = False
            socketio.emit('search_status', {'status': 'error'})
            return
        
        # 2. 유효한 데이터 필터링
        valid_df = rank_df[
            rank_df['keyword'].notna() & (rank_df['keyword'].str.strip() != '') &
            rank_df['product_id'].notna() & (rank_df['product_id'].str.strip() != '')
        ].copy()
        
        total_items = len(valid_df)
        
        if total_items == 0:
            emit_log("검색할 유효한 데이터가 없습니다.")
            search_active = False
            socketio.emit('search_status', {
                'status': 'error',
                'current': 0,
                'total': 0,
                'keyword': '',
                'message': '검색할 데이터가 없습니다'
            })
            return
        
        # 3. 검색 시작
        emit_log(f"\n=== 검색 시작 (총 {total_items}개 항목) ===")
        
        # 4. 각 키워드별 검색 실행
        for index, row in valid_df.iterrows():
            if not search_active:
                emit_log("\n=== 검색이 중지되었습니다 ===")
                socketio.emit('search_status', {'status': 'waiting'})
                break
            
            try:
                keyword = str(row['keyword'])
                product_id = str(row['product_id'])
                current_number = index + 1
                
                # 현재 검색 상태 업데이트
                current_status = f"검색 중: {keyword} ({current_number}/{total_items})"
                emit_log(f"\n=== {current_status} ===")
                socketio.emit('search_status', {
                    'status': 'searching',
                    'current': current_number,
                    'total': total_items,
                    'keyword': keyword,
                    'message': current_status
                })
                
                # 실제 검색 수행
                result = search_product(keyword, product_id)
                
                # 검색 결과 처리
                if result:
                    emit_log(f"상품 발견: 페이지 {result['page']}, " + 
                            (f"광고 순위 {result['rank']}" if result['rank_type'] == 'ad' else f"일반 순위 {result['rank']}") +
                            f", 전체 순위 {result['page_rank']}")
                    
                    rank_df.loc[index, 'page'] = result['page']
                    rank_df.loc[index, 'rank'] = result['rank'] if result['rank_type'] != 'ad' else 0
                    rank_df.loc[index, 'ad'] = 'O' if result['rank_type'] == 'ad' else '0'
                    rank_df.loc[index, 'page_rank'] = result['page_rank']
                else:
                    emit_log("상품을 찾을 수 없습니다.")
                    rank_df.loc[index, 'page'] = 0
                    rank_df.loc[index, 'rank'] = 0
                    rank_df.loc[index, 'ad'] = '0'
                    rank_df.loc[index, 'page_rank'] = 0
                
                # 날짜와 시간 업데이트
                rank_df.loc[index, 'date'] = datetime.now().strftime('%Y-%m-%d')
                rank_df.loc[index, 'time'] = datetime.now().strftime('%H:%M:%S')
                
                # 결과 저장
                rank_df.to_excel('coupang_rank.xlsx', index=False)
                socketio.emit('refresh_page', {})
                
                # 다음 검색 전 대기
                time.sleep(3)
                
            except Exception as e:
                emit_log(f"검색 중 오류 발생: {str(e)}")
                # 오류가 발생해도 검색 상태 유지
                socketio.emit('search_status', {'status': 'searching'})
                continue
        
        # 5. 검색 완료
        search_active = False
        if search_active:
            emit_log("\n=== 모든 검색이 완료되었습니다 ===")
            socketio.emit('search_status', {'status': 'completed'})
        
    except Exception as e:
        emit_log(f"검색 프로세스 오류: {str(e)}")
        search_active = False
        socketio.emit('search_status', {'status': 'error'})

@app.route("/")
def index():
    try:
        df = load_excel()
        if df is not None:
            data = df.to_dict('records')
            # 최근 100개의 로그만 전달
            recent_logs = log_messages[-100:] if log_messages else []
            return render_template('template.html', 
                                title="Coupang Rank Search",
                                message="Welcome to Coupang Rank Search Service",
                                data=data,
                                log_messages=recent_logs)
        return "엑셀 파일을 읽을 수 없습니다."
    except Exception as e:
        emit_log(f"인덱스 페이지 로드 중 오류: {str(e)}")
        return str(e)

@socketio.on('connect')
def handle_connect():
    """클라이언트 연결 시 기존 로그 전송"""
    # 최근 100개의 로그만 전송
    recent_logs = log_messages[-100:] if log_messages else []
    for message in recent_logs:
        emit('log_message', {'message': message})

@app.route("/update", methods=['POST'])
def update_excel():
    """웹페이지에서 데이터 정"""
    try:
        data = request.json
        df = load_excel()
        if df is not None:
            row_index = int(data['index'])
            column = data['column']
            value = data['value']
            df.at[row_index, column] = value
            df.to_excel('coupang_rank.xlsx', index=False)
            return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

def start_scheduler():
    """스케줄러 시작"""
    if not scheduler.running:
        scheduler.add_job(scheduled_search, 'interval', hours=1, id='scheduled_search')
        scheduler.start()
        emit_log("스케줄러가 시작되었습니다.")

# -*- coding: utf-8 -*-
 

def smooth_scroll(browser):
    """사람처럼 천천히 스크롤하는 함수"""
    # 현재 화면 높이 가져오기
    screen_height = browser.execute_script("return window.innerHeight")
    # 전체 문서 높이 가져오기
    total_height = browser.execute_script("return document.body.scrollHeight")
    
    current_scroll = 0
    scroll_increment = screen_height // 4  # 한 번에 스크롤할 높이 (화면 높이의 1/4)
    
    print("이지 스크롤 중...")
    while current_scroll < total_height:
        # 다음 스크롤 위치 계산
        next_scroll = min(current_scroll + scroll_increment, total_height)
        
        # 드럽게 스크롤
        browser.execute_script(f"window.scrollTo({current_scroll}, {next_scroll});")
        current_scroll = next_scroll
        
        # 랜덤한 시간 대기 (0.5~1.5초)
        time.sleep(random.uniform(0.5, 1.5))
        
        # 새로운 컨텐츠 로딩을 위한 대기
        new_height = browser.execute_script("return document.body.scrollHeight")
        if new_height > total_height:
            total_height = new_height

def analyze_page(soup, page, product_id):
    """페이지 ��을 분석하여 상품 찾기"""
    products = soup.select('.search-product')
    non_ad_rank = 0
    ad_rank = 0
    ad_count = 0
    
    for product in products:
        # 광고 상품 체크
        is_ad = 'search-product__ad' in product.get('class', [])
        
        if is_ad:
            ad_count += 1
            ad_rank += 1
        else:
            non_ad_rank += 1
        
        # 상품 정보 추출
        product_link = product.select_one('a.search-product-link')
        if not product_link:
            continue
        
        current_name = product.select_one('.name')
        if not current_name:
            continue
        
        current_name = current_name.text.strip()
        current_url = product_link.get('href', '')
        current_id = current_url.split('/')[-1].split('?')[0]
        
        # 목록 상품 확인
        if product_id == current_id:
            print("\n[상품 발견!]")
            print(f"페이지: {page}")
            
            # 광고 상품인 경우
            if is_ad:
                print(f"광고 순위: {ad_rank}")
                rank_type = "ad"
                rank_value = ad_rank
            else:
                print(f"일반 순위: {non_ad_rank}")
                rank_type = "normal"
                rank_value = non_ad_rank
                
            if ad_count > 0:
                print(f"광고 상품 수: {ad_count}개")
            print(f"상품명: {current_name}")
            print(f"상품 ID: {current_id}")
            print(f"URL: https://www.coupang.com{current_url}")
            
            # 전체 페이지 순위 계산 (36개 기준)
            page_rank = ((page - 1) * 36) + rank_value
            
            return {
                'page': page,
                'rank': rank_value,
                'rank_type': rank_type,
                'page_rank': page_rank,
                'ad_count': ad_count,
                'name': current_name,
                'id': current_id,
                'url': f"https://www.coupang.com{current_url}"
            }
    
    return None

def search_product(keyword, product_id):
    """쿠팡에서 상품을 검색하고 순위를 찾는 함수"""
    browser = None
    try:
        emit_log(f"검색 시작: 키워드 '{keyword}', 상품 ID '{product_id}'")
        
        # 브라우저 설정
        options = webdriver.ChromeOptions()
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--headless")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--window-size=1920x1080")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/94.0.4606.61 Safari/537.36")
        
        # ChromeDriver 설정 - 단순화된 방식으로 변경
        service = Service()
        browser = webdriver.Chrome(service=service, options=options)
        browser.maximize_window()
        
        encoded_keyword = urllib.parse.quote(keyword)
        base_url = f'https://www.coupang.com/np/search?component=&q={encoded_keyword}&channel=user'
        
        page = 1
        rank = 0
        
        while page <= 27:
            try:
                emit_log(f"\n{page}페이지 검색 중...")
                url = f"{base_url}&page={page}"
                
                browser.delete_all_cookies()
                browser.get(url)
                
                # 페이지 로딩 대기
                WebDriverWait(browser, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "search-product"))
                )
                
                # 천천히 스크롤
                time.sleep(2)
                smooth_scroll(browser)
                
                # 페이지 소스 분석
                soup = BeautifulSoup(browser.page_source, 'html.parser')
                result = analyze_page(soup, page, product_id)
                
                if result:
                    emit_log(f"상품 발견! 페이지: {page}")
                    return result
                
                # 다음 페이지로 이동
                if page < 27:
                    try:
                        products = browser.find_elements(By.CSS_SELECTOR, ".search-product:not(.search-product__ad-label)")
                        
                        if products:
                            browser.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", products[-1])
                            time.sleep(2)
                        
                        next_page = WebDriverWait(browser, 10).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, f"a.page-link[href*='page={page + 1}']"))
                        )
                        
                        browser.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", next_page)
                        time.sleep(2)
                        
                        next_page.click()
                        time.sleep(3)
                        
                    except Exception as e:
                        emit_log(f"페이지 이동 중 오류: {str(e)}")
                        page += 1
                        continue
                
                page += 1
                
            except Exception as e:
                emit_log(f"페이지 {page} 검색 중 오류: {str(e)}")
                if browser:
                    browser.quit()
                browser = webdriver.Chrome(service=service, options=options)
                browser.maximize_window()
                time.sleep(3)
                continue
        
        emit_log("\n[검색 결과]")
        emit_log(f"키워드: {keyword}")
        emit_log(f"상품 ID: {product_id}")
        emit_log("해당 상품을 찾을 수 없습니다. (27페이지 내)")
        return None
        
    except Exception as e:
        emit_log(f"검색 중 오류 발생: {str(e)}")
        return None
        
    finally:
        if browser:
            browser.quit()
            emit_log("브라우저 종료")

# 새로운 라우트 추가
@app.route("/add_row", methods=['POST'])
def add_row():
    try:
        df = load_excel()
        if df is not None:
            new_row = {
                'number': len(df) + 1,
                'keyword': '',
                'product_id': '',
                'page': None,
                'rank': None,
                'ad': None,
                'page_rank': None,
                'date': None,
                'time': None
            }
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            df.to_excel('coupang_rank.xlsx', index=False)
            return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/delete_row", methods=['POST'])
def delete_row():
    try:
        data = request.json
        df = load_excel()
        if df is not None:
            df.drop(index=int(data['index']), inplace=True)
            df.to_excel('coupang_rank.xlsx', index=False)
            return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/search_now", methods=['POST'])
def search_now():
    try:
        data = request.json
        df = load_excel()
        if df is not None:
            row = df.iloc[int(data['index'])]
            # 백그라운드에서 검색 실행
            scheduler.add_job(
                perform_single_search,  # 새로운 함수 사용
                args=[row['keyword'], row['product_id'], int(data['index'])],
                id=f"immediate_search_{data['index']}"
            )
            return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

def perform_single_search(keyword, product_id, index):
    """단일 항목 검색을 수행하는 함수"""
    try:
        df = load_excel()
        if df is not None:
            result = search_product(keyword, product_id)
            
            df.loc[index, 'date'] = datetime.now().strftime('%Y-%m-%d')
            df.loc[index, 'time'] = datetime.now().strftime('%H:%M:%S')
            
            if result:
                df.loc[index, 'page'] = result['page']
                df.loc[index, 'rank'] = float(result['rank']) if result['rank_type'] != 'ad' else None
                df.loc[index, 'ad'] = 'O' if result['rank_type'] == 'ad' else None
                df.loc[index, 'page_rank'] = float(result['page_rank'])
            else:
                df.loc[index, 'page'] = None
                df.loc[index, 'rank'] = 999
                df.loc[index, 'ad'] = None
                df.loc[index, 'page_rank'] = 999
            
            df.to_excel('coupang_rank.xlsx', index=False)
    except Exception as e:
        emit_log(f"단일 검색 중 오류 발생: {str(e)}")

@app.route("/start_search", methods=['POST'])
def start_search():
    """검색 시작"""
    global search_active
    try:
        if search_active:
            emit_log("이미 검색이 진행 중입니다.")
            return jsonify({"status": "error", "message": "이미 검색이 진행 중입니다."})
        
        # 검색 시작 전 엑셀 데이터 확인
        df = load_excel()
        if df is None:
            emit_log("엑셀 파일을 읽을 수 없습니다.")
            return jsonify({"status": "error", "message": "엑셀 파일을 읽을 수 없습니다."})
        
        # 유효한 데이터 확인
        valid_df = df[
            df['keyword'].notna() & (df['keyword'].str.strip() != '') &
            df['product_id'].notna() & (df['product_id'].str.strip() != '')
        ]
        
        if len(valid_df) == 0:
            emit_log("검색할 유효한 데이터가 없습니다. 키워드와 상품 ID를 확인해주세요.")
            return jsonify({"status": "error", "message": "검색할 데이터가 없습니다."})
        
        # 검색 시작
        search_active = True
        emit_log(f"\n=== 검색 시작 (총 {len(valid_df)}개 항목) ===")
        
        # 초기 상태 업데이트
        first_keyword = str(valid_df.iloc[0]['keyword'])
        socketio.emit('search_status', {
            'status': 'initializing',
            'current': 0,
            'total': len(valid_df),
            'keyword': first_keyword,
            'message': f'검색 초기화 중... (총 {len(valid_df)}개 항목)'
        })
        
        # 백그라운드에서 검색 ���행
        eventlet.spawn(perform_search)
        return jsonify({"status": "success", "message": f"검색을 시작합니다. (총 {len(valid_df)}개 항목)"})
        
    except Exception as e:
        search_active = False
        error_msg = f"검색 시작 중 오류 발생: {str(e)}"
        emit_log(error_msg)
        socketio.emit('search_status', {
            'status': 'error',
            'current': 0,
            'total': 0,
            'keyword': '',
            'message': error_msg
        })
        return jsonify({"status": "error", "message": error_msg})

@app.route("/stop_search", methods=['POST'])
def stop_search():
    """검색 중지"""
    global search_active
    try:
        if not search_active:
            return jsonify({"status": "error", "message": "진행 중인 검색이 없습니다."})
            
        search_active = False
        socketio.emit('search_status', {'status': 'waiting'})
        emit_log("\n=== 검색이 중지되었습니다 ===")
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/get_logs", methods=['GET'])
def get_logs():
    """저장된 로그 파일 목록 반환"""
    try:
        log_files = []
        for file in os.listdir(LOG_DIR):
            if file.startswith('search_log_') and file.endswith('.txt'):
                log_files.append(file)
        return jsonify({"status": "success", "files": sorted(log_files, reverse=True)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/view_log/<filename>")
def view_log(filename):
    """특정 로그 파일 내용 표시"""
    try:
        log_path = os.path.join(LOG_DIR, filename)
        if os.path.exists(log_path):
            with open(log_path, 'r', encoding='utf-8') as f:
                logs = f.readlines()
            return render_template('log_viewer.html', 
                                logs=logs, 
                                filename=filename)
        return "로그 파일을 찾을 수 없습니다."
    except Exception as e:
        return str(e)

if __name__ == "__main__":
    try:
        # 검색 상태 초기화
        search_active = False
        
        print("""
--------------------------------
    Hello, Rank Search
--------------------------------
        """)
        
        # 스케줄러 시작
        start_scheduler()
        
        # SocketIO 서버 시작
        socketio.run(app, 
                    host='222.122.202.122', 
                    port=5000, 
                    debug=False,
                    use_reloader=False,
                    log_output=True)
    except Exception as e:
        print(f"프로그램 실행 중 오류 발생: {str(e)}")
    finally:
        search_active = False
        if scheduler.running:
            scheduler.shutdown()
