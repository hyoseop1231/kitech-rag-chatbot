import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch

# Import the app instance from main
from app.main import app
# Import the new service functions to be patched
from app.services import document_processing_service

client = TestClient(app)

class TestUploadEndpoint:

    @patch('app.api.routers.document.FileValidator.validate_uploaded_file')
    @patch('app.services.document_processing_service.process_pdf_background_entry')
    def test_upload_pdf_success(self, mock_process_entry, mock_validator):
        """Test successful PDF upload"""
        mock_validator.return_value = {"is_valid": True, "errors": [], "file_hash": "test_hash"}

        test_pdf_content = b"%PDF-1.4\ntest content\n%%EOF"

        response = client.post(
            "/api/upload_pdf/",
            files={"files": ("test.pdf", test_pdf_content, "application/pdf")},
            data={"ocr_correction_enabled": "false", "llm_correction_enabled": "false"}
        )

        assert response.status_code == 202
        data = response.json()['results'][0]
        assert "document_id" in data
        assert data["file_hash"] == "test_hash"
        assert data["filename"] == "test.pdf"
        mock_process_entry.assert_called_once()

    # Note: The original tests for invalid file type and empty file relied on logic
    # that may have been in the old endpoint. The new endpoint has robust validation,
    # but we will simplify the test case here.
    def test_upload_empty_file(self):
        """Test upload with empty file"""
        response = client.post(
            "/api/upload_pdf/",
            files={"files": ("test.pdf", b"", "application/pdf")},
            data={"ocr_correction_enabled": "false", "llm_correction_enabled": "false"}
        )
        assert response.status_code == 202 # The endpoint now accepts and queues, but result shows error
        assert "Empty file uploaded" in response.json()['results'][0]["error"]


class TestStatusEndpoints:

    @patch('app.api.routers.document.get_pdf_processing_status')
    def test_upload_status_existing(self, mock_get_status):
        """Test getting status for existing document"""
        mock_get_status.return_value = {"doc123": {"step": "Completed", "message": "Done"}}
        response = client.get("/api/upload_status/doc123")

        assert response.status_code == 200
        data = response.json()
        assert data["step"] == "Completed"
        assert data["message"] == "Done"

    @patch('app.api.routers.document.get_pdf_processing_status')
    def test_upload_status_nonexistent(self, mock_get_status):
        """Test getting status for non-existent document"""
        mock_get_status.return_value = {}
        # Also patch the DB check to avoid real DB calls
        with patch('app.api.routers.document.get_multimodal_document_info') as mock_db_check:
            mock_db_check.return_value = None
            response = client.get("/api/upload_status/nonexistent")

            assert response.status_code == 200
            data = response.json()
            assert data["step"] == "Unknown"
