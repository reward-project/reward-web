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

# -*- coding: utf-8 -*-
print("안녕하세요")  # Non-ASCII 문자 (한글)

def smooth_scroll(browser):
    """사람처럼 천천히 스크롤하는 함수"""
    # 현재 화면 높이 가져오기
    screen_height = browser.execute_script("return window.innerHeight")
    # 전체 문서 높이 가져오기
    total_height = browser.execute_script("return document.body.scrollHeight")
    
    current_scroll = 0
    scroll_increment = screen_height // 4  # 한 번에 스크롤할 높이 (화면 높이의 1/4)
    
    print("페이지 스크롤 중...")
    while current_scroll < total_height:
        # 다음 스크롤 위치 계산
        next_scroll = min(current_scroll + scroll_increment, total_height)
        
        # 부드럽게 스크롤
        browser.execute_script(f"window.scrollTo({current_scroll}, {next_scroll});")
        current_scroll = next_scroll
        
        # 랜덤한 시간 대기 (0.5~1.5초)
        time.sleep(random.uniform(0.5, 1.5))
        
        # 새로운 컨텐츠 로딩을 위한 대기
        new_height = browser.execute_script("return document.body.scrollHeight")
        if new_height > total_height:
            total_height = new_height

def analyze_page(soup, page, product_id):
    """페이지 내용을 분석하여 상품 찾기"""
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
        
        # 목표 상품 확인
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
        
        # ChromeDriver 자동 설치 및 서비스 설정
        service = Service(ChromeDriverManager().install())
        browser = webdriver.Chrome(service=service, options=options)
        browser.maximize_window()
        
        encoded_keyword = urllib.parse.quote(keyword)
        base_url = f'https://www.coupang.com/np/search?component=&q={encoded_keyword}&channel=user'
        
        page = 1
        rank = 0
        
        while page <= 27:
            try:
                print(f"\n{page}페이지 검색 중...")
                url = f"{base_url}&page={page}"
                
                # 페이지 로드 전 쿠키 삭제
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
                    return result
                
                # 다음 페이지로 이동
                if page < 27:
                    try:
                        # 페재 페이지의 상품들 확인
                        products = browser.find_elements(By.CSS_SELECTOR, ".search-product:not(.search-product__ad-label)")
                        
                        # 마지막 상품으로 스크롤
                        if products:
                            browser.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", products[-1])
                            time.sleep(2)
                        
                        # 다음 페이지 버튼 찾기
                        next_page = WebDriverWait(browser, 10).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, f"a.page-link[href*='page={page + 1}']"))
                        )
                        
                        # 버튼이 보이도록 스크롤
                        browser.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", next_page)
                        time.sleep(2)
                        
                        # 클릭
                        next_page.click()
                        time.sleep(3)
                        
                    except Exception as e:
                        print(f"페이지 이동 중 오류: {str(e)}")
                        page += 1
                        continue
                
                page += 1
                
            except Exception as e:
                print(f"페이지 {page} 검색 중 오류: {str(e)}")
                # 브라우저 재시작
                if browser:
                    browser.quit()
                browser = webdriver.Chrome(service=service, options=options)
                browser.maximize_window()
                time.sleep(3)
                continue
        
        print("\n[검색 결과]")
        print(f"키워드: {keyword}")
        print(f"상품 ID: {product_id}")
        print("해당 상품을 찾을 수 없습니다. (27페이지 내)")
        return None
        
    except Exception as e:
        print(f"검색 중 오류 발생: {str(e)}")
        return None
        
    finally:
        if browser:
            browser.quit()

def main():
    try:
        # coupang_rank.xlsx 파일 읽기
        rank_df = pd.read_excel('coupang_rank.xlsx')
        print("\n=== coupang_rank.xlsx 파일 읽기 성공 ===")
        
        # number 순서대로 정렬
        rank_df = rank_df.sort_values('number')
        
        # 각 행에 대해 검색 수행
        for index, row in rank_df.iterrows():
            keyword = str(row['keyword'])
            product_id = str(row['product_id'])
            
            print(f"\n=== 검색 {row['number']} 시작 ===")
            print(f"키워드: {keyword}")
            print(f"상품 ID: {product_id}")
            
            # 검색 수행
            result = search_product(keyword, product_id)
            
            if result:
                # 결과 업데이트
                rank_df.loc[index, 'date'] = datetime.now().strftime('%Y-%m-%d')
                rank_df.loc[index, 'time'] = datetime.now().strftime('%H:%M:%S')
                rank_df.loc[index, 'page'] = result['page']
                
                # 광고/일반 순위 구분
                if result['rank_type'] == 'ad':
                    rank_df.loc[index, 'rank'] = None
                    rank_df.loc[index, 'ad'] = 'O'  # 광고인 경우 'O'로 표시
                else:
                    rank_df.loc[index, 'rank'] = float(result['rank'])
                    rank_df.loc[index, 'ad'] = None
                
                # 전체 페이지 순위 저장
                rank_df.loc[index, 'page_rank'] = float(result['page_rank'])
                
                # 중간 결과 저장
                rank_df.to_excel('coupang_rank.xlsx', index=False)
                print("\n검색이 완료되었습니다.")
                print(f"시간: {rank_df.loc[index, 'time']}")
                print(f"페이지: {result['page']}")
                if result['rank_type'] == 'ad':
                    print(f"광고 상품 (순위: {result['rank']})")
                else:
                    print(f"일반 순위: {result['rank']}")
                print(f"전체 페이지 순위: {result['page_rank']}")
                
                # 다음 검색 전 대기
                time.sleep(3)
        
        print("\n모든 검색이 완료되었습니다.")
            
    except Exception as e:
        print(f"프로그램 실행 중 오류 발생: {str(e)}")

if __name__ == "__main__":
    main()
