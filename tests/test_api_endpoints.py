"""Tests for API endpoints"""

import pytest
import asyncio
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from app.main import app
from app.api.routers.chat import ChatRequest

client = TestClient(app, base_url="http://localhost")

class TestUploadEndpoint:
    
    @patch('app.api.routers.upload.FileValidator.validate_uploaded_file')
    @patch('app.api.routers.upload.process_pdf_background')
    def test_upload_pdf_success(self, mock_process, mock_validator):
        """Test successful PDF upload"""
        # Mock validation
        mock_validator.return_value = {
            "is_valid": True,
            "errors": [],
            "file_hash": "test_hash"
        }
        
        # Create a test PDF file
        test_pdf_content = b"%PDF-1.4\ntest content\n%%EOF"
        
        response = client.post(
            "/api/upload_pdf/",
            files={
                "files": ("test.pdf", test_pdf_content, "application/pdf")
            },
            data={
                "ocr_correction_enabled": "false",
                "llm_correction_enabled": "false"
            }
        )
        
        assert response.status_code == 202
        data = response.json()
        # Multi-file response format: {"results": [...]}
        assert "results" in data
        result = data["results"][0]
        assert "document_id" in result
        assert "file_hash" in result
        assert result["filename"] == "test.pdf"
    
    def test_upload_invalid_file_type(self):
        """Test upload with invalid file type — returns 202 with error in results"""
        test_content = b"not a pdf"
        
        response = client.post(
            "/api/upload_pdf/",
            files={
                "files": ("test.txt", test_content, "text/plain")
            },
            data={
                "ocr_correction_enabled": "false",
                "llm_correction_enabled": "false"
            }
        )
        
        # Multi-file endpoint always returns 202; per-file errors in results
        assert response.status_code == 202
        data = response.json()
        result = data["results"][0]
        assert "error" in result
        assert "validation failed" in result["error"].lower() or "File validation failed" in result["error"]
    
    def test_upload_empty_file(self):
        """Test upload with empty file — returns 202 with error in results"""
        response = client.post(
            "/api/upload_pdf/",
            files={
                "files": ("test.pdf", b"", "application/pdf")
            },
            data={
                "ocr_correction_enabled": "false",
                "llm_correction_enabled": "false"
            }
        )
        
        assert response.status_code == 202
        data = response.json()
        result = data["results"][0]
        assert "error" in result
        assert "Empty file" in result["error"]
    
    @patch('app.api.routers.upload.FileValidator.validate_uploaded_file')
    def test_upload_validation_failure(self, mock_validator):
        """Test upload with validation failure — returns 202 with error in results"""
        # Mock validation failure
        mock_validator.return_value = {
            "is_valid": False,
            "errors": ["Invalid MIME type"],
            "file_hash": ""
        }
        
        test_pdf_content = b"fake pdf content"
        
        response = client.post(
            "/api/upload_pdf/",
            files={
                "files": ("test.pdf", test_pdf_content, "application/pdf")
            },
            data={
                "ocr_correction_enabled": "false",
                "llm_correction_enabled": "false"
            }
        )
        
        assert response.status_code == 202
        data = response.json()
        result = data["results"][0]
        assert "error" in result
        assert "File validation failed" in result["error"]

class TestChatEndpoint:
    
    @patch('app.api.routers.chat.get_embeddings')
    @patch('app.api.routers.chat.search_multimodal_content')
    @patch('app.api.routers.chat.process_multimodal_llm_chat_request')
    @patch('app.api.routers.chat.enhance_response_with_media_references')
    def test_chat_success(self, mock_enhance, mock_llm, mock_search, mock_embeddings):
        """Test successful chat request"""
        # Mock embeddings
        mock_embeddings.return_value = [[0.1, 0.2, 0.3]]
        
        # Mock multimodal search results
        mock_search.return_value = {
            'text': [{"text": "Sample document text", "metadata": {"source_document_id": "doc1"}}],
            'images': [],
            'tables': []
        }
        
        # Mock LLM response
        mock_llm.return_value = "This is the AI response."
        
        # Mock enhance response
        mock_enhance.return_value = {
            'text': "This is the AI response.",
            'referenced_images': [],
            'referenced_tables': [],
            'has_media': False
        }
        
        response = client.post(
            "/api/chat/",
            json={
                "query": "What is this document about?",
                "model_name": "test_model",
                "lang": "ko"
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["query"] == "What is this document about?"
        # FallbackResponseService may enhance short responses with additional info
        assert "This is the AI response." in data["response"]
    
    def test_chat_empty_query(self):
        """Test chat with empty query"""
        response = client.post(
            "/api/chat/",
            json={"query": ""}
        )
        
        assert response.status_code == 400
    
    @patch('app.api.routers.chat.sanitize_input')
    @patch('app.api.routers.chat.validate_document_id')
    def test_chat_invalid_document_id(self, mock_validate_id, mock_sanitize):
        """Test chat with invalid document ID"""
        mock_sanitize.return_value = "Test query"
        mock_validate_id.return_value = False
        
        response = client.post(
            "/api/chat/",
            json={
                "query": "Test query",
                "document_ids": ["invalid@id"]
            }
        )
        
        assert response.status_code == 400
        assert "Invalid document ID" in response.json()["detail"]
    
    @patch('app.api.routers.chat.sanitize_input')
    def test_chat_input_sanitization(self, mock_sanitize):
        """Test that input is properly sanitized"""
        mock_sanitize.return_value = "cleaned query"
        
        # This test ensures sanitize_input is called
        with patch('app.api.routers.chat.get_embeddings') as mock_embeddings:
            mock_embeddings.return_value = [[0.1, 0.2, 0.3]]
            with patch('app.api.routers.chat.search_multimodal_content') as mock_search:
                mock_search.return_value = {'text': [], 'images': [], 'tables': []}
                with patch('app.api.routers.chat.process_multimodal_llm_chat_request') as mock_llm:
                    mock_llm.return_value = "response"
                    
                    response = client.post(
                        "/api/chat/",
                        json={"query": "  malicious input  "}
                    )
                    
                    mock_sanitize.assert_called_once()

class TestStatusEndpoints:
    
    def test_upload_status_existing(self):
        """Test getting status for existing document"""
        with patch('app.api.routers.upload.pdf_processing_status', {"doc123": {"step": "Done", "message": "Complete"}}):
            response = client.get("/api/upload_status/doc123")
            
            assert response.status_code == 200
            data = response.json()
            assert data["step"] == "Done"
            assert data["message"] == "Complete"
    
    def test_upload_status_nonexistent(self):
        """Test getting status for non-existent document"""
        response = client.get("/api/upload_status/nonexistent")
        
        assert response.status_code == 200
        data = response.json()
        assert data["step"] == "Unknown"
    
    @patch('app.api.routers.models.httpx.AsyncClient')
    def test_ollama_status_running(self, mock_client_class):
        """Test Ollama status when running"""
        mock_response = Mock()
        mock_response.status_code = 200
        
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_class.return_value = mock_client
        
        response = client.get("/api/ollama/status")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"
    
    @patch('app.api.routers.models.httpx.AsyncClient')
    def test_ollama_status_not_running(self, mock_client_class):
        """Test Ollama status when not running"""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Connection failed"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_class.return_value = mock_client
        
        response = client.get("/api/ollama/status")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unreachable"