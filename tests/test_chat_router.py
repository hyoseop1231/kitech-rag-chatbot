import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch

from app.main import app
# Import the Pydantic model from its new location
from app.api.routers.chat import ChatRequest

client = TestClient(app)

class TestChatEndpoint:

    @patch('app.api.routers.chat.get_embeddings')
    @patch('app.api.routers.chat.search_multimodal_content')
    @patch('app.api.routers.chat.process_multimodal_llm_chat_request')
    def test_chat_success(self, mock_llm, mock_search, mock_embeddings):
        """Test successful chat request"""
        mock_embeddings.return_value = [[0.1, 0.2, 0.3]]
        mock_search.return_value = {
            'text': [{"text": "Sample document text", "metadata": {"source_document_id": "doc1"}}],
            'images': [],
            'tables': [],
        }
        mock_llm.return_value = "This is the AI response."
        # Mock the enhancement function as well
        with patch('app.api.routers.chat.enhance_response_with_media_references') as mock_enhance:
            mock_enhance.return_value = {'text': 'This is the AI response.', 'referenced_media': {}}

            response = client.post(
                # Note the updated path
                "/api/chat/chat/",
                json={"query": "What is this document about?", "model_name": "test_model"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["query"] == "What is this document about?"
            assert data["response"] == "This is the AI response."

    def test_chat_empty_query(self):
        """Test chat with empty query"""
        response = client.post(
            "/api/chat/chat/",
            json={"query": ""}
        )
        assert response.status_code == 400

    def test_chat_invalid_document_id(self):
        """Test chat with invalid document ID"""
        response = client.post(
            "/api/chat/chat/",
            json={"query": "Test query", "document_ids": ["invalid@id"]}
        )
        assert response.status_code == 400
        assert "Invalid document ID" in response.json()["detail"]

    @patch('app.api.routers.chat.sanitize_input')
    def test_chat_input_sanitization(self, mock_sanitize):
        """Test that input is properly sanitized"""
        mock_sanitize.return_value = "cleaned query"

        with patch('app.api.routers.chat.get_embeddings') as mock_embeddings, \
             patch('app.api.routers.chat.search_multimodal_content') as mock_search, \
             patch('app.api.routers.chat.process_multimodal_llm_chat_request') as mock_llm, \
             patch('app.api.routers.chat.FallbackResponseService.generate_no_results_response') as mock_fallback:

            mock_embeddings.return_value = [[0.1, 0.2, 0.3]]
            mock_search.return_value = {'text': [], 'images': [], 'tables': []}
            mock_fallback.return_value = {"response": "No results found."}

            client.post(
                "/api/chat/chat/",
                json={"query": "  malicious input  "}
            )

            mock_sanitize.assert_called_with("  malicious input  ", max_length=2000)
