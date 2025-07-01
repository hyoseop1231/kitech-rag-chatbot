"""
Fallback response service for handling queries without search results
"""
from typing import Dict, List, Optional
import re
from app.utils.logging_config import get_logger
from app.utils.query_validator import QueryValidator

logger = get_logger(__name__)

class FallbackResponseService:
    """검색 결과가 없을 때의 대체 응답 서비스"""
    
    @classmethod
    def generate_no_results_response(cls, user_query: str, document_ids: List[str] = None) -> Dict[str, any]:
        """
        검색 결과가 없을 때 사용자 친화적인 응답 생성
        
        Args:
            user_query: 사용자 질문
            document_ids: 검색 대상 문서 ID 목록
            
        Returns:
            Dict: 응답 데이터
        """
        # 질문 분석
        is_foundry_related = QueryValidator.is_foundry_related(user_query)
        suggestions = QueryValidator.get_query_suggestions(user_query)
        
        # 기본 응답 메시지
        if document_ids and len(document_ids) > 0:
            # 특정 문서에서 검색한 경우
            response_text = cls._generate_document_specific_response(user_query, document_ids, is_foundry_related)
        else:
            # 전체 문서에서 검색한 경우
            response_text = cls._generate_general_response(user_query, is_foundry_related)
        
        # 제안 질문 추가
        if suggestions:
            response_text += "\n\n## 💡 이런 질문은 어떠세요?\n"
            for i, suggestion in enumerate(suggestions[:3], 1):
                response_text += f"{i}. {suggestion}\n"
        
        # 도움말 추가
        response_text += cls._get_help_section()
        
        return {
            'response': response_text,
            'suggestions': suggestions,
            'is_foundry_related': is_foundry_related,
            'has_results': False
        }
    
    @classmethod
    def _generate_document_specific_response(cls, query: str, document_ids: List[str], is_foundry_related: bool) -> str:
        """특정 문서 대상 검색 실패 시 응답"""
        doc_count = len(document_ids)
        doc_info = f"{doc_count}개 문서" if doc_count > 1 else "해당 문서"
        
        if is_foundry_related:
            return f"""죄송합니다. **"{query}"**에 대한 정보를 {doc_info}에서 찾을 수 없습니다.

다음과 같은 이유일 수 있습니다:
- 해당 문서에 관련 내용이 포함되어 있지 않음
- 다른 용어나 표현으로 기술되어 있을 수 있음
- 문서가 아직 완전히 처리되지 않았을 수 있음

**다른 방법으로 시도해보세요:**
- 비슷한 의미의 다른 단어로 질문해보세요
- 더 일반적인 용어로 질문해보세요
- 전체 문서에서 검색해보세요 (문서 선택 해제)"""
        else:
            return f"""**"{query}"**에 대한 정보를 {doc_info}에서 찾을 수 없습니다.

이 시스템은 **주조(주물) 기술** 전문 자료를 다루고 있습니다. 
주조 기술과 관련된 질문을 해주시면 더 정확한 답변을 드릴 수 있습니다."""
    
    @classmethod
    def _generate_general_response(cls, query: str, is_foundry_related: bool) -> str:
        """전체 문서 대상 검색 실패 시 응답"""
        if is_foundry_related:
            return f"""죄송합니다. **"{query}"**에 대한 정보를 현재 업로드된 문서에서 찾을 수 없습니다.

**가능한 원인:**
- 해당 주제에 대한 문서가 아직 업로드되지 않았습니다
- 다른 전문 용어로 표현되어 있을 수 있습니다
- 문서 처리가 완료되지 않았을 수 있습니다

**해결 방법:**
1. **다른 표현으로 시도**: 비슷한 의미의 용어 사용
2. **더 일반적인 질문**: 구체적인 질문보다 범용적인 질문
3. **관련 문서 업로드**: 해당 주제의 PDF 문서 추가"""
        else:
            return f"""**"{query}"**에 대한 정보를 찾을 수 없습니다.

이 시스템은 **주조(주물) 기술 전문 자료**를 다루고 있습니다.

**주조 기술 관련 질문 예시:**
- 주물 결함의 종류와 대책
- 알루미늄 주조 공정
- 용해로 관리 방법
- 주형 설계 원리
- 품질 검사 기법"""
    
    @classmethod
    def _get_help_section(cls) -> str:
        """도움말 섹션 생성"""
        return """

## 📋 시스템 사용 가이드

**효과적인 질문 방법:**
- 구체적이고 명확한 질문을 해주세요
- 주조 기술 전문 용어를 사용해주세요  
- 너무 짧거나 애매한 질문은 피해주세요

**문제가 계속되면:**
- 관련 PDF 문서를 먼저 업로드해주세요
- 시스템 관리자에게 문의하세요"""
    
    @classmethod
    def enhance_poor_results_response(cls, response: str, query: str, result_count: int) -> str:
        """
        검색 결과가 적거나 품질이 낮을 때 응답 향상
        
        Args:
            response: 기존 LLM 응답
            query: 사용자 질문
            result_count: 검색된 결과 수
            
        Returns:
            str: 향상된 응답
        """
        # 응답이 너무 짧거나 불완전한 경우
        if len(response.strip()) < 50 or "정보가 부족" in response or "찾을 수 없" in response:
            suggestions = QueryValidator.get_query_suggestions(query)
            
            enhanced_response = response + f"""

## ⚠️ 제한된 정보 안내
현재 검색된 정보가 제한적입니다 ({result_count}개 결과).
더 자세한 정보를 위해 다음을 시도해보세요:

**추천 질문:**"""
            
            for i, suggestion in enumerate(suggestions[:2], 1):
                enhanced_response += f"\n{i}. {suggestion}"
            
            enhanced_response += "\n\n**또는 관련 문서를 추가로 업로드해주세요.**"
            
            return enhanced_response
        
        return response
    
    @classmethod
    def generate_error_response(cls, error_type: str, error_message: str, query: str) -> str:
        """
        시스템 오류 시 사용자 친화적 응답 생성
        
        Args:
            error_type: 오류 유형
            error_message: 오류 메시지
            query: 사용자 질문
            
        Returns:
            str: 사용자 친화적 오류 응답
        """
        base_message = f"""죄송합니다. 질문 "**{query}**" 처리 중 문제가 발생했습니다.

**오류 상황:** {error_type}"""
        
        if "embedding" in error_type.lower():
            base_message += """

**가능한 원인:**
- 네트워크 연결 문제
- 임베딩 모델 로딩 실패

**해결 방법:**
- 잠시 후 다시 시도해주세요
- 질문을 더 간단하게 바꿔보세요"""
        
        elif "llm" in error_type.lower():
            base_message += """

**가능한 원인:**
- AI 모델 서버 연결 문제
- 모델 응답 생성 실패

**해결 방법:**
- 잠시 후 다시 시도해주세요
- 더 간단한 질문으로 시도해보세요"""
        
        elif "vector" in error_type.lower():
            base_message += """

**가능한 원인:**
- 데이터베이스 연결 문제
- 검색 인덱스 오류

**해결 방법:**
- 페이지를 새로고침해주세요
- 다른 질문으로 시도해보세요"""
        
        else:
            base_message += """

**해결 방법:**
- 페이지를 새로고침하고 다시 시도해주세요
- 문제가 지속되면 관리자에게 문의하세요"""
        
        # 기본 제안 질문 추가
        suggestions = QueryValidator.get_query_suggestions(query)
        if suggestions:
            base_message += "\n\n**임시로 이런 질문을 시도해보세요:**"
            for i, suggestion in enumerate(suggestions[:2], 1):
                base_message += f"\n{i}. {suggestion}"
        
        return base_message