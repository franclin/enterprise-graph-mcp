import logging
from opentelemetry.context import Context
from opentelemetry.sdk.trace import SpanProcessor, ReadableSpan

# Configure a dedicated audit logger for token cost auditing
audit_logger = logging.getLogger("mcp.token_audit")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [AUDIT] - %(message)s")

class TokenCostAuditProcessor(SpanProcessor):
    """
    A SpanProcessor that audits token costs for spans related to LLM calls.
    It logs the span name, trace ID, and token cost to a dedicated audit logger.
    """

    def __init__(self):
        #super().__init__()
        self.PRICING = {
            "gpt-4o": {"input": 2.50, "output": 10.00},
            "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
            "default": {"input": 5.00, "output": 15.00},
        }
    def on_start(self, span: ReadableSpan, parent_context: Context) -> None:
        """Called when a span is started."""
        print(f"--- TOKEN COST AUDITING Starting span: {span.name} ---")
        # No action needed on span start for auditing
        pass

    def on_end(self, span: ReadableSpan) -> None:
        # Standard OTel semantic keys for GenAI tracking
        model_name = span.attributes.get("gen_ai.response.model") or span.attributes.get("llm.request.model")
        prompt_tokens = span.attributes.get("gen_ai.usage.input_tokens") or span.attributes.get("llm.usage.prompt_tokens")
        completion_tokens = span.attributes.get("gen_ai.usage.output_tokens") or span.attributes.get("llm.usage.completion_tokens")

        print(f"--- TOKEN COST AUDITING Ending span: {span.name} ---")
        print(f" Attributes before masking: {span.attributes}")
        print(f" Model name: {model_name}, prompt token: {prompt_tokens}, completion token: {completion_tokens}, Span ID: {span.context.span_id}")

        # Process if this span generated token tracking metrics
        if prompt_tokens is not None or completion_tokens is not None:
            prompt_tokens = prompt_tokens or 0
            completion_tokens = completion_tokens or 0
            model_key = str(model_name).lower() if model_name else "default"
            
            # Match pricing tier
            rates = self.PRICING.get(model_key, self.PRICING["default"])
            
            # Calculate cost ($ per million tokens)
            input_cost = (prompt_tokens / 1_000_000) * rates["input"]
            output_cost = (completion_tokens / 1_000_000) * rates["output"]
            total_cost = input_cost + output_cost

            # Inject the calculated cost back into the span so visualization tools see it
            span._attributes["audit.cost_usd"] = total_cost

            print(f"--- TOKEN COST AUDITING span: {span.name} ---")
            print(f" Trace ID: {span.context.trace_id}, Span ID: {span.context.span_id}")
            print(f" Model: {model_key}, Prompt Tokens: {prompt_tokens}, Completion Tokens: {completion_tokens}, Total Cost: ${total_cost:.6f}")

            # Write to audit infrastructure
            audit_logger.info(
                f"TraceID: {span.context.trace_id:x} | Span: {span.name} | "
                f"Model: {model_key} | Input: {prompt_tokens} | Output: {completion_tokens} | "
                f"Total Cost: ${total_cost:.6f}"
            )