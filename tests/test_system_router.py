import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch

from app.main import app

client = TestClient(app)

class TestSystemEndpoints:

    @patch('app.api.routers.system.requests.get')
    def test_ollama_status_running(self, mock_get):
        """Test Ollama status when running"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        response = client.get("/api/system/ollama/status")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"

    @patch('app.api.routers.system.requests.get')
    def test_ollama_status_not_running(self, mock_get):
        """Test Ollama status when not running"""
        mock_get.side_effect = Exception("Connection failed")

        response = client.get("/api/system/ollama/status")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unreachable"
        assert "Connection failed" in data["error"]
