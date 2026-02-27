import requests
import json
from typing import List, Dict, Any, Optional, Generator, Union
from openai import OpenAI
from app.config import settings
from app.utils.logging_config import get_logger
from app.utils.exceptions import LLMError

logger = get_logger(__name__)

openrouter_client = None
if (
    settings.LLM_PROVIDER == "openrouter"
    and settings.OPENROUTER_API_KEY
    and not settings.OPENROUTER_API_KEY.startswith("your-")
):
    try:
        openrouter_client = OpenAI(
            base_url=settings.OPENROUTER_API_URL, api_key=settings.OPENROUTER_API_KEY
        )
    except Exception as e:
        logger.warning(f"Failed to initialize OpenRouter client: {e}")


def construct_multimodal_rag_prompt(
    user_query: str,
    context_chunks: List[str],
    image_descriptions: List[str],
    table_contents: List[str],
    lang: str = "ko",
    conversation_history: List[Dict[str, str]] = None,
    model_name: Optional[str] = None,
) -> tuple[str, bool]:
    """
    Construct a multimodal RAG prompt combining text, images, and tables.

    Args:
        user_query: User's query
        context_chunks: List of relevant text chunks
        image_descriptions: List of image descriptions
        table_contents: List of table contents
        lang: Language code (default: "ko")
        conversation_history: List of previous conversation turns
        model_name: Name of the model to use (for model-specific prompts)

    Returns:
        tuple[str, bool]: Constructed prompt for LLM and a boolean indicating if it's a reasoning model
    """
    # Check if the model is a reasoning model
    is_reasoning_model = model_name and any(
        kw in model_name.lower() for kw in ["reasoning", "qwen", "deepseek", "qwq"]
    )

    if lang == "ko":
        prompt_parts = []

        # 대화 히스토리 추가 (맥락 제공)
        if conversation_history and len(conversation_history) > 0:
            logger.info(
                f"Adding {len(conversation_history)} conversation history messages to prompt"
            )
            prompt_parts.extend(
                [
                    "=== 이전 대화 내용 ===",
                    "다음은 이전에 나눈 대화입니다. 이 맥락을 참고하여 답변해주세요:",
                    "",
                ]
            )

            # 최근 대화들만 포함 (최대 3쌍)
            recent_history = (
                conversation_history[-6:]
                if len(conversation_history) > 6
                else conversation_history
            )
            for msg in recent_history:
                role = "사용자" if msg.get("role") == "user" else "어시스턴트"
                content = msg.get("content", "").strip()
                if content:
                    prompt_parts.append(f"{role}: {content}")

            prompt_parts.extend(["", "=== 현재 질문 관련 정보 ===", ""])

        prompt_parts.extend(
            [
                "다음은 주조 기술 문서에서 추출한 관련 정보입니다:",
                "",
                "=== 텍스트 정보 ===",
            ]
        )

        for i, chunk in enumerate(context_chunks, 1):
            prompt_parts.append(f"텍스트 {i}: {chunk}")
            prompt_parts.append("")

        if image_descriptions:
            prompt_parts.append("=== 이미지 정보 ===")
            for i, desc in enumerate(image_descriptions, 1):
                if desc.strip():
                    prompt_parts.append(f"이미지 {i}: {desc}")
            prompt_parts.append("")

        if table_contents:
            prompt_parts.append("=== 표 정보 ===")
            for i, table in enumerate(table_contents, 1):
                if table.strip():
                    prompt_parts.append(f"표 {i}: {table}")
            prompt_parts.append("")

        prompt_parts.extend(
            [
                "위의 정보를 바탕으로 다음 질문에 답해주세요:",
                f"질문: {user_query}",
                "",
                "답변 시 다음 사항을 준수해주세요:",
                "1. 이전 대화 내용이 있다면 반드시 그 맥락을 고려하여 답변하세요",
                "2. 사용자가 '그것', '그거', '앞에서 말한', '위에서 언급한' 등의 표현을 사용하면 이전 대화를 참조하세요",
                "3. 제공된 정보만을 사용하여 정확하고 구체적으로 답변하세요",
                "4. 관련 이미지나 표가 있다면 해당 내용을 참조하여 설명하세요",
                "5. 정보가 부족하거나 없는 경우 솔직히 말씀해주세요",
                "",
                "⚡ **중요: 답변 형식 및 시각화 요구사항**",
                "6. **구조화된 마크다운 형식**으로 답변하되, 다음 시각화 요소들을 적극 활용하세요:",
                "",
                "   📊 **표 형식 데이터**: 비교, 분류, 수치 데이터는 반드시 표로 정리",
                "   ```",
                "   | 항목 | 값 | 단위 | 비고 |",
                "   |------|----|----- |----- |",
                "   | 예시 | 100 | ℃   | 권장값 |",
                "   ```",
                "",
                "   🎯 **강조 및 색깔 구조화**: 중요 정보는 다양한 방식으로 강조",
                "   - **🔴 위험/주의사항**: 빨간색 강조로 표시",
                "   - **🟡 권장사항**: 노란색 강조로 표시",
                "   - **🟢 정상/양호**: 초록색 강조로 표시",
                "   - **🔵 정보/참고**: 파란색 강조로 표시",
                "",
                "   📋 **단계별 프로세스**: 복잡한 과정은 번호나 체크리스트로",
                "   ```",
                "   ### 📝 작업 순서",
                "   1. ✅ 1단계: 준비작업",
                "   2. ⚙️ 2단계: 실행과정",
                "   3. 🔍 3단계: 검증단계",
                "   ```",
                "",
                "   💡 **박스 형태 정보**: 팁, 주의사항, 요약 등은 박스로 구분",
                "   ```",
                "   > 💡 **전문가 팁**",
                "   > 온도 관리가 품질의 80%를 결정합니다.",
                "   ```",
                "",
                "   📊 **수치 데이터 시각화**: 범위, 비율, 성능 지표 등",
                "   ```",
                "   적정 온도: ████████░░ 80% (1200-1250℃)",
                "   압력 범위: ██████░░░░ 60% (2-3 bar)",
                "   ```",
                "",
                "7. **상세하고 구체적인 내용**: 단순 설명보다는 실무에 도움되는 구체적 정보 제공",
                "   - 수치, 범위, 기준값 명시",
                "   - 원인과 결과의 관계 설명",
                "   - 실제 적용 방법과 주의사항",
                "   - 품질 기준 및 평가 방법",
                "",
                "8. **전문용어 설명**: 기술용어 사용 시 괄호 안에 쉬운 설명 추가",
                "   예: 탕구시스템(용융금속이 흐르는 통로) 설계",
                "",
                "9. **실무 중심 접근**: 이론보다는 실제 현장에서 활용할 수 있는 정보 우선",
                "",
                "답변:",
            ]
        )
    else:
        prompt_parts = []

        # Add conversation history (for context)
        if conversation_history and len(conversation_history) > 0:
            prompt_parts.extend(
                [
                    "=== Previous Conversation ===",
                    "Here is our previous conversation. Please refer to this context for your answer:",
                    "",
                ]
            )

            # Include only recent conversations (max 3 pairs)
            recent_history = (
                conversation_history[-6:]
                if len(conversation_history) > 6
                else conversation_history
            )
            for msg in recent_history:
                role = "User" if msg.get("role") == "user" else "Assistant"
                content = msg.get("content", "").strip()
                if content:
                    prompt_parts.append(f"{role}: {content}")

            prompt_parts.extend(["", "=== Current Question Information ===", ""])

        prompt_parts.extend(
            [
                "The following is relevant information extracted from foundry technology documents:",
                "",
                "=== Text Information ===",
            ]
        )

        for i, chunk in enumerate(context_chunks, 1):
            prompt_parts.append(f"Text {i}: {chunk}")
            prompt_parts.append("")

        if image_descriptions:
            prompt_parts.append("=== Image Information ===")
            for i, desc in enumerate(image_descriptions, 1):
                if desc.strip():
                    prompt_parts.append(f"Image {i}: {desc}")
            prompt_parts.append("")

        if table_contents:
            prompt_parts.append("=== Table Information ===")
            for i, table in enumerate(table_contents, 1):
                if table.strip():
                    prompt_parts.append(f"Table {i}: {table}")
            prompt_parts.append("")

        prompt_parts.extend(
            [
                "Based on the above information, please answer the following question:",
                f"Question: {user_query}",
                "",
                "Please follow these guidelines when answering:",
                "1. If there is previous conversation history, always consider that context in your response",
                "2. When users use words like 'it', 'that', 'mentioned above', 'previously discussed', refer to the conversation history",
                "3. Use only the provided information to give accurate and specific answers",
                "4. If there are related images or tables, refer to them in your explanation",
                "5. Be honest if information is insufficient or unavailable",
                "",
                "⚡ **Important: Answer Format & Visualization Requirements**",
                "6. **Structured Markdown Format** with extensive use of the following visualization elements:",
                "",
                "   📊 **Tabular Data**: Always organize comparisons, classifications, and numerical data in tables",
                "   ```",
                "   | Item | Value | Unit | Notes |",
                "   |------|-------|------|-------|",
                "   | Example | 100 | ℃ | Recommended |",
                "   ```",
                "",
                "   🎯 **Emphasis & Color Coding**: Highlight important information with various methods",
                "   - **🔴 Danger/Warnings**: Mark with red emphasis",
                "   - **🟡 Recommendations**: Mark with yellow emphasis",
                "   - **🟢 Normal/Good**: Mark with green emphasis",
                "   - **🔵 Information/Reference**: Mark with blue emphasis",
                "",
                "   📋 **Step-by-Step Processes**: Present complex procedures as numbered lists or checklists",
                "   ```",
                "   ### 📝 Work Sequence",
                "   1. ✅ Step 1: Preparation",
                "   2. ⚙️ Step 2: Execution",
                "   3. 🔍 Step 3: Verification",
                "   ```",
                "",
                "   💡 **Information Boxes**: Use boxes for tips, warnings, and summaries",
                "   ```",
                "   > 💡 **Expert Tip**",
                "   > Temperature control determines 80% of quality.",
                "   ```",
                "",
                "   📊 **Numerical Data Visualization**: Show ranges, ratios, performance indicators",
                "   ```",
                "   Optimal Temp: ████████░░ 80% (1200-1250℃)",
                "   Pressure Range: ██████░░░░ 60% (2-3 bar)",
                "   ```",
                "",
                "7. **Detailed and Specific Content**: Provide practical information rather than simple explanations",
                "   - Include specific numbers, ranges, and standard values",
                "   - Explain cause-and-effect relationships",
                "   - Provide practical application methods and precautions",
                "   - Include quality standards and evaluation methods",
                "",
                "8. **Technical Term Explanations**: Add simple explanations in parentheses for technical terms",
                "   Example: Gating system (channels through which molten metal flows) design",
                "",
                "9. **Practical Focus**: Prioritize information that can be used in actual field work over theory",
                "",
                "Answer:",
            ]
        )

    return "\n".join(prompt_parts), is_reasoning_model


def get_openrouter_response_stream(
    prompt: str, model_name: str = None, options: Dict = None
) -> Generator[str, None, None]:
    if not openrouter_client:
        raise LLMError("OpenRouter client not initialized. Check OPENROUTER_API_KEY.")

    if not model_name:
        model_name = settings.OPENROUTER_DEFAULT_MODEL

    temperature = (
        options.get("temperature", settings.LLM_TEMPERATURE)
        if options
        else settings.LLM_TEMPERATURE
    )
    max_tokens = (
        options.get("num_predict", settings.LLM_NUM_PREDICT_TEXT)
        if options
        else settings.LLM_NUM_PREDICT_TEXT
    )

    logger.info(
        f"Sending streaming prompt to OpenRouter model '{model_name}'. Prompt length: {len(prompt)} chars"
    )

    try:
        stream = openrouter_client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )

        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    except Exception as e:
        error_msg = f"OpenRouter API error: {str(e)}"
        logger.error(error_msg)
        raise LLMError(error_msg)


def get_openrouter_response(
    prompt: str, model_name: str = None, options: Dict = None
) -> str:
    if not openrouter_client:
        raise LLMError("OpenRouter client not initialized. Check OPENROUTER_API_KEY.")

    if not model_name:
        model_name = settings.OPENROUTER_DEFAULT_MODEL

    temperature = (
        options.get("temperature", settings.LLM_TEMPERATURE)
        if options
        else settings.LLM_TEMPERATURE
    )
    max_tokens = (
        options.get("num_predict", settings.LLM_NUM_PREDICT_TEXT)
        if options
        else settings.LLM_NUM_PREDICT_TEXT
    )

    logger.info(
        f"Sending prompt to OpenRouter model '{model_name}'. Prompt length: {len(prompt)} chars"
    )

    try:
        response = openrouter_client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )

        result = response.choices[0].message.content or ""
        logger.info(f"Received response from OpenRouter. Length: {len(result)} chars")
        return result.strip()

    except Exception as e:
        error_msg = f"OpenRouter API error: {str(e)}"
        logger.error(error_msg)
        raise LLMError(error_msg)


def get_ollama_response_stream(
    prompt: str, model_name: str = None, options: Dict = None
) -> Generator[str, None, None]:
    if not model_name:
        model_name = settings.OLLAMA_DEFAULT_MODEL

    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": True,
    }
    if options:
        payload["options"] = options

    logger.info(
        f"Sending streaming prompt to Ollama model '{model_name}'. Prompt length: {len(prompt)} chars"
    )

    try:
        response = requests.post(
            settings.OLLAMA_API_URL,
            json=payload,
            stream=True,
            timeout=settings.OLLAMA_TIMEOUT,
        )

        if response.status_code != 200:
            error_msg = f"Ollama API request failed with status {response.status_code}: {response.text}"
            logger.error(error_msg)
            raise LLMError(error_msg)

        logger.debug("Streaming response from Ollama...")
        for line in response.iter_lines():
            if line:
                try:
                    json_response = json.loads(line.decode("utf-8"))
                    if "response" in json_response:
                        yield json_response["response"]
                    if json_response.get("done", False):
                        break
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"Failed to parse JSON response line: {line}, error: {e}"
                    )
                    continue

    except requests.exceptions.Timeout:
        error_msg = f"Ollama request timed out after {settings.OLLAMA_TIMEOUT} seconds"
        logger.error(error_msg)
        raise LLMError(error_msg)
    except requests.exceptions.ConnectionError:
        error_msg = "Failed to connect to Ollama. Please ensure Ollama is running."
        logger.error(error_msg)
        raise LLMError(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error during Ollama request: {str(e)}"
        logger.error(error_msg)
        raise LLMError(error_msg)


def get_ollama_response(
    prompt: str, model_name: str = None, options: Dict = None
) -> str:
    if not model_name:
        model_name = settings.OLLAMA_DEFAULT_MODEL

    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
    }
    if options:
        payload["options"] = options

    logger.info(
        f"Sending prompt to Ollama model '{model_name}'. Prompt length: {len(prompt)} chars"
    )
    logger.debug(f"Prompt preview: {prompt[:200]}...")

    try:
        response = requests.post(
            settings.OLLAMA_API_URL,
            json=payload,
            stream=True,
            timeout=settings.OLLAMA_TIMEOUT,
        )

        if response.status_code != 200:
            error_msg = f"Ollama API request failed with status {response.status_code}: {response.text}"
            logger.error(error_msg)
            raise LLMError(error_msg)

        full_response = ""
        for line in response.iter_lines():
            if line:
                try:
                    json_response = json.loads(line.decode("utf-8"))
                    if "response" in json_response:
                        full_response += json_response["response"]
                    if json_response.get("done", False):
                        break
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"Failed to parse JSON response line: {line}, error: {e}"
                    )
                    continue

        logger.info(
            f"Received complete response from Ollama. Length: {len(full_response)} chars"
        )
        return full_response.strip()

    except requests.exceptions.Timeout:
        error_msg = f"Ollama request timed out after {settings.OLLAMA_TIMEOUT} seconds"
        logger.error(error_msg)
        raise LLMError(error_msg)
    except requests.exceptions.ConnectionError:
        error_msg = "Failed to connect to Ollama. Please ensure Ollama is running."
        logger.error(error_msg)
        raise LLMError(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error during Ollama request: {str(e)}"
        logger.error(error_msg)
        raise LLMError(error_msg)


def get_llm_response_stream(
    prompt: str, model_name: str = None, options: Dict = None
) -> Generator[str, None, None]:
    if settings.LLM_PROVIDER == "openrouter":
        return get_openrouter_response_stream(prompt, model_name, options)
    else:
        return get_ollama_response_stream(prompt, model_name, options)


def get_llm_response(
    prompt: str, model_name: str = None, options: Dict = None, stream: bool = False
) -> Union[str, Generator[str, None, None]]:
    if stream:
        return get_llm_response_stream(prompt, model_name, options)

    if settings.LLM_PROVIDER == "openrouter":
        return get_openrouter_response(prompt, model_name, options)
    else:
        return get_ollama_response(prompt, model_name, options)


def get_llm_response_async(
    prompt: str, model_name: str = None, options: Dict = None
) -> str:
    """
    Asynchronous version of get_llm_response for non-streaming requests.

    Args:
        prompt (str): The prompt to send to the LLM.
        model_name (str, optional): The name of the Ollama model to use.
        options (Dict, optional): Additional Ollama model parameters.

    Returns:
        str: The LLM's response text.
    """
    return get_llm_response(prompt, model_name, options, stream=False)
