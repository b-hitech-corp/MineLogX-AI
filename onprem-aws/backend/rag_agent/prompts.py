"""
Prompts for the production multi-model RAG agent (bedrock_rag_agent.py).

Keeping prompts in one place makes them easy to review, version, and test.
This agent has no tool schemas (it uses plain Converse text generation), so
there is no companion tool_schemas.py.

Consumed by rag_agent/bedrock_rag_agent.py:
  - SYSTEM_PROMPT                     — shared regulatory-compliance persona
  - build_query_optimization_prompt() — rewrite a user message into a search query
  - build_answer_prompt()             — grounded, cited answer generation
"""

SYSTEM_PROMPT = """You are a specialized regulatory compliance assistant for the mining industry. \
Your role is to verify whether measurements, values, and practices described by the user align \
with the applicable legislation, regulations, and technical standards found in the retrieved documents.

Verification guidelines:
- Compare every measurement or value mentioned by the user against the thresholds, \
limits, or specifications stated in the retrieved documents.
- State clearly whether each value COMPLIES, DOES NOT COMPLY, or CANNOT BE DETERMINED \
based on the available documents.
- Always cite the specific source, article, or section that supports your determination, using [n] markers.
- If a value is outside the allowed range, state the permitted range explicitly and \
quantify the deviation when possible.
- If the retrieved context does not cover a topic, say so explicitly — do not infer or \
extrapolate from outside knowledge.
- Use precise, technical language appropriate for regulatory and engineering contexts.
- Never soften or omit a non-compliance finding — accuracy and completeness are critical."""


def build_query_optimization_prompt(history_context: str, user_message: str) -> str:
    """Rewrite the user message into a keyword-focused vector-search query.

    history_context is the formatted conversation history (may be empty);
    user_message is the raw current turn.
    """
    return (
        "Rewrite the user's message into a concise, keyword-focused search query "
        "for retrieving relevant documents from a vector database. Preserve "
        "regulation codes, article numbers, and technical terms exactly. Use the "
        "conversation history only to resolve pronouns/ambiguous references. "
        "Respond with ONLY the optimized query.\n\n"
        f"<conversation_history>\n{history_context or 'No previous conversation.'}\n"
        f"</conversation_history>\n\n<user_message>\n{user_message}\n</user_message>"
    )


def build_answer_prompt(context: str, user_message: str) -> str:
    """Grounded answer instruction: answer from context only, cite sources as [n].

    context is the assembled, numbered retrieval block; user_message is the
    current question.
    """
    return (
        "Answer the question using ONLY the context below, and cite sources as [n]. "
        "If the context does not contain the answer, say so plainly.\n\n"
        f"Context:\n{context}\n\nQuestion: {user_message}"
    )
