"""
Tests for query validation functionality
"""
import pytest
from app.utils.query_validator import QueryValidator
from app.services.fallback_response_service import FallbackResponseService

class TestQueryValidator:
    """QueryValidator 클래스 테스트"""
    
    def test_valid_queries(self):
        """유효한 질문들 테스트"""
        valid_queries = [
            "주물 결함의 종류는 무엇인가요?",
            "알루미늄 주조 온도는?",
            "What is casting defect?",
            "주조 공정에 대해 설명해주세요",
            "용해로 관리 방법",
            "How to prevent porosity in casting?",
            "철강 주물의 특성과 용도는?"
        ]
        
        for query in valid_queries:
            result = QueryValidator.validate_query(query)
            assert result['is_valid'] == True, f"Query should be valid: {query}"
            assert result['error_type'] is None
            assert result['suggestion'] is None
    
    def test_empty_queries(self):
        """빈 질문들 테스트"""
        empty_queries = [
            "",
            "   ",
            "\t\n",
            None
        ]
        
        for query in empty_queries:
            if query is not None:
                result = QueryValidator.validate_query(query)
                assert result['is_valid'] == False
                assert result['error_type'] == 'empty_query'
                assert '질문을 입력해주세요' in result['suggestion']
    
    def test_too_short_queries(self):
        """너무 짧은 질문들 테스트"""
        short_queries = [
            "a",
            "ㄱ",
            "1",
            "?"
        ]
        
        for query in short_queries:
            result = QueryValidator.validate_query(query)
            assert result['is_valid'] == False
            assert result['error_type'] in ['too_short', 'too_short_meaningful']
    
    def test_meaningless_queries(self):
        """의미없는 질문들 테스트"""
        meaningless_queries = [
            "ㅇㅇ",
            "ㅎㅎㅎ",
            "ㅋㅋㅋㅋ",
            "ㄱㄴㄷㄹ",
            "...",
            "!!!!!",
            "12345",
            "qwerty",
            "asdf",
            "ㅇㅇㅇㅇㅇ"
        ]
        
        for query in meaningless_queries:
            result = QueryValidator.validate_query(query)
            assert result['is_valid'] == False
            assert result['error_type'] == 'meaningless_input'
            assert '의미있는 질문' in result['suggestion']
    
    def test_too_long_queries(self):
        """너무 긴 질문 테스트"""
        long_query = "매우 " * 500  # 1000자 이상
        result = QueryValidator.validate_query(long_query)
        assert result['is_valid'] == False
        assert result['error_type'] == 'too_long'
    
    def test_no_meaningful_text(self):
        """의미있는 텍스트가 없는 질문들 테스트"""
        no_text_queries = [
            "123456789",
            "!@#$%^&*()",
            "。。。。",
            "？？？"
        ]
        
        for query in no_text_queries:
            result = QueryValidator.validate_query(query)
            assert result['is_valid'] == False
            assert result['error_type'] == 'no_meaningful_text'
    
    def test_foundry_related_detection(self):
        """주조 기술 관련 질문 감지 테스트"""
        foundry_queries = [
            "주물 결함이 뭐야?",
            "알루미늄 주조 공정",
            "casting defect types",
            "용해로 관리",
            "주형 설계 방법",
            "foundry technology"
        ]
        
        non_foundry_queries = [
            "오늘 날씨는?",
            "파이썬 프로그래밍",
            "요리 레시피",
            "영화 추천"
        ]
        
        for query in foundry_queries:
            assert QueryValidator.is_foundry_related(query) == True, f"Should be foundry related: {query}"
        
        for query in non_foundry_queries:
            assert QueryValidator.is_foundry_related(query) == False, f"Should not be foundry related: {query}"
    
    def test_query_enhancement(self):
        """질문 향상 테스트"""
        test_cases = [
            ("결함 종류", "주조 기술에서 결함 종류?"),
            ("온도는", "주조 기술에서 온도는?"),
            ("주물 결함이 뭐야", "주물 결함이 뭐야?"),
            ("What is casting", "What is casting?")
        ]
        
        for original, expected_pattern in test_cases:
            enhanced = QueryValidator.enhance_query_for_search(original)
            if "주조 기술" in expected_pattern:
                assert "주조 기술" in enhanced
            assert enhanced.endswith("?")
    
    def test_get_suggestions(self):
        """질문 제안 테스트"""
        # 결함 관련 질문
        suggestions = QueryValidator.get_query_suggestions("결함")
        assert len(suggestions) > 0
        assert any("결함" in s for s in suggestions)
        
        # 온도 관련 질문
        suggestions = QueryValidator.get_query_suggestions("온도")
        assert len(suggestions) > 0
        assert any("온도" in s for s in suggestions)
        
        # 무효한 질문
        suggestions = QueryValidator.get_query_suggestions("ㅇㅇ")
        assert len(suggestions) > 0
        # 기본 제안이 반환되어야 함

class TestFallbackResponseService:
    """FallbackResponseService 테스트"""
    
    def test_no_results_response_foundry_related(self):
        """주조 관련 질문의 검색 결과 없음 응답 테스트"""
        query = "주물 결함의 종류는?"
        response = FallbackResponseService.generate_no_results_response(query)
        
        assert response['has_results'] == False
        assert response['is_foundry_related'] == True
        assert query in response['response']
        assert len(response['suggestions']) > 0
    
    def test_no_results_response_non_foundry(self):
        """비주조 관련 질문의 검색 결과 없음 응답 테스트"""
        query = "오늘 날씨는?"
        response = FallbackResponseService.generate_no_results_response(query)
        
        assert response['has_results'] == False
        assert response['is_foundry_related'] == False
        assert "주조(주물) 기술" in response['response']
    
    def test_document_specific_response(self):
        """특정 문서 대상 검색 실패 응답 테스트"""
        query = "알루미늄 주조 방법"
        document_ids = ["doc1", "doc2"]
        response = FallbackResponseService.generate_no_results_response(query, document_ids)
        
        assert "2개 문서" in response['response']
        assert "전체 문서에서 검색" in response['response']
    
    def test_enhance_poor_results(self):
        """부족한 검색 결과 응답 향상 테스트"""
        poor_response = "정보가 부족합니다"
        query = "주물 결함"
        result_count = 1
        
        enhanced = FallbackResponseService.enhance_poor_results_response(
            poor_response, query, result_count
        )
        
        assert len(enhanced) > len(poor_response)
        assert "제한된 정보" in enhanced
        assert str(result_count) in enhanced
    
    def test_error_response_generation(self):
        """에러 응답 생성 테스트"""
        error_types = [
            ("embedding_error", "임베딩 모델"),
            ("llm_error", "AI 모델 서버"),
            ("vector_error", "데이터베이스"),
            ("unknown_error", "새로고침")
        ]
        
        query = "테스트 질문"
        
        for error_type, expected_content in error_types:
            response = FallbackResponseService.generate_error_response(
                error_type, "Test error", query
            )
            
            assert query in response
            assert expected_content in response
            assert "해결 방법" in response

class TestIntegration:
    """통합 테스트"""
    
    def test_validation_flow(self):
        """전체 검증 플로우 테스트"""
        # 1. 유효하지 않은 질문
        invalid_query = "ㅇㅇ"
        validation = QueryValidator.validate_query(invalid_query)
        assert validation['is_valid'] == False
        
        suggestions = QueryValidator.get_query_suggestions(invalid_query)
        assert len(suggestions) > 0
        
        # 2. 유효하지만 검색 결과 없는 질문
        valid_query = "존재하지 않는 특수 주조 기법"
        validation = QueryValidator.validate_query(valid_query)
        assert validation['is_valid'] == True
        
        response = FallbackResponseService.generate_no_results_response(valid_query)
        assert response['has_results'] == False
        assert len(response['suggestions']) > 0
    
    def test_error_handling_flow(self):
        """에러 처리 플로우 테스트"""
        query = "정상 질문"
        
        # 다양한 에러 상황 시뮬레이션
        error_scenarios = [
            "embedding_error",
            "llm_error", 
            "vector_error",
            "system_error"
        ]
        
        for error_type in error_scenarios:
            response = FallbackResponseService.generate_error_response(
                error_type, "Simulated error", query
            )
            
            assert isinstance(response, str)
            assert len(response) > 100  # 충분한 정보 제공
            assert query in response
            assert "해결 방법" in response

if __name__ == "__main__":
    pytest.main([__file__, "-v"])