"""
Tool schemas for the PDF Vectorization Pipeline.

Structured JSON tool definitions kept separate from prose prompts (see
prompts.py) so each file stays single-purpose. These use the Amazon Bedrock
**Converse** tool format (toolSpec / inputSchema.json), not the Anthropic
Messages format — the PDF tools call bedrock_client.converse().

Consumed by:
  - tools/pdf_claude_extractor.py — EXTRACT_TOOL  (emit_sections)
  - tools/pdf_classifier.py        — CLASSIFY_TOOL (classify_document)
"""

# Forced-tool-use schema for reliable structured output. Returning sections through a tool
# call (rather than a JSON string in free text) makes Bedrock serialize them as an
# already-parsed object — eliminating the markdown-fence / unescaped-quote parse failures
# that legal text triggers (e.g. bodies containing 'the ("Act")' or '[Chapter 21:05]').
EXTRACT_TOOL = {
    "toolSpec": {
        "name": "emit_sections",
        "description": "Return every distinct section extracted from the regulatory PDF, in document order.",
        "inputSchema": {
            "json": {
                "type": "object",
                "required": ["sections"],
                "properties": {
                    "sections": {
                        "type": "array",
                        "description": "One entry per section/heading in the document, in reading order.",
                        "items": {
                            "type": "object",
                            "required": ["title", "body", "page_start", "page_end"],
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "description": "Exact section heading as it appears in the document.",
                                },
                                "body": {
                                    "type": "string",
                                    "description": "Complete verbatim section text incl. sub-clauses, numbered lists, pipe-delimited tables, and schedules.",
                                },
                                "page_start": {
                                    "type": "integer",
                                    "description": "1-based page where the section begins.",
                                },
                                "page_end": {
                                    "type": "integer",
                                    "description": "1-based inclusive page where the section ends.",
                                },
                            },
                        },
                    }
                },
            }
        },
    }
}

CLASSIFY_TOOL = {
    "name": "classify_document",
    "description": (
        "Classify a regulatory PDF document based on its first page text. "
        "Use 'high' for dense legal/regulatory documents with numbered clauses, "
        "cross-references, definitions sections, or legislative language. "
        "Use 'low' or 'medium' for standard forms, templates, or lightly formatted documents."
    ),
    "inputSchema": {
        # Converse requires the schema wrapped under a "json" key (ToolInputSchema union).
        "json": {
            "type": "object",
            "properties": {
                "complexity": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": (
                        "high → dense regulatory/legal structure (mining acts, safety codes, "
                        "environmental regulations with numbered clauses); "
                        "medium → moderately structured document; "
                        "low → simple form, template, or administrative document"
                    ),
                },
                "confidence": {
                    "type": "number",
                    "description": "Confidence in this classification, 0.0–1.0",
                },
                "reasoning": {
                    "type": "string",
                    "description": "One sentence explaining the classification decision",
                },
            },
            "required": ["complexity", "confidence", "reasoning"],
        },
    },
}
