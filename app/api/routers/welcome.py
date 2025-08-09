from fastapi import APIRouter, HTTPException
from app.utils.logging_config import get_logger
from app.services.welcome_message_service import (
    get_random_welcome_message,
    generate_welcome_messages as generate_service,
    welcome_service,
    get_welcome_message_stats as get_stats_service
)

logger = get_logger(__name__)
router = APIRouter()

@router.get("/welcome-messages/random")
async def get_random_message():
    """
    Returns a single random welcome message.
    """
    try:
        return {"message": get_random_welcome_message()}
    except Exception as e:
        logger.error(f"Error getting random welcome message: {e}", exc_info=True)
        return {"message": "Hello! How can I help you with your documents today?"}

@router.post("/welcome-messages/generate")
async def generate_welcome_messages(count: int = 5):
    """
    Generates a specified number of new welcome messages.
    """
    if not 1 <= count <= 10:
        raise HTTPException(status_code=400, detail="Count must be between 1 and 10.")
    try:
        generated_count = await generate_service(count)
        return {"message": f"{generated_count} new welcome messages have been generated."}
    except Exception as e:
        logger.error(f"Error generating welcome messages: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate welcome messages.")

@router.get("/welcome-messages")
async def get_all_messages():
    """
    Returns all available welcome messages.
    """
    try:
        messages = welcome_service.get_all_messages()
        return {"messages": messages, "count": len(messages)}
    except Exception as e:
        logger.error(f"Error getting all welcome messages: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve welcome messages.")

@router.delete("/welcome-messages/{message_id}")
async def delete_message(message_id: int):
    """
    Deletes a specific welcome message by its ID.
    """
    try:
        if welcome_service.delete_message(message_id):
            return {"message": f"Welcome message {message_id} deleted."}
        else:
            raise HTTPException(status_code=404, detail=f"Welcome message with ID {message_id} not found.")
    except Exception as e:
        logger.error(f"Error deleting welcome message {message_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete welcome message.")

@router.get("/welcome-messages/stats")
async def get_stats():
    """
    Returns statistics about the welcome messages.
    """
    try:
        return get_stats_service()
    except Exception as e:
        logger.error(f"Error getting welcome message stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get welcome message stats.")
