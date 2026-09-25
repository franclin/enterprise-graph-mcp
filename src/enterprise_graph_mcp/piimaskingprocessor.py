import os
import re
from opentelemetry.context import Context
from opentelemetry.trace import Span
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter


class PiiMaskingProcessor(BatchSpanProcessor):
    """
    A SpanProcessor that masks PII (Personally Identifiable Information) in span attributes.
    """

    def __init__(self):
        # Point to your local or remote OTLP collector endpoint (e.g., Jaeger, SigNoz)
        otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        otlp_exporter = OTLPSpanExporter(
            endpoint=otlp_endpoint, insecure=True  # Adjust as needed
        )
        super().__init__(otlp_exporter)
        # Common regex patterns for sensitive data (e.g., email addresses, phone numbers, etc.)
        self.email_regex = re.compile(r'[\w\.-]+@[\w\.-]+\.\w+')
        self.sensitive_keys = {'email', 'phone', 'ssn', 'credit_card', 'api_key', 'password', 'token', 'authorization', 'secret'}

    def on_start(self, span: Span, parent_context: Context) -> None:
        """Attributes are mutable here, so we can clean them up early."""
        print(f"--- Starting span: {span.name}---")
        pass

    def on_end(self, span: ReadableSpan) -> None:
        """Cleans attributes before they hit the exporter."""
        print(f"--- Ending span: {span.name}---")
        print(f" Trace ID: {span.context.trace_id}, Span ID: {span.context.span_id}")
        print(f" Duration: {max(0, (span.end_time - span.start_time) / 1e9)} seconds")
        print(f" Attributes before masking: {span.attributes}")

        for key, value in list(span.attributes.items()):
            # Mask sensitive keys
            # 1. Check if the attribute KEY is sensitive (e.g., 'email', 'phone', etc.)
            if any(sensitive_key in key.lower() for sensitive_key in self.sensitive_keys):
                span._attributes[key] = "[REDACTED]"
                continue
            
            # 2. Check if the attribute VALUE matches known PII patterns (e.g., email addresses)
            if isinstance(value, str) and self.email_regex.search(value):
                masked_value = self.email_regex.sub("[REDACTED_EMAIL]", value)
                span._attributes[key] = masked_value

        super().on_end(span)