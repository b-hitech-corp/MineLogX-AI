# Amazon Bedrock Guardrail — parity with cloudformation/bedrock-guardrails.
# Applied at every AI touchpoint (user queries, chunks before embedding, final
# agent responses): blocks prompt attacks/jailbreaks, denies off-scope topics,
# and filters PII / mining operational identifiers. See AGENTS.md.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_bedrock_guardrail" "this" {
  name                      = "${var.name_prefix}-guardrail"
  description               = "MineLogX guardrail — prompt-attack, topic denial, PII filtering."
  blocked_input_messaging   = "This request cannot be processed."
  blocked_outputs_messaging = "This response was blocked by policy."
  tags                      = merge(var.tags, { Name = "${var.name_prefix}-guardrail" })

  content_policy_config {
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
    filters_config {
      type            = "HATE"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "INSULTS"
      input_strength  = "MEDIUM"
      output_strength = "MEDIUM"
    }
    filters_config {
      type            = "MISCONDUCT"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
  }

  topic_policy_config {
    topics_config {
      name       = "LegalAdvice"
      type       = "DENY"
      definition = "Requests for legal counsel or advice. The RAG agent provides regulatory information with citations, never legal advice."
    }
    topics_config {
      name       = "FinancialAdvice"
      type       = "DENY"
      definition = "Requests for investment, tax, or financial advice."
    }
    topics_config {
      name       = "MedicalAdvice"
      type       = "DENY"
      definition = "Requests for medical diagnosis or treatment advice."
    }
  }

  sensitive_information_policy_config {
    pii_entities_config {
      type   = "EMAIL"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "PHONE"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "ADDRESS"
      action = "ANONYMIZE"
    }
    # Mining operational identifiers — tune patterns to real formats.
    regexes_config {
      name    = "EmployeeId"
      pattern = "\\bEMP-\\d{4,6}\\b"
      action  = "ANONYMIZE"
    }
    regexes_config {
      name    = "ContractId"
      pattern = "\\bCT-\\d{4,}\\b"
      action  = "ANONYMIZE"
    }
    regexes_config {
      name    = "SiteId"
      pattern = "\\bSITE-[A-Z0-9]{2,}\\b"
      action  = "ANONYMIZE"
    }
  }
}

resource "aws_bedrock_guardrail_version" "this" {
  guardrail_arn = aws_bedrock_guardrail.this.guardrail_arn
  description   = "Published version"
}

output "guardrail_id" {
  value = aws_bedrock_guardrail.this.guardrail_id
}

output "guardrail_arn" {
  value = aws_bedrock_guardrail.this.guardrail_arn
}

output "guardrail_version" {
  value = aws_bedrock_guardrail_version.this.version
}
