#!/usr/bin/env python3
"""
Simple validation test without dependencies
"""
import re
from typing import Dict, List

# 간소화된 QueryValidator 로직 테스트
class SimpleQueryValidator:
    MIN_QUERY_LENGTH = 2
    MIN_MEANINGFUL_LENGTH = 2
    
    KOREAN_PATTERN = re.compile(r'[가-힣]')
    ENGLISH_PATTERN = re.compile(r'[a-zA-Z]')
    
    MEANINGLESS_PATTERNS = [
        re.compile(r'^[ㄱ-ㅎㅏ-ㅣ]+$'),
        re.compile(r'^[ㅇㅇ]+$'),
        re.compile(r'^[ㅎㅎ]+$'),
        re.compile(r'^[ㅋㅋ]+$'),
        re.compile(r'^[.,!?;:\s]+$'),
        re.compile(r'^[12345]+$'),
        re.compile(r'^[qwerty]+$', re.IGNORECASE),
    ]
    
    @classmethod
    def validate_query(cls, query: str) -> Dict[str, any]:
        if not query:
            return {'is_valid': False, 'error_type': 'empty_query'}
        
        clean_query = query.strip()
        if not clean_query:
            return {'is_valid': False, 'error_type': 'empty_query'}
        
        if len(clean_query) < cls.MIN_QUERY_LENGTH:
            return {'is_valid': False, 'error_type': 'too_short'}
        
        for pattern in cls.MEANINGLESS_PATTERNS:
            if pattern.match(clean_query):
                return {'is_valid': False, 'error_type': 'meaningless_input'}
        
        if len(clean_query) < cls.MIN_MEANINGFUL_LENGTH:
            # 주조 기술 관련 핵심 키워드는 허용
            foundry_core_keywords = ['주조', '주물', '용해', '탕구', '결함', 'casting', 'foundry']
            is_core_keyword = any(keyword in clean_query.lower() for keyword in foundry_core_keywords)
            
            if not is_core_keyword:
                return {'is_valid': False, 'error_type': 'too_short_meaningful'}
        
        has_korean = bool(cls.KOREAN_PATTERN.search(clean_query))
        has_english = bool(cls.ENGLISH_PATTERN.search(clean_query))
        
        if not has_korean and not has_english:
            return {'is_valid': False, 'error_type': 'no_meaningful_text'}
        
        return {'is_valid': True, 'error_type': None}

def test_validation():
    print("=== 질문 검증 로직 테스트 ===\n")
    
    # 테스트 케이스
    test_cases = [
        # (질문, 예상결과, 설명)
        ("주물 결함의 종류는?", True, "유효한 주조 관련 질문"),
        ("알루미늄 주조 온도", True, "유효한 기술 질문"),
        ("What is casting?", True, "유효한 영어 질문"),
        ("", False, "빈 질문"),
        ("   ", False, "공백만 있는 질문"),
        ("ㅇㅇ", False, "의미없는 자음모음"),
        ("ㅋㅋㅋ", False, "웃음 표현"),
        ("12345", False, "숫자만"),
        ("a", False, "너무 짧은 질문"),
        ("??", False, "구두점만"),
        ("qwerty", False, "키보드 무작위 입력"),
        ("주조", True, "짧지만 의미있는 질문"),
        ("온도는?", True, "간단하지만 유효한 질문"),
    ]
    
    success_count = 0
    total_count = len(test_cases)
    
    for query, expected, description in test_cases:
        result = SimpleQueryValidator.validate_query(query)
        actual = result['is_valid']
        
        status = "✓" if actual == expected else "✗"
        print(f"{status} 테스트: {description}")
        print(f"   질문: '{query}'")
        print(f"   예상: {expected}, 실제: {actual}")
        
        if actual != expected:
            print(f"   오류 타입: {result.get('error_type', 'None')}")
        else:
            success_count += 1
        print()
    
    print(f"=== 테스트 결과 ===")
    print(f"성공: {success_count}/{total_count}")
    print(f"성공률: {(success_count/total_count)*100:.1f}%")
    
    if success_count == total_count:
        print("🎉 모든 테스트 통과!")
    else:
        print("⚠️  일부 테스트 실패")

def test_fallback_responses():
    print("\n=== 대체 응답 로직 테스트 ===\n")
    
    # 간단한 대체 응답 생성 함수
    def generate_simple_fallback(query: str, has_results: bool = False) -> str:
        if not has_results:
            if any(keyword in query.lower() for keyword in ['주물', '주조', '용해', 'casting']):
                return f"'{query}'에 대한 주조 기술 정보를 찾을 수 없습니다. 다른 용어로 시도해보세요."
            else:
                return f"이 시스템은 주조 기술 전문 자료를 다룹니다. 주조 관련 질문을 해주세요."
        return "정상 응답"
    
    test_queries = [
        ("주물 결함 종류", "주조 관련 질문"),
        ("알루미늄 용해", "주조 관련 질문"),
        ("오늘 날씨", "비주조 관련 질문"),
        ("파이썬 프로그래밍", "비주조 관련 질문"),
    ]
    
    for query, description in test_queries:
        response = generate_simple_fallback(query, False)
        print(f"📝 {description}: '{query}'")
        print(f"   응답: {response}")
        print()

if __name__ == "__main__":
    test_validation()
    test_fallback_responses()
    print("테스트 완료! 🚀")