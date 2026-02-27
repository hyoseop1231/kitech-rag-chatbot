"""
Welcome router — 환영 메시지 관리
"""
from fastapi import APIRouter, HTTPException
from datetime import datetime

from app.utils.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.get("/welcome-messages/stats")
async def get_welcome_message_stats():
    """환영메시지 통계 정보를 반환합니다."""
    try:
        from app.services.welcome_message_service import get_welcome_message_stats
        
        stats = get_welcome_message_stats()
        return stats
        
    except Exception as e:
        logger.error(f"Error getting welcome message stats: {e}")
        raise HTTPException(status_code=500, detail="환영메시지 통계 조회 중 오류가 발생했습니다")


@router.get("/welcome-messages/random")
async def get_random_welcome_message():
    """랜덤 환영메시지를 반환합니다."""
    try:
        from app.services.welcome_message_service import get_random_welcome_message
        
        message = get_random_welcome_message()
        return {
            "message": message,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error getting random welcome message: {e}")
        return {
            "message": "📚 안녕하세요! 업로드된 문서들에 대해 궁금한 것이 있으시면 언제든 물어보세요.",
            "timestamp": datetime.now().isoformat()
        }


@router.post("/welcome-messages/generate")
async def generate_welcome_messages(count: int = 5):
    """새로운 환영메시지들을 생성합니다."""
    try:
        from app.services.welcome_message_service import generate_welcome_messages
        
        if count < 1 or count > 10:
            raise HTTPException(status_code=400, detail="생성할 메시지 개수는 1-10개 사이여야 합니다")
        
        generated_count = generate_welcome_messages(count)
        
        return {
            "requested_count": count,
            "generated_count": generated_count,
            "message": f"환영메시지 {generated_count}개가 성공적으로 생성되었습니다.",
            "timestamp": datetime.now().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating welcome messages: {e}")
        raise HTTPException(status_code=500, detail=f"환영메시지 생성 중 오류가 발생했습니다: {str(e)}")


@router.get("/welcome-messages")
async def get_all_welcome_messages():
    """모든 환영메시지 목록을 반환합니다."""
    try:
        from app.services.welcome_message_service import welcome_service
        
        messages = welcome_service.get_all_messages()
        doc_summary = welcome_service.get_document_summary()
        
        return {
            "messages": messages,
            "total_count": len(messages),
            "document_summary": doc_summary,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error getting welcome messages: {e}")
        raise HTTPException(status_code=500, detail="환영메시지 목록 조회 중 오류가 발생했습니다")


@router.delete("/welcome-messages/{message_id}")
async def delete_welcome_message(message_id: int):
    """특정 환영메시지를 삭제합니다."""
    try:
        from app.services.welcome_message_service import welcome_service
        
        success = welcome_service.delete_message(message_id)
        
        if success:
            return {
                "message": f"환영메시지 ID {message_id}가 삭제되었습니다.",
                "timestamp": datetime.now().isoformat()
            }
        else:
            raise HTTPException(status_code=404, detail=f"환영메시지 ID {message_id}를 찾을 수 없습니다")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting welcome message {message_id}: {e}")
        raise HTTPException(status_code=500, detail="환영메시지 삭제 중 오류가 발생했습니다")
