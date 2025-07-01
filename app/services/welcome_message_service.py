import json
import os
import random
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pathlib import Path

from app.services.llm_service import get_llm_response
from app.services.vector_db_service import get_all_documents
from app.config import settings
from app.utils.logging_config import get_logger
from app.utils.exceptions import LLMError

logger = get_logger(__name__)

class WelcomeMessageService:
    """환영메시지 생성 및 관리 서비스"""
    
    def __init__(self):
        self.data_dir = Path("data")
        self.data_dir.mkdir(exist_ok=True)
        self.welcome_messages_file = self.data_dir / "welcome_messages.json"
        self.max_messages = 20  # 최대 저장할 메시지 수
        
    def _load_messages(self) -> List[Dict[str, Any]]:
        """저장된 환영메시지들을 로드"""
        try:
            if self.welcome_messages_file.exists():
                with open(self.welcome_messages_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get('messages', [])
            return []
        except Exception as e:
            logger.error(f"환영메시지 로드 실패: {e}")
            return []
    
    def _save_messages(self, messages: List[Dict[str, Any]]) -> bool:
        """환영메시지들을 저장"""
        try:
            data = {
                'messages': messages,
                'last_updated': datetime.now().isoformat(),
                'version': '1.0'
            }
            with open(self.welcome_messages_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"환영메시지 {len(messages)}개 저장 완료")
            return True
        except Exception as e:
            logger.error(f"환영메시지 저장 실패: {e}")
            return False
    
    def get_document_summary(self) -> Dict[str, Any]:
        """현재 문서들의 요약 정보 생성"""
        try:
            documents = get_all_documents()
            
            if not documents:
                return {
                    'total_documents': 0,
                    'total_chunks': 0,
                    'main_topics': ['일반적인 질문'],
                    'content_keywords': []
                }
            
            # 문서 분석
            total_chunks = sum(doc.get("chunk_count", 0) for doc in documents)
            document_names = [doc.get("document_id", "") for doc in documents]
            
            # 문서 내용 기반 주제 추출 (파일명 대신 내용 분석)
            topics = set()
            content_keywords = set()
            
            # 실제 문서 내용에서 키워드 추출
            for doc in documents:
                preview_text = doc.get("first_chunk_preview", "").lower()
                if preview_text:
                    # 주조 관련 키워드
                    if any(keyword in preview_text for keyword in ["주물", "주조", "casting", "foundry", "용해", "응고"]):
                        topics.add("주조기술")
                        content_keywords.update(["주조", "주물", "용해"])
                    
                    # 품질관리 키워드
                    if any(keyword in preview_text for keyword in ["결함", "품질", "검사", "측정", "관리", "defect", "quality"]):
                        topics.add("품질관리")
                        content_keywords.update(["품질관리", "결함분석"])
                    
                    # 공정기술 키워드
                    if any(keyword in preview_text for keyword in ["공정", "제조", "가공", "process", "manufacturing"]):
                        topics.add("공정기술")
                        content_keywords.update(["제조공정", "가공기술"])
                    
                    # 설계 키워드
                    if any(keyword in preview_text for keyword in ["설계", "design", "모델링", "해석"]):
                        topics.add("설계기술")
                        content_keywords.update(["설계", "모델링"])
                    
                    # 재료 키워드
                    if any(keyword in preview_text for keyword in ["재료", "합금", "금속", "material", "alloy", "metal"]):
                        topics.add("재료공학")
                        content_keywords.update(["재료", "합금", "금속"])
                    
                    # 열처리 키워드
                    if any(keyword in preview_text for keyword in ["열처리", "어닐링", "템퍼링", "heat treatment"]):
                        topics.add("열처리")
                        content_keywords.update(["열처리", "금속처리"])
            
            # 파일명 기반 보조 분석 (내용이 부족할 때만)
            if not topics:
                for name in document_names:
                    if "주물" in name or "foundry" in name.lower():
                        topics.add("주조기술")
                    if "결함" in name:
                        topics.add("품질관리")
                    if "설계" in name:
                        topics.add("설계기술")
                    if "공정" in name:
                        topics.add("공정기술")
            
            if not topics:
                topics.add("기술문서")
            
            if not content_keywords:
                content_keywords.update(["기술", "공학", "제조"])
            
            return {
                'total_documents': len(documents),
                'total_chunks': total_chunks,
                'main_topics': list(topics),
                'content_keywords': list(content_keywords),
                'has_foundry_docs': any("주물" in topic or "주조" in topic for topic in topics)
            }
            
        except Exception as e:
            logger.error(f"문서 요약 생성 실패: {e}")
            return {
                'total_documents': 0,
                'total_chunks': 0,
                'document_types': [],
                'main_topics': ['일반적인 질문']
            }
    
    def _create_welcome_prompt(self, doc_summary: Dict[str, Any]) -> str:
        """환영메시지 생성을 위한 프롬프트 생성"""
        
        if doc_summary['total_documents'] == 0:
            return """아직 업로드된 문서가 없는 상황에서 사용자를 환영하는 메시지를 작성해주세요.
            
요구사항:
- 친근하고 전문적인 톤
- 문서 업로드를 유도하는 내용
- 한국어로 작성
- 50-80자 정도의 간결한 메시지
- 이모지 1-2개 포함

1개의 환영메시지만 작성해주세요."""

        topics_str = ", ".join(doc_summary['main_topics'])
        keywords_str = ", ".join(doc_summary.get('content_keywords', []))
        
        return f"""사용자가 챗봇과 대화를 시작할 때 보여줄 환영메시지를 작성해주세요.

현재 시스템 보유 지식:
- 총 {doc_summary['total_documents']}개의 전문 기술문서 보유
- 총 {doc_summary['total_chunks']}개의 검색 가능한 지식 청크
- 전문 분야: {topics_str}
- 핵심 키워드: {keywords_str}
- 주조 기술 전문성: {'보유' if doc_summary.get('has_foundry_docs') else '일반'}

요구사항:
- 친근하고 전문적인 톤으로 작성
- 핵심 키워드나 전문 분야를 자연스럽게 언급
- 사용자가 관련 질문을 하고 싶게 만드는 내용
- 절대 파일명이나 문서명(Vol., 기술총서 등)은 언급하지 말 것
- 기술 키워드와 전문 영역 중심으로 작성
- 한국어로 작성
- 50-100자 정도의 적절한 길이
- 이모지 1-2개 포함
- 매번 다른 스타일과 접근법 사용

예시 스타일 (키워드 중심):
1. "🔧 주조기술과 품질관리에 대한 궁금한 점이 있으시면 언제든 물어보세요!"
2. "⚙️ 재료공학과 열처리 분야의 전문 지식으로 도움드리겠습니다."
3. "🏭 제조공정 최적화와 결함분석 관련 질문을 기다리고 있어요!"
4. "🔬 금속가공과 설계기술에 대해 무엇이든 문의해 보세요!"

1개의 환영메시지만 작성해주세요. 다른 설명은 하지 마세요."""

    def generate_welcome_message(self) -> Optional[str]:
        """LLM을 사용하여 새로운 환영메시지 생성"""
        try:
            # 현재 문서 상황 파악
            doc_summary = self.get_document_summary()
            
            # LLM 프롬프트 생성
            prompt = self._create_welcome_prompt(doc_summary)
            
            # LLM으로 환영메시지 생성
            response = get_llm_response(
                prompt,
                model_name=settings.OLLAMA_DEFAULT_MODEL,  # 기본 모델 사용 (OCR 모델 대신)
                options={
                    "num_predict": 200,  # 짧은 응답
                    "temperature": 0.8,  # 창의적 응답
                    "top_p": 0.9
                }
            )
            
            if response and response.strip():
                # 응답 정리 (불필요한 따옴표나 설명 제거)
                welcome_msg = response.strip().strip('"').strip("'")
                
                # 너무 길면 자르기
                if len(welcome_msg) > 150:
                    welcome_msg = welcome_msg[:147] + "..."
                
                logger.info(f"새 환영메시지 생성: {welcome_msg[:50]}...")
                return welcome_msg
            else:
                logger.warning("LLM 응답이 비어있음")
                return None
                
        except Exception as e:
            logger.error(f"환영메시지 생성 실패: {e}")
            return None
    
    def add_new_message(self, message: str) -> bool:
        """새로운 환영메시지 추가"""
        try:
            messages = self._load_messages()
            
            # 중복 체크
            for existing in messages:
                if existing.get('message', '').strip() == message.strip():
                    logger.info("중복 메시지이므로 추가하지 않음")
                    return False
            
            # 새 메시지 추가
            new_message = {
                'id': len(messages) + 1,
                'message': message,
                'created_at': datetime.now().isoformat(),
                'document_count': len(get_all_documents()),
                'used_count': 0
            }
            
            messages.append(new_message)
            
            # 최대 개수 제한
            if len(messages) > self.max_messages:
                # 사용 횟수가 적은 것부터 제거
                messages.sort(key=lambda x: x.get('used_count', 0))
                messages = messages[-(self.max_messages):]
                
                # ID 재정렬
                for i, msg in enumerate(messages):
                    msg['id'] = i + 1
            
            return self._save_messages(messages)
            
        except Exception as e:
            logger.error(f"메시지 추가 실패: {e}")
            return False
    
    def get_random_message(self) -> Optional[str]:
        """랜덤 환영메시지 반환"""
        try:
            messages = self._load_messages()
            
            if not messages:
                # 기본 메시지들
                default_messages = [
                    "📚 안녕하세요! 업로드된 문서들에 대해 궁금한 것이 있으시면 언제든 물어보세요.",
                    "🤖 반갑습니다! 문서 기반 질답 시스템입니다. 어떤 도움이 필요하신가요?",
                    "💭 안녕하세요! 저장된 기술문서들을 바탕으로 상세한 답변을 드릴 수 있습니다."
                ]
                return random.choice(default_messages)
            
            # 가중치 기반 선택 (사용 횟수가 적은 것을 더 자주 선택)
            weights = []
            for msg in messages:
                used_count = msg.get('used_count', 0)
                # 사용 횟수가 적을수록 높은 가중치
                weight = max(1, 10 - used_count)
                weights.append(weight)
            
            # 가중치 기반 랜덤 선택
            selected_msg = random.choices(messages, weights=weights)[0]
            
            # 사용 횟수 증가
            selected_msg['used_count'] = selected_msg.get('used_count', 0) + 1
            self._save_messages(messages)
            
            return selected_msg['message']
            
        except Exception as e:
            logger.error(f"랜덤 메시지 선택 실패: {e}")
            return "📚 안녕하세요! 업로드된 문서들에 대해 궁금한 것이 있으시면 언제든 물어보세요."
    
    def generate_multiple_messages(self, count: int = 5) -> int:
        """여러 개의 환영메시지를 한번에 생성"""
        generated_count = 0
        
        for i in range(count):
            try:
                message = self.generate_welcome_message()
                if message:
                    success = self.add_new_message(message)
                    if success:
                        generated_count += 1
                
                # 요청 간격 (LLM 과부하 방지)
                import time
                time.sleep(2)
                
            except Exception as e:
                logger.error(f"메시지 생성 중 오류 (시도 {i+1}): {e}")
                continue
        
        logger.info(f"환영메시지 {generated_count}개 생성 완료")
        return generated_count
    
    def get_all_messages(self) -> List[Dict[str, Any]]:
        """모든 환영메시지 반환 (관리용)"""
        return self._load_messages()
    
    def delete_message(self, message_id: int) -> bool:
        """특정 환영메시지 삭제"""
        try:
            messages = self._load_messages()
            original_count = len(messages)
            
            messages = [msg for msg in messages if msg.get('id') != message_id]
            
            if len(messages) < original_count:
                # ID 재정렬
                for i, msg in enumerate(messages):
                    msg['id'] = i + 1
                
                self._save_messages(messages)
                logger.info(f"환영메시지 ID {message_id} 삭제 완료")
                return True
            else:
                logger.warning(f"삭제할 메시지 ID {message_id}를 찾을 수 없음")
                return False
                
        except Exception as e:
            logger.error(f"메시지 삭제 실패: {e}")
            return False

# 전역 서비스 인스턴스
welcome_service = WelcomeMessageService()

# 편의 함수들
def generate_welcome_messages(count: int = 5) -> int:
    """환영메시지 생성 (편의 함수)"""
    return welcome_service.generate_multiple_messages(count)

def get_random_welcome_message() -> str:
    """랜덤 환영메시지 조회 (편의 함수)"""
    return welcome_service.get_random_message()

def get_welcome_message_stats() -> Dict[str, Any]:
    """환영메시지 통계 (편의 함수)"""
    messages = welcome_service.get_all_messages()
    doc_summary = welcome_service.get_document_summary()
    
    return {
        'total_messages': len(messages),
        'recent_messages': messages[-3:] if messages else [],
        'document_summary': doc_summary,
        'last_generated': messages[-1].get('created_at') if messages else None
    }