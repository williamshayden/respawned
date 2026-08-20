from litellm import completion
import os
#TODO: modularize this endpoint, integrate with litellm proxy server. #NOTE - this should not be treated as complete, final, or working code. It is a placeholder for testing and development purposes only.
llm_provider = os.getenv("LLM_PROVIDER")

#future: add support for more providers. ie OpenRouter, Gemini, Local, etc.
provider_key_map = {
    "anthropic": os.getenv("ANTHROPIC_API_KEY"),
    "openai": os.getenv("OPENAI_API_KEY"),
}

if llm_provider not in provider_key_map:
    raise ValueError(f"Unsupported LLM provider: {llm_provider}")

content = input("Enter your prompt: ")

response = completion(
  model=os.getenv("LLM_MODEL"),
  messages=[{"role": "user", "content": content}]
)
print(response.choices[0].message.content)