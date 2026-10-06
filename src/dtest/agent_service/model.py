"""Public model binding compatibility for the configured OpenAI-compatible gateway."""

from langchain_openai import ChatOpenAI


class CompatibleChatOpenAI(ChatOpenAI):
    def bind_tools(self, tools, **kwargs):
        # Let LangChain convert tools/ProviderStrategy schemas through its public
        # bind_tools API. Its no-tool ProviderStrategy branch otherwise sends
        # tools=[], which the on-prem gateway rejects instead of treating as absent.
        binding = super().bind_tools(tools, **kwargs)
        if tools:
            return binding
        options = {
            key: value
            for key, value in binding.kwargs.items()
            if key not in {"tools", "tool_choice", "parallel_tool_calls"}
        }
        return self.bind(**options)
