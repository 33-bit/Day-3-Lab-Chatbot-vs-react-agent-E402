"""
🔌 MULTI-PROVIDER LLM ADAPTER (OpenAI, Gemini, Anthropic, Mistral, OpenRouter & Offline Mock)
Hỗ trợ chuyển đổi linh hoạt giữa các nhà cung cấp AI chỉ bằng cách đổi biến môi trường LLM_PROVIDER.
"""

import os
import re
import sys
import json
import requests
from dotenv import load_dotenv

# Đảm bảo in ra Tiếng Việt và Emojis không bị lỗi trên Windows Console
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

load_dotenv()

class BaseLLMProvider:
    """Interface cơ sở cho tất cả các LLM Provider"""
    def generate(self, prompt: str, system_prompt: str = "") -> str:
        raise NotImplementedError


class GeminiProvider(BaseLLMProvider):
    """Google Gemini Provider"""
    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model_name = model or os.getenv("LLM_MODEL") or "gemini-2.5-flash"
        
    def generate(self, prompt: str, system_prompt: str = "") -> str:
        if not self.api_key or self.api_key == "your_gemini_api_key_here":
            return "[Gemini Error]: Chưa cấu hình GEMINI_API_KEY trong file .env!"
        try:
            from google import genai
            client = genai.Client(api_key=self.api_key)
            contents = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
            response = client.models.generate_content(
                model=self.model_name,
                contents=contents
            )
            return response.text
        except Exception as e:
            return f"[Gemini Exception]: {str(e)}"


class OpenAIProvider(BaseLLMProvider):
    """OpenAI Provider (GPT-4o, GPT-3.5-turbo, etc.)"""
    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model_name = model or os.getenv("LLM_MODEL") or "gpt-4o-mini"
        
    def generate(self, prompt: str, system_prompt: str = "") -> str:
        if not self.api_key or self.api_key == "your_openai_api_key_here":
            return "[OpenAI Error]: Chưa cấu hình OPENAI_API_KEY trong file .env!"
        try:
            import openai
            client = openai.OpenAI(api_key=self.api_key)
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            
            response = client.chat.completions.create(
                model=self.model_name,
                messages=messages
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"[OpenAI Exception]: {str(e)}"


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude Provider (Claude 3.5 Sonnet, Claude 3 Haiku)"""
    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.model_name = model or os.getenv("LLM_MODEL") or "claude-3-haiku-20240307"
        
    def generate(self, prompt: str, system_prompt: str = "") -> str:
        if not self.api_key or self.api_key == "your_anthropic_api_key_here":
            return "[Anthropic Error]: Chưa cấu hình ANTHROPIC_API_KEY trong file .env!"
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)
            kwargs = {
                "model": self.model_name,
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": prompt}]
            }
            if system_prompt:
                kwargs["system"] = system_prompt
                
            response = client.messages.create(**kwargs)
            return response.content[0].text
        except Exception as e:
            return f"[Anthropic Exception]: {str(e)}"


class OpenRouterProvider(BaseLLMProvider):
    """OpenRouter Provider (Hỗ trợ gọi mọi model qua OpenRouter API)"""
    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self.model_name = model or os.getenv("LLM_MODEL") or "google/gemini-2.5-flash"
        
    def generate(self, prompt: str, system_prompt: str = "") -> str:
        if not self.api_key or self.api_key == "your_openrouter_api_key_here":
            return "[OpenRouter Error]: Chưa cấu hình OPENROUTER_API_KEY trong file .env!"
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            
            payload = {
                "model": self.model_name,
                "messages": messages
            }
            res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=30)
            if res.status_code == 200:
                data = res.json()
                return data["choices"][0]["message"]["content"]
            else:
                return f"[OpenRouter API Error {res.status_code}]: {res.text}"
        except Exception as e:
            return f"[OpenRouter Exception]: {str(e)}"


class MistralProvider(BaseLLMProvider):
    """Mistral AI Provider (Mistral Small, Mistral Large, etc.)"""
    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or os.getenv("MISTRAL_API_KEY")
        self.model_name = model or os.getenv("LLM_MODEL") or "mistral-small-latest"

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        if not self.api_key or self.api_key == "your_mistral_api_key_here":
            return "[Mistral Error]: Chưa cấu hình MISTRAL_API_KEY trong file .env!"
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            payload = {
                "model": self.model_name,
                "messages": messages
            }
            res = requests.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=30
            )
            if res.status_code == 200:
                data = res.json()
                return data["choices"][0]["message"]["content"]
            return f"[Mistral API Error {res.status_code}]: {res.text}"
        except Exception as e:
            return f"[Mistral Exception]: {str(e)}"


class MockProvider(BaseLLMProvider):
    """Offline Mock Provider (Cho bài test không cần kết nối API)"""
    def generate(self, prompt: str, system_prompt: str = "") -> str:
        if "react agent" in system_prompt.lower():
            query_match = re.search(
                r"<user_query>\s*(.*?)\s*</user_query>",
                prompt,
                flags=re.DOTALL,
            )
            user_query = query_match.group(1) if query_match else prompt
            order_match = re.search(r"\bDH\d{3,}\b", user_query.upper())
            contact_match = re.search(
                r"(?:\+?84|0)\d{9,10}|[^@\s]+@[^@\s]+\.[^@\s]+",
                user_query,
            )
            controlled_history = prompt.split(
                "NHẬT KÝ REACT DO APPLICATION KIỂM SOÁT:",
                maxsplit=1,
            )[-1]

            if "(chưa có bước nào)" not in controlled_history:
                order_id = order_match.group(0) if order_match else "đơn hàng"
                if '"success": true' in controlled_history.lower():
                    return (
                        "Thought: Tôi đã có Observation hợp lệ từ hệ thống.\n"
                        f"Final Answer: Đã tra cứu {order_id} thành công. "
                        "Thông tin trạng thái đã được xác nhận từ hệ thống."
                    )
                return (
                    "Thought: Observation cho biết chưa thể hoàn tất tra cứu.\n"
                    "Final Answer: Tôi chưa thể xác thực đơn hàng với thông tin "
                    "đã cung cấp. Vui lòng kiểm tra lại mã đơn và thông tin liên hệ."
                )

            if order_match and contact_match:
                return (
                    "Thought: Tôi cần xác thực và tra cứu đơn hàng bằng tool.\n"
                    "Action: search_order["
                    f"order_id='{order_match.group(0)}', "
                    f"customer_contact='{contact_match.group(0)}']"
                )
            if order_match:
                return (
                    "Thought: Tôi còn thiếu thông tin liên hệ để xác thực đơn.\n"
                    "Final Answer: Vui lòng cung cấp email hoặc số điện thoại đã "
                    "dùng khi đặt hàng để tôi tra cứu an toàn."
                )
            return (
                "Thought: Tôi cần mã đơn hàng trước khi sử dụng tool.\n"
                "Final Answer: Vui lòng cung cấp mã đơn theo định dạng DH001 để "
                "tôi hỗ trợ tra cứu."
            )

        text = prompt.lower()
        if "thời tiết" in text and "hà nội" in text:
            return "Thought: Cần tra cứu thời tiết Hà Nội.\nAction: get_weather['Hà Nội']"
        return "🤖 [Mock Provider]: Phản hồi giả lập offline cho bài test."


def get_llm_provider(provider_name: str = None, model: str = None) -> BaseLLMProvider:
    """Factory function tự chọn Provider từ biến môi trường LLM_PROVIDER"""
    name = (provider_name or os.getenv("LLM_PROVIDER") or "mock").lower().strip()
    
    if name == "gemini":
        return GeminiProvider(model=model)
    elif name == "openai":
        return OpenAIProvider(model=model)
    elif name == "anthropic":
        return AnthropicProvider(model=model)
    elif name == "openrouter":
        return OpenRouterProvider(model=model)
    elif name == "mistral":
        return MistralProvider(model=model)
    else:
        return MockProvider()


if __name__ == "__main__":
    print("=== TEST MULTI-PROVIDER LLM ADAPTER ===")
    provider = get_llm_provider()
    print(f"✅ Provider đang dùng: {provider.__class__.__name__}")
    print(f"🤖 User Query: Hello")
    print(f"💬 Response  : {provider.generate('Hello')}")
