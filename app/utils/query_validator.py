"""
Query validation utility for chat requests
"""
import re
from typing import Dict, List, Optional, Tuple
from app.utils.logging_config import get_logger

logger = get_logger(__name__)

class QueryValidator:
    """질문 유효성 검증 클래스"""
    
    # 최소/최대 길이 제한
    MIN_QUERY_LENGTH = 2
    MAX_QUERY_LENGTH = 2000
    
    # 유의미한 질문을 위한 최소 길이 (한국어 고려)
    MIN_MEANINGFUL_LENGTH = 2
    
    # 한국어 문자 패턴
    KOREAN_PATTERN = re.compile(r'[가-힣]')
    
    # 영어 문자 패턴  
    ENGLISH_PATTERN = re.compile(r'[a-zA-Z]')
    
    # 숫자 패턴
    NUMBER_PATTERN = re.compile(r'\d')
    
    # 의미없는 문자열 패턴 (키보드 무작위 입력 등)
    MEANINGLESS_PATTERNS = [
        re.compile(r'^[ㄱ-ㅎㅏ-ㅣ]+$'),  # 자음모음만
        re.compile(r'^[ㅇㅇ]+$'),         # ㅇㅇ만 반복
        re.compile(r'^[ㅎㅎ]+$'),         # ㅎㅎ만 반복
        re.compile(r'^[ㅋㅋ]+$'),         # ㅋㅋ만 반복
        re.compile(r'^[.,!?;:\s]+$'),     # 구두점과 공백만
        re.compile(r'^[12345]+$'),        # 숫자만 연속
        re.compile(r'^[qwerty]+$', re.IGNORECASE),  # 키보드 연속 타이핑
        re.compile(r'^[asdf]+$', re.IGNORECASE),
        re.compile(r'^[zxcv]+$', re.IGNORECASE),
    ]
    
    # 의미있는 키워드 패턴 (주조 기술 관련)
    FOUNDRY_KEYWORDS = [
        '주물', '주조', '용해', '주형', '탕구', '용탕', '응고', '결함',
        '사형', '금형', '다이캐스팅', '인베스트먼트', '셸몰드',
        '철', '알루미늄', '구리', '아연', '마그네슘', '합금',
        '온도', '압력', '속도', '시간', '품질', '검사', '측정',
        '기공', '수축', '크랙', '편석', '조직', '경도', '강도',
        'casting', 'foundry', 'molten', 'mold', 'defect',
        'temperature', 'alloy', 'metal', 'iron', 'aluminum'
    ]
    
    @classmethod
    def validate_query(cls, query: str) -> Dict[str, any]:
        """
        질문의 유효성을 종합적으로 검증
        
        Args:
            query: 사용자 질문
            
        Returns:
            Dict: 검증 결과 {'is_valid': bool, 'error_type': str, 'suggestion': str}
        """
        if not query:
            return {
                'is_valid': False,
                'error_type': 'empty_query',
                'suggestion': '질문을 입력해주세요.'
            }
        
        # 공백 제거 후 재검증
        clean_query = query.strip()
        if not clean_query:
            return {
                'is_valid': False,
                'error_type': 'empty_query',
                'suggestion': '공백이 아닌 질문을 입력해주세요.'
            }
        
        # 길이 검증
        if len(clean_query) < cls.MIN_QUERY_LENGTH:
            return {
                'is_valid': False,
                'error_type': 'too_short',
                'suggestion': f'질문이 너무 짧습니다. 최소 {cls.MIN_QUERY_LENGTH}자 이상 입력해주세요.'
            }
        
        if len(clean_query) > cls.MAX_QUERY_LENGTH:
            return {
                'is_valid': False,
                'error_type': 'too_long',
                'suggestion': f'질문이 너무 깁니다. 최대 {cls.MAX_QUERY_LENGTH}자 이하로 입력해주세요.'
            }
        
        # 의미없는 패턴 검사
        for pattern in cls.MEANINGLESS_PATTERNS:
            if pattern.match(clean_query):
                return {
                    'is_valid': False,
                    'error_type': 'meaningless_input',
                    'suggestion': '의미있는 질문을 입력해주세요. 예: "주물 결함의 종류가 뭐야?", "알루미늄 주조 온도는?"'
                }
        
        # 의미있는 내용 검증 (주조 기술 키워드는 예외 처리)
        if len(clean_query) < cls.MIN_MEANINGFUL_LENGTH:
            # 주조 기술 관련 핵심 키워드는 허용
            foundry_core_keywords = ['주조', '주물', '용해', '탕구', '결함', 'casting', 'foundry']
            is_core_keyword = any(keyword in clean_query.lower() for keyword in foundry_core_keywords)
            
            if not is_core_keyword:
                return {
                    'is_valid': False,
                    'error_type': 'too_short_meaningful',
                    'suggestion': '더 구체적인 질문을 해주세요. 예: "주조 온도는?", "결함 종류는?"'
                }
        
        # 문자 구성 검증 (완전히 무의미한 입력 방지)
        has_korean = bool(cls.KOREAN_PATTERN.search(clean_query))
        has_english = bool(cls.ENGLISH_PATTERN.search(clean_query))
        has_number = bool(cls.NUMBER_PATTERN.search(clean_query))
        
        # 최소한 한국어 또는 영어가 포함되어야 함
        if not has_korean and not has_english:
            return {
                'is_valid': False,
                'error_type': 'no_meaningful_text',
                'suggestion': '한국어 또는 영어로 질문을 입력해주세요.'
            }
        
        # 유효한 질문으로 판단
        return {
            'is_valid': True,
            'error_type': None,
            'suggestion': None
        }
    
    @classmethod
    def is_foundry_related(cls, query: str) -> bool:
        """
        질문이 주조 기술과 관련있는지 검사
        
        Args:
            query: 사용자 질문
            
        Returns:
            bool: 주조 기술 관련 여부
        """
        query_lower = query.lower()
        
        for keyword in cls.FOUNDRY_KEYWORDS:
            if keyword.lower() in query_lower:
                return True
        
        return False
    
    @classmethod
    def get_query_suggestions(cls, query: str) -> List[str]:
        """
        질문과 관련된 제안사항 생성
        
        Args:
            query: 사용자 질문
            
        Returns:
            List[str]: 제안 질문 목록
        """
        suggestions = []
        
        # 기본 제안 질문들
        basic_suggestions = [
            "주물 결함의 주요 종류는 무엇인가요?",
            "알루미늄 주조 시 적정 온도는?",
            "사형 주조와 금형 주조의 차이점은?",
            "주조품의 품질 검사 방법은?",
            "용탕 처리 방법에 대해 설명해주세요"
        ]
        
        # 질문이 너무 짧거나 의미없는 경우 기본 제안
        validation = cls.validate_query(query)
        if not validation['is_valid']:
            return basic_suggestions
        
        # 키워드 기반 맞춤 제안
        query_lower = query.lower()
        
        if any(kw in query_lower for kw in ['결함', 'defect']):
            suggestions.extend([
                "주물 결함의 종류와 원인은?",
                "기공 결함을 방지하는 방법은?",
                "수축 결함이 발생하는 이유는?"
            ])
        
        if any(kw in query_lower for kw in ['온도', 'temperature']):
            suggestions.extend([
                "주조 온도 설정 기준은?",
                "용해 온도와 주입 온도의 차이는?",
                "온도가 주조품 품질에 미치는 영향은?"
            ])
        
        if any(kw in query_lower for kw in ['알루미늄', 'aluminum']):
            suggestions.extend([
                "알루미늄 합금의 특성은?",
                "알루미늄 주조 시 주의사항은?",
                "알루미늄 용해 온도는?"
            ])
        
        return suggestions[:5] if suggestions else basic_suggestions
    
    @classmethod
    def enhance_query_for_search(cls, query: str) -> str:
        """
        검색을 위해 질문을 향상시킴
        
        Args:
            query: 원본 질문
            
        Returns:
            str: 향상된 질문
        """
        # 기본 정제
        enhanced = query.strip()
        
        # 질문 부호 정규화
        if not enhanced.endswith(('?', '？')):
            enhanced += "?"
        
        # 주조 관련이 아닌 경우 컨텍스트 추가
        if not cls.is_foundry_related(enhanced):
            enhanced = f"주조 기술에서 {enhanced}"
        
        return enhanced